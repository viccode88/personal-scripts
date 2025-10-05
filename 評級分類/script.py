import os
import re
import sys
import shutil
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, List, Dict


# -----------------------------
# XMP 解析
# -----------------------------
_RATING_PATTERNS = [
    re.compile(r"<xmp:Rating>\s*(-?\d+)\s*</xmp:Rating>", re.IGNORECASE),
    re.compile(r"<MicrosoftPhoto:Rating>\s*(-?\d+)\s*</MicrosoftPhoto:Rating>", re.IGNORECASE),
]


def get_xmp_rating(jpeg_path: str) -> Optional[int]:
    """從 JPEG 內嵌的 XMP 區塊讀取評級，找不到或錯誤則回傳 None。"""
    try:
        with open(jpeg_path, "rb") as f:
            data = f.read()

        start = data.find(b"<x:xmpmeta")
        end = data.find(b"</x:xmpmeta")
        if start == -1 or end == -1:
            return None

        # 包含結尾標籤長度（12）
        xmp_str = data[start : end + 12].decode("utf-8", errors="ignore")

        for pat in _RATING_PATTERNS:
            m = pat.search(xmp_str)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
        return None
    except Exception:
        # 掃描階段不在此印詳細錯誤，統一由呼叫端彙整錯誤摘要
        return None


# -----------------------------
# 掃描與目的檔路徑規劃
# -----------------------------
def collect_image_files(src_root: str, extensions: Tuple[str, ...]) -> List[str]:
    files: List[str] = []
    for root, _, names in os.walk(src_root):
        for name in names:
            if name.lower().endswith(extensions):
                files.append(os.path.join(root, name))
    return files


def build_dest_path(
    src_path: str,
    src_root: str,
    dest_root: str,
    rating: int,
    group_by_rating: bool,
    preserve_structure: bool,
) -> str:
    dest_dir = dest_root
    if group_by_rating:
        dest_dir = os.path.join(dest_dir, str(rating))

    if preserve_structure:
        # 以來源根目錄為基準保留子結構
        rel_dir = os.path.relpath(os.path.dirname(src_path), src_root)
        if rel_dir == ".":
            rel_dir = ""
        dest_dir = os.path.join(dest_dir, rel_dir)

    os.makedirs(dest_dir, exist_ok=True)
    return os.path.join(dest_dir, os.path.basename(src_path))


def generate_unique_destination_path(path: str) -> str:
    """若檔名衝突，產生 `name (1).ext`、`name (2).ext`..."""
    if not os.path.exists(path):
        return path

    directory, filename = os.path.dirname(path), os.path.basename(path)
    name, ext = os.path.splitext(filename)
    index = 1
    while True:
        candidate = os.path.join(directory, f"{name} ({index}){ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def resolve_conflict(dest_path: str, on_conflict: str) -> Optional[str]:
    if not os.path.exists(dest_path):
        return dest_path

    if on_conflict == "skip":
        return None
    if on_conflict == "overwrite":
        try:
            os.remove(dest_path)
        except OSError:
            # 若刪不掉，仍嘗試覆蓋
            pass
        return dest_path
    if on_conflict == "rename":
        return generate_unique_destination_path(dest_path)

    # 未知策略，預設跳過
    return None


# -----------------------------
# 檔案處理與進度列
# -----------------------------
def print_progress(prefix: str, done: int, total: int, extra: str = "") -> None:
    percent = (done / total * 100) if total else 100.0
    msg = f"\r{prefix} {done}/{total} ({percent:5.1f}%)"
    if extra:
        msg += f" | {extra}"
    sys.stdout.write(msg)
    sys.stdout.flush()


def scan_ratings_parallel(
    files: List[str],
    min_rating: int,
    workers: int,
) -> Tuple[List[Tuple[str, int]], List[Tuple[str, str]]]:
    """
    回傳：
      - 符合評級的清單：[(檔案路徑, 評級), ...]
      - 讀取錯誤清單：[(檔案路徑, 錯誤訊息), ...]
    """
    rated: List[Tuple[str, int]] = []
    read_errors: List[Tuple[str, str]] = []

    total = len(files)
    done = 0

    def task(path: str) -> Tuple[str, Optional[int], Optional[str]]:
        try:
            rating = get_xmp_rating(path)
            return (path, rating, None)
        except Exception as e:
            return (path, None, str(e))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(task, p): p for p in files}
        for fut in as_completed(futures):
            path, rating, err = fut.result()
            done += 1
            if err is not None:
                read_errors.append((path, err))
            elif rating is not None and rating >= min_rating:
                rated.append((path, rating))
            print_progress("掃描中", done, total, extra=f"已標記 {len(rated)}")

    # 換行收尾
    sys.stdout.write("\n")
    return rated, read_errors


def transfer_file(
    src: str,
    dest: str,
    op: str,
) -> None:
    if op == "copy":
        shutil.copy2(src, dest)
    elif op == "move":
        shutil.move(src, dest)
    else:
        raise ValueError(f"未知操作：{op}")


