#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import shutil
import subprocess
import zipfile
import tempfile
import time
import re
import json
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 翻譯依賴
from bs4 import BeautifulSoup, NavigableString, Comment, FeatureNotFound
from openai import OpenAI


# ===================== 共同工具（Calibre / ebook-convert） =====================

CALIBRE_DEFAULT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"


def resolve_ebook_convert() -> str:
    path = shutil.which("ebook-convert")
    if path:
        return path
    if Path(CALIBRE_DEFAULT).exists():
        return CALIBRE_DEFAULT
    print("[ERROR] 找不到指令 `ebook-convert`。請安裝 Calibre 或把它加入 PATH。", file=sys.stderr)
    sys.exit(2)


def needs_convert(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return True
    if dst.stat().st_size == 0:
        return True
    return src.stat().st_mtime > dst.stat().st_mtime


def convert_one(src: Path) -> tuple[Path, bool, str]:
    dst = src.with_suffix(".epub")
    if not needs_convert(src, dst):
        return (src, False, "skip (already up-to-date)")
    cmd = [
        resolve_ebook_convert(),
        str(src),
        str(dst),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return (src, True, "ok")
        else:
            return (src, True, f"failed (code={res.returncode})\nSTDERR:\n{res.stderr.strip()}")
    except Exception as e:
        return (src, True, f"exception: {e}")


# ===================== convert 子指令（批量轉 EPUB） =====================


def cmd_convert(args: argparse.Namespace) -> None:
    base_dir = Path(args.base_dir).expanduser().resolve()
    if not base_dir.exists():
        print(f"[ERROR] 路徑不存在：{base_dir}", file=sys.stderr)
        sys.exit(1)

    exts = set()
    for e in (args.exts or ".mobi,.azw3").split(","):
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        exts.add(e)

    targets: List[Path] = []
    for p in base_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            targets.append(p)

    if not targets:
        print("沒有找到任何待轉檔的來源檔案。")
        return

    print(f"找到 {len(targets)} 個檔案，開始轉檔…")
    success, skipped, failed = 0, 0, 0
    details_failed = []

    max_workers = max(1, int(args.workers))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(convert_one, src): src for src in targets}
        for fut in as_completed(futures):
            src, attempted, msg = fut.result()
            try:
                rel = src.relative_to(base_dir)
            except ValueError:
                rel = src.name
            if "ok" in msg:
                success += 1
                print(f"✅ {rel} -> ok")
            elif msg.startswith("skip"):
                skipped += 1
                print(f"⏭️  {rel} -> {msg}")
            else:
                failed += 1
                details_failed.append((rel, msg))
                print(f"❌ {rel} -> {msg}")

    print("\n=== 結算 ===")
    print(f"成功：{success}")
    print(f"跳過：{skipped}")
    print(f"失敗：{failed}")
    if failed:
        print("\n失敗清單：")
        for rel, msg in details_failed:
            print(f"- {rel}\n  {msg}")


# ===================== translate 子指令（保留排版翻譯） =====================

SKIP_TAGS = {"script", "style", "head", "svg", "math"}
TRANSLATE_ALT = True


def unzip_epub(epub_path: Path, workdir: Path):
    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(workdir)


def rezip_epub(src_dir: Path, out_epub: Path):
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


def parse_with_best_parser(text: str) -> BeautifulSoup:
    for parser in ("lxml-xml", "xml", "html.parser"):
        try:
            return BeautifulSoup(text, parser)
        except FeatureNotFound:
            continue
    raise RuntimeError("沒有可用的 BeautifulSoup 解析器（請安裝 lxml 或 html5lib）。")


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
        "EXACTLY as in the source (no translation, no transliteration)."
    )


_ckpt_lock = threading.Lock()


def _translate_one_batch(
    batch_index: int,
    batch: List[str],
    client: OpenAI,
    model: str,
    sys_prompt: str,
    max_retries: int,
    checkpoint_path: Optional[Path],
) -> Tuple[int, List[str]]:
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
                        w.write(
                            json.dumps({"batch": batch_index, "ok": True, "parts": parts}, ensure_ascii=False)
                            + "\n"
                        )
            return batch_index, parts

        except Exception as e:
            wait = min(2 ** attempt, 30) + (0.2 * attempt)
            sys.stderr.write(f"[WARN] 批次 {batch_index} 第 {attempt} 次失敗：{e}；{wait:.1f}s 後重試…\n")
            time.sleep(wait)

    if checkpoint_path:
        with _ckpt_lock:
            with checkpoint_path.open("a", encoding="utf-8") as w:
                w.write(json.dumps({"batch": batch_index, "ok": False, "error": "max retries reached"}, ensure_ascii=False) + "\n")
    raise RuntimeError(f"批次 {batch_index} 重試仍失敗")


def openai_self_test(client: OpenAI, model: str, target_lang: str, out_txt: Path):
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
    sys_prompt = make_system_prompt(target_lang)
    batches = batch_texts(items, max_chars_per_call)

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
    outputs_by_batch: Dict[int, List[str]] = dict(done)

    if pending_idx:
        max_workers = max(1, min(int(max_workers), 25))

        def make_client() -> OpenAI:
            return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {}
            for i in pending_idx:
                c = make_client()
                fut = ex.submit(
                    _translate_one_batch, i, batches[i - 1], c, model, sys_prompt, max_retries, checkpoint_path
                )
                futures[fut] = i

            for fut in as_completed(futures):
                i, parts = fut.result()
                outputs_by_batch[i] = parts
                print(f"[INFO] 批次 {i} 完成（{len(parts)} 段）")

    outputs: List[str] = []
    for i in range(1, len(batches) + 1):
        parts = outputs_by_batch.get(i)
        if not parts:
            raise RuntimeError(f"缺少批次 {i} 的結果（可能是中斷且無 checkpoint）。")
        outputs.extend(parts)
    return outputs


