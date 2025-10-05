#!/usr/bin/env python3
"""
move_pre_cutoff_bigfiles.py

從來源根目錄中尋找第一層名稱為 YYYY-MM-DD 的資料夾，
將「資料夾日期 早於 指定日期」且「檔案大小 大於 指定大小(KB)」的檔案搬移到目的地。

支援：
- 參數化截止日期與最小大小 (KB)
- 可選副檔名過濾（逗號分隔）
- 乾跑模式 (--dry-run)

可選：
- 保留來源子目錄結構 (--preserve-structure)
- 同名檔案衝突策略 (--on-conflict skip/overwrite/rename，預設 rename)

注意：目的地為扁平化存放（不保留來源子目錄結構）。如發生同名，會在檔名後加 _1, _2, ...。
"""

import argparse
import datetime as dt
import itertools
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple, List


date_dir_re = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$")


def iter_date_dirs(root: Path) -> Iterable[Tuple[Path, dt.date]]:
    """列出符合 YYYY-MM-DD 的第一層資料夾，回傳 (Path, date)。"""
    for p in root.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            m = date_dir_re.match(p.name)
            if m:
                y, mth, d = map(int, (m.group("y"), m.group("m"), m.group("d")))
                yield p, dt.date(y, mth, d)


def generate_unique_path(dst: Path) -> Path:
    """若目的檔已存在，回傳帶序號的新路徑：foo.jpg -> foo_1.jpg、foo_2.jpg ..."""
    if not dst.exists():
        return dst
    stem = dst.stem
    suffix = dst.suffix
    for i in itertools.count(1):
        candidate = dst.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate


def parse_cutoff_date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError("日期格式須為 YYYY-MM-DD")


def parse_extensions(exts: Optional[str]) -> Optional[Set[str]]:
    """將逗號分隔的副檔名轉為小寫集合；傳入 None/空字串則回傳 None 表示不過濾。"""
    if not exts:
        return None
    parts = [e.strip().lower() for e in exts.split(",") if e.strip()]
    if not parts:
        return None
    normalized: Set[str] = set()
    for e in parts:
        if not e.startswith("."):
            e = "." + e
        normalized.add(e)
    return normalized or None


def should_include_file(path: Path, allowed_exts: Optional[Set[str]]) -> bool:
    if allowed_exts is None:
        return True
    return path.suffix.lower() in allowed_exts


def print_progress(prefix: str, done: int, total: int, extra: str = "") -> None:
    percent = (done / total * 100) if total else 100.0
    msg = f"\r{prefix} {done}/{total} ({percent:5.1f}%)"
    if extra:
        msg += f" | {extra}"
    sys.stdout.write(msg)
    sys.stdout.flush()


def collect_candidates(
    src_root: Path,
    cutoff_date: dt.date,
    min_size_bytes: int,
    allowed_exts: Optional[Set[str]],
) -> List[Path]:
    """收集符合日期與大小（以及副檔名過濾）的候選檔案清單。"""
    candidates: List[Path] = []
    for date_dir, folder_date in iter_date_dirs(src_root):
        if folder_date >= cutoff_date:
            continue
        for f in date_dir.rglob("*"):
            if not f.is_file():
                continue
            if allowed_exts is not None and not should_include_file(f, allowed_exts):
                continue
            try:
                if f.stat().st_size > min_size_bytes:
                    candidates.append(f)
            except OSError:
                continue
    return candidates


def transfer_all(
    files: List[Path],
    src_root: Path,
    dst_root: Path,
    dry_run: bool,
    preserve_structure: bool,
    on_conflict: str,
    workers: int,
) -> Tuple[int, List[Tuple[Path, str]]]:
    """並行搬移檔案。回傳 (成功數, 錯誤清單)。"""
    total = len(files)
    done = 0
    success = 0
    errors: List[Tuple[Path, str]] = []

    def task(src: Path) -> Tuple[bool, Optional[str]]:
        try:
            planned = build_destination_path(
                src_file=src,
                src_root=src_root,
                dst_root=dst_root,
                preserve_structure=preserve_structure,
            )
            target = resolve_conflict(planned, on_conflict=on_conflict)
            if target is None:
                # skip by policy
                if dry_run:
                    sys.stdout.write(f"\n[DRY-RUN][SKIP] {src}  →  {planned} (on_conflict=skip & 存在)")
                return False, None

            if dry_run:
                sys.stdout.write(f"\n[DRY-RUN] {src}  →  {target}")
                return True, None

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(target))
                return True, None
            except Exception as e:
                return False, str(e)
        except Exception as e:
            return False, str(e)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(task, p): p for p in files}
        for fut in as_completed(futures):
            ok, err = fut.result()
            done += 1
            if ok:
                success += 1
            elif err is not None:
                errors.append((futures[fut], err))
            print_progress("傳輸中", done, total, extra=f"已完成 {success}")

    sys.stdout.write("\n")
    return success, errors


