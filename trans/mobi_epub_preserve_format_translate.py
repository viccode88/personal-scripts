#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 .mobi 轉為 .epub，僅翻譯 EPUB 裡的 XHTML/XML 文字節點，保持原始排版、連結、圖片、CSS 不動。
輸出：
  - 全量：<原檔名>.<lang>.translated.epub
  - 預覽：<原檔名>.<lang>.preview.epub + <原檔名>.<lang>.preview.tsv
  - Self-test：<原檔名>.api-selftest.txt

新增：
  - 人名/專有名詞統一保留英文原名（提示詞規則）
  - 多線程並發（--max-workers，最多 25）

建議先預覽：
  python mobi_epub_preserve_format_translate.py input.mobi --target zh-TW --model gpt-5 --preview-limit 150 --max-workers 25
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from bs4 import BeautifulSoup, NavigableString, Comment, FeatureNotFound
import subprocess

# ---- OpenAI Responses API ----
from openai import OpenAI

# ------------------ EPUB / 轉檔 ------------------

CALIBRE_DEFAULT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"

def resolve_ebook_convert() -> str:
    path = shutil.which("ebook-convert")
    if path:
        return path
    if Path(CALIBRE_DEFAULT).exists():
        return CALIBRE_DEFAULT
    print("[ERROR] 找不到指令 `ebook-convert`。請安裝 Calibre 或把它加入 PATH。", file=sys.stderr)
    sys.exit(2)

def mobi_to_epub(in_path: Path, out_epub: Path):
    cmd = [
        resolve_ebook_convert(),
        str(in_path),
        str(out_epub),
        "--keep-ligatures",
        "--no-default-epub-cover",
        "--embed-all-fonts",
        "--pretty-print",
    ]
    print(f"[INFO] 轉檔：{' '.join(cmd)}")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("ebook-convert 執行失敗。")
    if not out_epub.exists() or out_epub.stat().st_size == 0:
        raise RuntimeError("轉檔後 EPUB 不存在或為空（可能是 DRM 或原檔損壞）。")

def unzip_epub(epub_path: Path, workdir: Path):
    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(workdir)

def rezip_epub(src_dir: Path, out_epub: Path):
    # EPUB 慣例：mimetype 需置頂且不壓縮
    with zipfile.ZipFile(out_epub, "w") as zf:
        mimetype_file = src_dir / "mimetype"
        if mimetype_file.exists():
            zf.writestr("mimetype", mimetype_file.read_text(encoding="utf-8"), compress_type=zipfile.ZIP_STORED)
        for root, _, files in os.walk(src_dir):
            for fn in files:
                p = Path(root) / fn
                rel = p.relative_to(src_dir)
                if str(rel) == "mimetype":
                    continue
                zf.write(p, str(rel), compress_type=zipfile.ZIP_DEFLATED)

# ------------------ HTML/XML 文字抽取 / 回填 ------------------

SKIP_TAGS = {"script", "style", "head", "svg", "math"}
TRANSLATE_ALT = True  # 是否翻譯 <img alt="...">

def iter_text_nodes(soup: BeautifulSoup) -> Iterable[Tuple[NavigableString, str]]:
    for node in soup.descendants:
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            parent = node.parent
            if parent and parent.name and parent.name.lower() in SKIP_TAGS:
                continue
            text = str(node)
            if not text or not re.search(r"\S", text):
                continue
            yield node, text

def extract_alt_texts(soup: BeautifulSoup) -> List[Tuple[Any, str]]:
    items: List[Tuple[Any, str]] = []
    if not TRANSLATE_ALT:
        return items
    for img in soup.find_all("img"):
        alt = img.get("alt")
        if alt and re.search(r"\S", alt):
            items.append((img, alt))
    return items

def batch_texts(texts: List[str], max_chars: int) -> List[List[str]]:
    batches, buf, size = [], [], 0
    for t in texts:
        t_len = len(t)
        if t_len > max_chars:
            start = 0
            while start < t_len:
                end = min(start + max_chars, t_len)
                batches.append([t[start:end]])
                start = end
            buf, size = [], 0
            continue
        if size + t_len + 1 > max_chars and buf:
            batches.append(buf)
            buf, size = [t], t_len
        else:
            buf.append(t)
            size += t_len + 1
    if buf:
        batches.append(buf)
    return batches