def cmd_translate(args: argparse.Namespace) -> None:
    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists():
        print(f"[ERROR] 找不到檔案：{in_path}", file=sys.stderr)
        sys.exit(1)

    base = in_path.with_suffix(".epub") if in_path.suffix.lower() == ".mobi" else in_path
    out_epub = base.with_name(f"{base.stem}.{args.target}.translated.epub")

    if in_path.suffix.lower() == ".mobi" and not args.skip_convert:
        tmp_epub = in_path.with_suffix(".tmp.convert.epub")
        cmd = [resolve_ebook_convert(), str(in_path), str(tmp_epub), "--keep-ligatures", "--no-default-epub-cover", "--embed-all-fonts", "--pretty-print"]
        print(f"[INFO] 轉檔：{' '.join(cmd)}")
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print("[ERROR] ebook-convert 執行失敗。", file=sys.stderr)
            sys.exit(2)
        if not tmp_epub.exists() or tmp_epub.stat().st_size == 0:
            print("[ERROR] 轉檔後 EPUB 不存在或為空（可能是 DRM 或原檔損壞）。", file=sys.stderr)
            sys.exit(2)
        src_epub = tmp_epub
    else:
        if in_path.suffix.lower() != ".epub":
            print("[ERROR] 略過轉檔僅適用於 EPUB 輸入；請移除 --skip-convert 或提供 .epub。", file=sys.stderr)
            sys.exit(1)
        src_epub = in_path

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] 尚未設定 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(2)
    client = OpenAI(api_key=api_key)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        unzip_epub(src_epub, tmp)

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
                if args.preview_limit and total_nodes >= args.preview_limit:
                    break

            if not args.preview_limit or total_nodes < args.preview_limit:
                for img, alt in extract_alt_texts(soup):
                    alt_refs.append((img, alt))
                    texts.append(alt)
                    total_nodes += 1
                    if args.preview_limit and total_nodes >= args.preview_limit:
                        break

            if args.preview_limit and total_nodes >= args.preview_limit:
                break

        selftest_path = in_path.with_suffix(".api-selftest.txt")
        openai_self_test(client, args.model, args.target, selftest_path)

        if not texts:
            print("[INFO] 沒有可翻譯文字（或已達 preview 限制）。")
            out_path = out_epub if args.preview_limit == 0 else out_epub.with_name(
                out_epub.name.replace(".translated.", ".preview.")
            )
            rezip_epub(tmp, out_path)
            print(f"[DONE] 已輸出（未改動）：{out_path}")
            return

        print(f"[INFO] 將翻譯 {len(texts)} 個原子字串（文字節點 + alt）。")

        ckpt = (in_path.parent / "translated_batches.jsonl")
        translations = translate_text_list_concurrent(
            client=client,
            model=args.model,
            target_lang=args.target,
            items=texts,
            max_chars_per_call=args.max_chars_per_call,
            max_workers=args.max_workers,
            max_retries=7,
            checkpoint_path=ckpt,
        )
        assert len(translations) == len(texts)

        limit = len(texts) if args.preview_limit == 0 else args.preview_limit
        t_i = 0
        replaced_pairs: List[Tuple[str, str]] = []
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

        for f, soup in soup_map.items():
            f.write_text(str(soup), encoding="utf-8")

        if args.preview_limit > 0:
            out_preview = out_epub.with_name(out_epub.name.replace(".translated.", ".preview."))
            rezip_epub(tmp, out_preview)
            tsv_path = out_preview.with_suffix(".tsv")
            with tsv_path.open("w", encoding="utf-8") as w:
                for src, tgt in replaced_pairs:
                    w.write(src.replace("\n", " ") + "\t" + tgt.replace("\n", " ") + "\n")
            print(f"[DONE] 預覽輸出：{out_preview}")
            print(f"[DONE] 對照表：{tsv_path}")
        else:
            rezip_epub(tmp, out_epub)
            print(f"[DONE] 全量輸出：{out_epub}")


# ===================== 主 CLI =====================


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Ebook convert and translate (preserve layout/images).")
    sub = ap.add_subparsers(dest="command", required=True)

    ap_c = sub.add_parser("convert", help="批量將 .mobi/.azw3 轉為 .epub")
    ap_c.add_argument("--base-dir", required=True, help="掃描根目錄")
    ap_c.add_argument("--exts", default=".mobi,.azw3", help="來源副檔名，逗號分隔（含或不含點）")
    ap_c.add_argument("--workers", type=int, default=8, help="併發度（預設 8）")
    ap_c.set_defaults(func=cmd_convert)

    ap_t = sub.add_parser("translate", help="翻譯 EPUB/MOBI，可保留原始排版與圖片")
    ap_t.add_argument("input", help="輸入檔（.mobi 或 .epub）")
    ap_t.add_argument("--target", default="zh-TW", help="目標語言，預設 zh-TW")
    ap_t.add_argument("--model", default="gpt-5", help="OpenAI 模型，例如 gpt-5 或 gpt-5-mini")
    ap_t.add_argument("--skip-convert", action="store_true", help="輸入已是 EPUB，跳過 MOBI→EPUB 轉檔")
    ap_t.add_argument("--max-chars-per-call", type=int, default=3500, help="每次 API 呼叫最大字元數")
    ap_t.add_argument("--preview-limit", type=int, default=0, help="僅翻譯前 N 個原子字串（試跑/抽樣）")
    ap_t.add_argument("--max-workers", type=int, default=10, help="同時並發批次數（1~25）")
    ap_t.set_defaults(func=cmd_translate)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    main()