def build_destination_path(
    src_file: Path,
    src_root: Path,
    dst_root: Path,
    preserve_structure: bool,
) -> Path:
    """決定目的檔路徑；若保留結構，則以 src_root 為基準保留子路徑。"""
    if preserve_structure:
        try:
            rel_dir = src_file.parent.relative_to(src_root)
        except ValueError:
            # 萬一不是子路徑，退化為扁平化
            rel_dir = Path("")
        return (dst_root / rel_dir / src_file.name)
    else:
        return dst_root / src_file.name


def resolve_conflict(dest_path: Path, on_conflict: str) -> Optional[Path]:
    """根據策略處理同名衝突，回傳最終目的路徑或 None(跳過)。"""
    if not dest_path.exists():
        return dest_path
    if on_conflict == "skip":
        return None
    if on_conflict == "overwrite":
        try:
            dest_path.unlink()
        except OSError:
            # 若刪除失敗，仍嘗試覆蓋
            pass
        return dest_path
    if on_conflict == "rename":
        return generate_unique_path(dest_path)
    return None


def move_files(
    src_root: Path,
    dst_dir: Path,
    cutoff_date: dt.date,
    min_size_bytes: int,
    allowed_exts: Optional[Set[str]],
    dry_run: bool,
    preserve_structure: bool,
    on_conflict: str,
    workers: int,
) -> int:
    """向後相容的封裝：收集候選→並行搬移，回傳成功數。"""
    candidates = collect_candidates(
        src_root=src_root,
        cutoff_date=cutoff_date,
        min_size_bytes=min_size_bytes,
        allowed_exts=allowed_exts,
    )
    if not candidates:
        return 0
    success, errors = transfer_all(
        files=candidates,
        src_root=src_root,
        dst_root=dst_dir,
        dry_run=dry_run,
        preserve_structure=preserve_structure,
        on_conflict=on_conflict,
        workers=workers,
    )
    if errors:
        sys.stdout.write("\n=== 傳輸錯誤(前 10) ===\n")
        for path, err in errors[:10]:
            sys.stdout.write(f"{path} | {err}\n")
    return success


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="搬移指定日期以前且大於指定大小(KB)的檔案到目的地 (預設扁平化)",
    )
    parser.add_argument("--src", "-s", required=True, help="來源根目錄（含 YYYY-MM-DD 子資料夾）")
    parser.add_argument("--dest", "-d", required=True, help="目的地根目錄")
    parser.add_argument(
        "--cutoff-date",
        type=parse_cutoff_date,
        default=dt.date(2023, 7, 10),
        help="截止日期（早於此日期才搬），格式 YYYY-MM-DD，預設 2023-07-10",
    )
    parser.add_argument(
        "--min-size-kb",
        type=int,
        default=60,
        help="最小大小（KB），僅搬移大於此大小者，預設 60",
    )
    parser.add_argument(
        "--ext",
        help="可選副檔名白名單，逗號分隔，例如：.jpg,.png 或 jpg,png（不填則不過濾）",
    )
    parser.add_argument(
        "--preserve-structure",
        action="store_true",
        help="保留來源子目錄結構至目的地（以 --src 為相對根目錄）",
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
        help="並行搬移的執行緒數（預設 8）",
    )
    parser.add_argument("--dry-run", action="store_true", help="僅預覽即將搬移的檔案，不實際移動")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    src_root = Path(args.src).expanduser().resolve()
    dst_dir = Path(args.dest).expanduser().resolve()

    if not src_root.is_dir():
        print(f"❌ 來源資料夾不存在：{src_root}")
        raise SystemExit(1)

    dst_dir.mkdir(parents=True, exist_ok=True)

    allowed_exts = parse_extensions(args.ext)
    min_size_bytes = max(0, int(args.min_size_kb)) * 1024

    moved = move_files(
        src_root=src_root,
        dst_dir=dst_dir,
        cutoff_date=args.cutoff_date,
        min_size_bytes=min_size_bytes,
        allowed_exts=allowed_exts,
        dry_run=args.dry_run,
        preserve_structure=args.preserve_structure,
        on_conflict=args.on_conflict,
        workers=max(1, int(args.workers)),
    )

    verb = "符合條件（僅預覽，未移動）" if args.dry_run else "個檔案"
    ext_info = f", 副檔名過濾={','.join(sorted(allowed_exts))}" if allowed_exts else ""
    print("\n=== 摘要 ===")
    print(f"來源：{src_root}")
    print(f"目的地：{dst_dir}")
    print(f"截止日期：{args.cutoff_date}")
    print(f"最小大小：{args.min_size_kb} KB{ext_info}")
    print(f"保留結構：{bool(args.preserve_structure)} | 衝突策略：{args.on_conflict}")
    print(f"共 {moved} {verb}。")


if __name__ == "__main__":
    main()