# ------------------ OpenAI 翻譯 / 自我測試 ------------------

def make_system_prompt(target_lang: str) -> str:
    return (
        "You are a professional book translator. "
        f"Translate the user's text into {target_lang}.\n"
        "Rules:\n"
        "1) Preserve meaning, tone, and literary style.\n"
        "2) Do NOT add commentary.\n"
        "3) Keep inline punctuation and spacing natural for the target language.\n"
        "4) Return translations in the SAME order, one per segment, aligned with inputs.\n"
        "5) Do not translate HTML/XML tags or entities; only translate human-readable text.\n"
        "6) CRITICAL: Preserve all ENGLISH proper nouns (people names, places, organizations, product/series titles) "
        "EXACTLY as in the source (no translation, no transliteration). Examples: 'Nora Sutherlin', 'New York', "
        "'Harlequin Enterprises Limited'."
    )

def openai_self_test(client: OpenAI, model: str, target_lang: str, out_txt: Path):
    """小小煙霧測試：確認 API 可用且能翻出內容。"""
    sys_prompt = "You are a helpful translator."
    user_prompt = f"Translate the following short line into {target_lang}:\nThis is a translation health check for EPUB batch translation."
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text={"format": {"type": "text"}, "verbosity": "medium"},
        reasoning={"effort": "medium", "summary": "auto"},
        tools=[],
        store=True,
    )
    text = (resp.output_text or "").strip()
    if not text:
        raise RuntimeError("Self-test 失敗：API 回傳空白。")
    out_txt.write_text(text + "\n", encoding="utf-8")
    print(f"[SELF-TEST] OK → {out_txt.name}: {text[:80]}{'...' if len(text)>80 else ''}")

# ---- 併發批次翻譯（多線程） ----

_ckpt_lock = threading.Lock()

def _translate_one_batch(
    batch_index: int,
    batch: List[str],
    client: OpenAI,
    model: str,
    sys_prompt: str,
    max_retries: int,
    sleep_base: float,
    checkpoint_path: Optional[Path],
) -> Tuple[int, List[str]]:
    """執行單一批次（含重試/退避），回傳 (batch_index, parts)。"""
    inp = "\n---\n".join(batch)
    user_prompt = (
        "Translate each segment below, preserving order. "
        "Segments are separated by a line with only '---'. "
        "Output MUST contain the same number of segments, also separated by single lines '---', "
        "with no extra text.\n\n" + inp
    )

    for attempt in range(1, max_retries + 1):
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={"format": {"type": "text"}, "verbosity": "medium"},
                reasoning={"effort": "medium", "summary": "auto"},
                tools=[],
                store=True,
            )
            out_text = (resp.output_text or "").strip()
            if not out_text:
                raise RuntimeError("空白輸出")
            parts = [p.strip() for p in out_text.split("\n---\n")]
            if len(parts) != len(batch):
                parts_alt = [p.strip() for p in out_text.splitlines() if p.strip()]
                if len(parts_alt) == len(batch):
                    parts = parts_alt
                else:
                    raise RuntimeError(f"輸出段數不符：預期 {len(batch)}，實得 {len(parts)}。")

            if checkpoint_path:
                with _ckpt_lock:
                    with checkpoint_path.open("a", encoding="utf-8") as w:
                        w.write(json.dumps({"batch": batch_index, "ok": True, "parts": parts}, ensure_ascii=False) + "\n")
            return batch_index, parts

        except Exception as e:
            wait = min(2 ** attempt, 30) + (0.2 * attempt)
            sys.stderr.write(f"[WARN] 批次 {batch_index} 第 {attempt} 次失敗：{e}；{wait:.1f}s 後重試…\n")
            time.sleep(wait)

    # 到這裡代表失敗
    if checkpoint_path:
        with _ckpt_lock:
            with checkpoint_path.open("a", encoding="utf-8") as w:
                w.write(json.dumps({"batch": batch_index, "ok": False, "error": "max retries reached"}, ensure_ascii=False) + "\n")
    raise RuntimeError(f"批次 {batch_index} 重試仍失敗")

