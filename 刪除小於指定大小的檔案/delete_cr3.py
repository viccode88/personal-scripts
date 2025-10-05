#!/usr/bin/env python3
"""
delete_cr3.py

在 /Volumes/vic_big 內遞迴尋找 .CR3 檔並刪除。
預設只列出將被刪除的檔案 (dry-run)；若要真的刪除，執行時加上 --delete。

用法：
    python delete_cr3.py           # 先試跑，不刪檔
    python delete_cr3.py --delete  # 確定後，真正刪除
"""

from pathlib import Path
import argparse
import sys
import os

ROOT = Path("/Volumes/vic_big")

def find_cr3_files(root: Path):
    """遞迴產生所有 .CR3 / .cr3 檔案路徑。"""
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".cr3":
            yield p

def main():
    parser = argparse.ArgumentParser(description="Delete .CR3 files under /Volumes/vic_big")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="真的刪除檔案（預設只顯示將被刪除的檔案）",
    )
    args = parser.parse_args()

    if not ROOT.exists():
        print(f"⚠️  找不到目錄：{ROOT}", file=sys.stderr)
        sys.exit(1)

    files = list(find_cr3_files(ROOT))

    if not files:
        print("找不到任何 .CR3 檔案。")
        return

    if args.delete:
        # 刪檔前再次提醒
        print(f"⚠️  即將刪除 {len(files)} 個 .CR3 檔案，這個動作無法復原！")
        confirm = input("請輸入 'yes' 以繼續： ").strip().lower()
        if confirm != "yes":
            print("已取消。")
            return

        for f in files:
            try:
                os.remove(f)
                print(f"🗑️  Deleted: {f}")
            except Exception as e:
                print(f"❌ 無法刪除 {f}: {e}", file=sys.stderr)
        print("✅ 完成。")
    else:
        print(f"💡 試跑模式：共找到 {len(files)} 個 .CR3 檔案，以下是清單（未刪除）")
        for f in files:
            print(f)
        print("\n若要真的刪除，請加上 --delete")

if __name__ == "__main__":
    main()
