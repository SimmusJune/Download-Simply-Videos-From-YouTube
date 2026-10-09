from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


def collect_mp3_files(download_dir: Path) -> list[Path]:
    return [
        path
        for path in download_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp3"
    ]


def ensure_target_dirs(
    download_dir: Path,
    total_files: int,
    batch_size: int,
    prefix: str,
    *,
    force: bool,
) -> list[Path]:
    target_dirs: list[Path] = []
    if total_files == 0:
        return target_dirs

    batches = (total_files + batch_size - 1) // batch_size
    for idx in range(1, batches + 1):
        target_dir = download_dir / f"{prefix}_{idx:03d}"
        if target_dir.exists() and any(target_dir.iterdir()) and not force:
            raise SystemExit(
                f"Target folder already exists and is not empty: {target_dir}. "
                f"Use --force or a different --prefix."
            )
        target_dirs.append(target_dir)
    return target_dirs


def move_files(
    files: list[Path],
    target_dirs: list[Path],
    batch_size: int,
    *,
    dry_run: bool,
) -> None:
    for idx, path in enumerate(files):
        target_dir = target_dirs[idx // batch_size]
        target_path = target_dir / path.name
        if dry_run:
            print(f"DRY-RUN: {path} -> {target_path}")
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shuffle MP3 files in downloads and group into folders.",
    )
    parser.add_argument(
        "download_dir",
        nargs="?",
        default="downloads",
        help="Directory containing MP3 files (default: downloads)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of files per folder (default: 50)",
    )
    parser.add_argument(
        "--prefix",
        default="batch",
        help="Folder name prefix (default: batch)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for repeatable shuffle",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without moving files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow using non-empty target folders",
    )
    args = parser.parse_args()

    download_dir = Path(args.download_dir).expanduser()
    if not download_dir.exists():
        raise SystemExit(f"Directory not found: {download_dir}")

    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be a positive integer.")

    files = collect_mp3_files(download_dir)
    if not files:
        print("No MP3 files found.")
        return

    rng = random.Random(args.seed)
    rng.shuffle(files)

    target_dirs = ensure_target_dirs(
        download_dir,
        total_files=len(files),
        batch_size=args.batch_size,
        prefix=args.prefix,
        force=args.force,
    )

    move_files(files, target_dirs, args.batch_size, dry_run=args.dry_run)
    print(f"Done. Moved {len(files)} file(s) into {len(target_dirs)} folder(s).")


if __name__ == "__main__":
    main()