def translate_text_list_concurrent(
    client: OpenAI,
    model: str,
    target_lang: str,
    items: List[str],
    *,
    max_chars_per_call: int = 3500,
    max_workers: int = 10,
    max_retries: int = 7,
    checkpoint_path: Optional[Path] = None,
) -> List[str]:
    """多線程並發翻譯，批次並行但輸出順序與輸入對齊。"""
    sys_prompt = make_system_prompt(target_lang)
    batches = batch_texts(items, max_chars_per_call)

    # 讀取既有 checkpoint（允許任意批次完成）
    done: Dict[int, List[str]] = {}
    if checkpoint_path and checkpoint_path.exists():
        for line in checkpoint_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("ok") and "batch" in rec and rec.get("parts"):
                    done[int(rec["batch"])] = rec["parts"]
            except Exception:
                continue

    pending_idx = [i for i in range(1, len(batches) + 1) if i not in done]
    outputs_by_batch: Dict[int, List[str]] = dict(done)  # 先塞已完成

    if pending_idx:
        max_workers = max(1, min(int(max_workers), 25))  # 上限 25
        print(f"[INFO] 併發執行：{len(pending_idx)} 批待處理，max_workers={max_workers}")

        # 每個線程用自己的 client（更保險）
        def make_client() -> OpenAI:
            return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {}
            for i in pending_idx:
                c = make_client()
                fut = ex.submit(
                    _translate_one_batch, i, batches[i-1], c, model, sys_prompt,
                    max_retries, sleep_base=1.0, checkpoint_path=checkpoint_path
                )
                futures[fut] = i

            for fut in as_completed(futures):
                i, parts = fut.result()
                outputs_by_batch[i] = parts
                print(f"[INFO] 批次 {i} 完成（{len(parts)} 段）")

    # 重建輸出（照批次順序串回）
    outputs: List[str] = []
    for i in range(1, len(batches) + 1):
        parts = outputs_by_batch.get(i)
        if not parts:
            raise RuntimeError(f"缺少批次 {i} 的結果（可能是中斷且無 checkpoint）。")
        outputs.extend(parts)
    return outputs

# ------------------ 主流程 ------------------

def parse_with_best_parser(text: str) -> BeautifulSoup:
    """優先用 lxml-xml；沒裝就退回 'xml' 或 html.parser。"""
    for parser in ("lxml-xml", "xml", "html.parser"):
        try:
            return BeautifulSoup(text, parser)
        except FeatureNotFound:
            continue
    raise RuntimeError("沒有可用的 BeautifulSoup 解析器（請安裝 lxml 或 html5lib）。")