def transfer_all(
    items: List[Tuple[str, int]],
    src_root: str,
    dest_root: str,
    op: str,
    group_by_rating: bool,
    preserve_structure: bool,
    on_conflict: str,
) -> Tuple[int, List[Tuple[str, str]]]:
    total = len(items)
    done = 0
    success = 0
    transfer_errors: List[Tuple[str, str]] = []

    for src, rating in items:
        try:
            dest_path = build_dest_path(
                src_path=src,
                src_root=src_root,
                dest_root=dest_root,
                rating=rating,
                group_by_rating=group_by_rating,
                preserve_structure=preserve_structure,
            )

            dest_path_final = resolve_conflict(dest_path, on_conflict=on_conflict)
            if dest_path_final is None:
                # skip
                pass
            else:
                transfer_file(src, dest_path_final, op=op)
                success += 1
        except Exception as e:
            transfer_errors.append((src, str(e)))
        finally:
            done += 1
            print_progress("傳輸中", done, total, extra=f"已完成 {success}")

    sys.stdout.write("\n")
    return success, transfer_errors


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="依 XMP 評級篩選 JPG/JPEG，並依策略複製/搬移到目標資料夾",
    )
    parser.add_argument("--src", "-s", required=True, help="來源根目錄")
    parser.add_argument(
        "--dest", "-d", default=os.path.join(os.getcwd(), "rated_images"), help="輸出根目錄"
    )
    parser.add_argument("--min-rating", type=int, default=1, help="最低評級（含）")
    parser.add_argument(
        "--op", choices=["copy", "move"], default="copy", help="操作：copy 或 move"
    )

    # 分類與結構
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--group-by-rating",
        dest="group_by_rating",
        action="store_true",
        help="依評級分子資料夾（預設啟用）",
    )
    group.add_argument(
        "--no-group-by-rating",
        dest="group_by_rating",
        action="store_false",
        help="不依評級分子資料夾，全部放同一層",
    )
    parser.set_defaults(group_by_rating=True)

    parser.add_argument(
        "--preserve-structure",
        action="store_true",
        help="保留來源目錄結構至輸出目錄",
    )

    parser.add_argument(
        "--on-conflict",
        choices=["skip", "overwrite", "rename"],
        default="rename",
        help="同名檔案衝突策略（預設 rename）",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="掃描與解析評級的執行緒數（預設 8）",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    src_root = os.path.abspath(args.src)
    dest_root = os.path.abspath(args.dest)

    if not os.path.isdir(src_root):
        print(f"❌ 來源資料夾不存在：{src_root}")
        sys.exit(1)

    os.makedirs(dest_root, exist_ok=True)

    # 1) 收集檔案
    extensions = (".jpg", ".jpeg")
    files = collect_image_files(src_root, extensions=extensions)
    print(f"找到 {len(files)} 個候選影像（{', '.join(extensions)}）")

    # 2) 多執行緒掃描與解析評級
    rated, read_errors = scan_ratings_parallel(
        files=files, min_rating=args.min_rating, workers=args.workers
    )

    if not rated:
        print("未找到符合評級的影像，結束。")
        if read_errors:
            print(f"讀取錯誤：{len(read_errors)}")
        return

    # 3) 傳輸（copy/move）
    success, transfer_errors = transfer_all(
        items=rated,
        src_root=src_root,
        dest_root=dest_root,
        op=args.op,
        group_by_rating=args.group_by_rating,
        preserve_structure=args.preserve_structure,
        on_conflict=args.on_conflict,
    )

    # 4) 摘要
    print("\n=== 摘要 ===")
    print(f"來源：{src_root}")
    print(f"輸出：{dest_root}")
    print(f"候選檔：{len(files)}")
    print(f"符合評級：{len(rated)} (min_rating={args.min_rating})")
    print(f"成功{ '複製' if args.op == 'copy' else '移動' }：{success}")

    # 各評級數量
    counts: Dict[int, int] = {}
    for _, r in rated:
        counts[r] = counts.get(r, 0) + 1
    if counts:
        by_rating = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
        print(f"各評級數量：{by_rating}")

    # 錯誤摘要
    if read_errors or transfer_errors:
        print("\n=== 錯誤摘要 ===")
        if read_errors:
            print(f"讀取/解析評級錯誤：{len(read_errors)}")
        if transfer_errors:
            print(f"傳輸錯誤：{len(transfer_errors)}")

        # 顯示前幾筆錯誤，避免洗版
        MAX_SHOW = 10
        if read_errors:
            print("\n-- 讀取錯誤(前 10) --")
            for path, err in read_errors[:MAX_SHOW]:
                print(f"{path} | {err}")
        if transfer_errors:
            print("\n-- 傳輸錯誤(前 10) --")
            for path, err in transfer_errors[:MAX_SHOW]:
                print(f"{path} | {err}")


if __name__ == "__main__":
    main()
