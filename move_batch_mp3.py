from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def iter_batch_dirs(downloads_dir: Path, prefix: str) -> list[Path]:
    return sorted(
        path for path in downloads_dir.glob(f"{prefix}*") if path.is_dir()
    )


def iter_mp3_files(batch_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for batch_dir in batch_dirs:
        for path in batch_dir.rglob("*"):
            if path.is_file() and path.name.lower().endswith(".mp3"):
                files.append(path)
    return files


def normalize_name(filename: str) -> str:
    cleaned = filename.replace(",", "")
    if cleaned.lower().endswith(".mp3.mp3"):
        cleaned = cleaned[:-4]
    if not cleaned.strip():
        return filename
    return cleaned


def unique_target_path(target_dir: Path, filename: str) -> Path:
    target = target_dir / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for idx in range(1, 10_000):
        candidate = target_dir / f"{stem} ({idx}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find available name for {filename}")


def move_files(files: list[Path], target_dir: Path, *, dry_run: bool) -> int:
    moved = 0
    for path in files:
        normalized = normalize_name(path.name)
        target = unique_target_path(target_dir, normalized)
        if dry_run:
            print(f"DRY-RUN: {path} -> {target}")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        moved += 1
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Move MP3 files from downloads/batch* into a single folder, "
            "fixing .mp3.mp3 and removing commas."
        )
    )
    parser.add_argument(
        "--downloads-dir",
        default="downloads",
        help="Root downloads directory (default: downloads)",
    )
    parser.add_argument(
        "--batch-prefix",
        default="batch",
        help="Batch folder prefix under downloads (default: batch)",
    )
    parser.add_argument(
        "--target-dir",
        default="mp3",
        help="Target folder for moved MP3 files (default: mp3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without moving files",
    )
    args = parser.parse_args()

    downloads_dir = Path(args.downloads_dir).expanduser()
    if not downloads_dir.exists():
        raise SystemExit(f"Directory not found: {downloads_dir}")

    batch_dirs = iter_batch_dirs(downloads_dir, args.batch_prefix)
    if not batch_dirs:
        print(f"No batch directories found under {downloads_dir}.")
        return

    files = iter_mp3_files(batch_dirs)
    if not files:
        print("No MP3 files found.")
        return

    target_dir = Path(args.target_dir).expanduser()
    moved = move_files(files, target_dir, dry_run=args.dry_run)
    print(f"Done. Moved {moved} file(s) into {target_dir}.")


if __name__ == "__main__":
    main()