def translate_epub_texts(
    epub_in: Path,
    epub_out_full: Path,
    target_lang: str,
    model: str,
    *,
    max_chars_per_call: int = 3500,
    preview_limit: int = 0,
    max_workers: int = 10,
):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("尚未設定 OPENAI_API_KEY")
    client = OpenAI(api_key=api_key)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        unzip_epub(epub_in, tmp)

        html_files: List[Path] = []
        for ext in ("*.xhtml", "*.html", "*.htm"):
            html_files.extend(tmp.rglob(ext))

        soup_map: Dict[Path, BeautifulSoup] = {}
        node_refs: List[Tuple[Path, NavigableString]] = []
        texts: List[str] = []
        alt_refs: List[Tuple[Any, str]] = []

        total_nodes = 0
        for f in html_files:
            soup = parse_with_best_parser(f.read_text(encoding="utf-8", errors="ignore"))
            soup_map[f] = soup

            for node, text in iter_text_nodes(soup):
                node_refs.append((f, node))
                texts.append(text)
                total_nodes += 1
                if preview_limit and total_nodes >= preview_limit:
                    break

            if not preview_limit or total_nodes < preview_limit:
                for img, alt in extract_alt_texts(soup):
                    alt_refs.append((img, alt))
                    texts.append(alt)
                    total_nodes += 1
                    if preview_limit and total_nodes >= preview_limit:
                        break

            if preview_limit and total_nodes >= preview_limit:
                break

        # === Self-test：先確認 API 正常 ===
        selftest_path = epub_in.with_suffix(".api-selftest.txt")
        openai_self_test(client, model, target_lang, selftest_path)

        if not texts:
            print("[INFO] 沒有可翻譯文字（或已達 preview 限制）。")
            out_path = epub_out_full if preview_limit == 0 else epub_out_full.with_name(
                epub_out_full.name.replace(".translated.", ".preview.")
            )
            rezip_epub(tmp, out_path)
            print(f"[DONE] 已輸出（未改動）：{out_path}")
            return

        print(f"[INFO] 將翻譯 {len(texts)} 個原子字串（文字節點 + alt）。")

        ckpt = (epub_in.parent / "translated_batches.jsonl")
        translations = translate_text_list_concurrent(
            client=client,
            model=model,
            target_lang=target_lang,
            items=texts,
            max_chars_per_call=max_chars_per_call,
            max_workers=max_workers,
            max_retries=7,
            checkpoint_path=ckpt,
        )
        assert len(translations) == len(texts)

        # 回填（只回填前 preview_limit 個原子字串；若為全量則全部回填）
        limit = len(texts) if preview_limit == 0 else preview_limit
        t_i = 0
        replaced_pairs: List[Tuple[str, str]] = []  # for preview.tsv
        for f, node in node_refs:
            if t_i >= limit:
                break
            new_text = translations[t_i]
            replaced_pairs.append((str(node), new_text))
            node.replace_with(new_text)
            t_i += 1
        for img, _alt in alt_refs:
            if t_i >= limit:
                break
            new_alt = translations[t_i]
            replaced_pairs.append((_alt, new_alt))
            img["alt"] = new_alt
            t_i += 1

        # 寫回所有 soup
        for f, soup in soup_map.items():
            f.write_text(str(soup), encoding="utf-8")

        if preview_limit > 0:
            out_preview = epub_out_full.with_name(epub_out_full.name.replace(".translated.", ".preview."))
            rezip_epub(tmp, out_preview)
            # 產出 TSV 預覽（原文\t譯文）
            tsv_path = out_preview.with_suffix(".tsv")
            with tsv_path.open("w", encoding="utf-8") as w:
                for src, tgt in replaced_pairs:
                    w.write(src.replace("\n", " ") + "\t" + tgt.replace("\n", " ") + "\n")
            print(f"[DONE] 預覽輸出：{out_preview}")
            print(f"[DONE] 對照表：{tsv_path}")
        else:
            rezip_epub(tmp, epub_out_full)
            print(f"[DONE] 全量輸出：{epub_out_full}")

# ------------------ CLI ------------------

def main():
    ap = argparse.ArgumentParser(description="Translate MOBI/EPUB to target language while preserving layout/images.")
    ap.add_argument("input", type=str, help=".mobi 或 .epub 檔")
    ap.add_argument("--target", type=str, default="zh-TW", help="目標語言（預設 zh-TW）")
    ap.add_argument("--model", type=str, default="gpt-5", help="OpenAI 模型，例如 gpt-5 或 gpt-5-mini")
    ap.add_argument("--skip-convert", action="store_true", help="輸入已是 EPUB，跳過 MOBI→EPUB 轉檔")
    ap.add_argument("--max-chars-per-call", type=int, default=3500, help="每次 API 呼叫的最大字元數")
    ap.add_argument("--preview-limit", type=int, default=0, help="僅翻譯前 N 個原子字串（試跑/抽樣）")
    ap.add_argument("--max-workers", type=int, default=10, help="同時並發批次數（1~25）")
    args = ap.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists():
        print(f"[ERROR] 找不到檔案：{in_path}", file=sys.stderr)
        sys.exit(1)

    # 目標輸出檔名
    base = in_path.with_suffix(".epub") if in_path.suffix.lower() == ".mobi" else in_path
    out_epub = base.with_name(f"{base.stem}.{args.target}.translated.epub")

    # 需要的話先轉 EPUB
    if in_path.suffix.lower() == ".mobi" and not args.skip_convert:
        tmp_epub = in_path.with_suffix(".tmp.convert.epub")
        mobi_to_epub(in_path, tmp_epub)
        src_epub = tmp_epub
    else:
        if in_path.suffix.lower() not in (".epub",):
            print("[ERROR] 只支援 .mobi 或 .epub 輸入。", file=sys.stderr)
            sys.exit(1)
        src_epub = in_path

    try:
        translate_epub_texts(
            epub_in=src_epub,
            epub_out_full=out_epub,
            target_lang=args.target,
            model=args.model,
            max_chars_per_call=args.max_chars_per_call,
            preview_limit=args.preview_limit,
            max_workers=args.max_workers,
        )
    finally:
        if src_epub != in_path and src_epub.exists():
            try:
                src_epub.unlink()
            except Exception:
                pass

if __name__ == "__main__":
    main()
