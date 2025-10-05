#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

BASE_DIR = Path("/Users/icv/Documents/project/trans/trans")
EXTS = {".mobi", ".azw3"}
MAX_WORKERS = 8  # 依照 CPU 調整

def find_ebook_convert() -> str:
    # 1) 先找 PATH
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    # 2) macOS 常見安裝位置
    mac_path = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
    if Path(mac_path).exists():
        return mac_path
    print("找不到 ebook-convert。請先安裝 Calibre，或把 ebook-convert 加到 PATH。", file=sys.stderr)
    sys.exit(1)

EBOOK_CONVERT = find_ebook_convert()

def needs_convert(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return True
    # 若 EPUB 比來源舊或為 0 bytes，就重轉
    if dst.stat().st_size == 0:
        return True
    return src.stat().st_mtime > dst.stat().st_mtime

def convert_one(src: Path) -> tuple[Path, bool, str]:
    dst = src.with_suffix(".epub")
    if not needs_convert(src, dst):
        return (src, False, "skip (already up-to-date)")
    cmd = [
        EBOOK_CONVERT,
        str(src),
        str(dst),
        # 你也可加入參數微調，例如：
        # "--no-inline-toc",
        # "--embed-all-fonts",
        # "--enable-heuristics",
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return (src, True, "ok")
        else:
            return (src, True, f"failed (code={res.returncode})\nSTDERR:\n{res.stderr.strip()}")
    except Exception as e:
        return (src, True, f"exception: {e}")

def main():
    if not BASE_DIR.exists():
        print(f"路徑不存在：{BASE_DIR}", file=sys.stderr)
        sys.exit(1)

    targets: list[Path] = []
    for p in BASE_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS:
            targets.append(p)

    if not targets:
        print("沒有找到任何 .mobi 或 .azw3 檔案。")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 找到 {len(targets)} 個待檢查檔案，開始轉檔…")
    success, skipped, failed = 0, 0, 0
    details_failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(convert_one, src): src for src in targets}
        for fut in as_completed(futures):
            src, attempted, msg = fut.result()
            rel = src.relative_to(BASE_DIR)
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

if __name__ == "__main__":
    main()
