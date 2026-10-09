from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from qcloud_cos import CosConfig, CosS3Client

try:
    from qcloud_cos import CosServiceError
except Exception:  # pragma: no cover - fallback if SDK changes
    CosServiceError = Exception

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - optional dependency
    MutagenFile = None

THUMB_URL = "https://haohao-1312736007.cos.ap-guangzhou.myqcloud.com/SSS/sss.png"


def iter_files(root: Path, *, recursive: bool) -> Iterable[Path]:
    if recursive:
        for path in root.rglob("*"):
            if path.is_file():
                yield path
    else:
        for path in root.iterdir():
            if path.is_file():
                yield path


def normalize_prefix(prefix: str) -> str:
    cleaned = prefix.strip().lstrip("/")
    if cleaned and not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned


def build_key(root: Path, path: Path, *, prefix: str, recursive: bool) -> str:
    if recursive:
        rel = path.relative_to(root).as_posix()
    else:
        rel = path.name
    return f"{prefix}{rel}" if prefix else rel


def make_client(
    *,
    secret_id: str,
    secret_key: str,
    region: str,
    token: str | None,
) -> CosS3Client:
    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Token=token,
        Scheme="https",
    )
    return CosS3Client(config)


def get_audio_duration(path: Path) -> float:
    if MutagenFile is None:
        raise SystemExit("Missing dependency: pip install mutagen")
    audio = MutagenFile(str(path))
    if not audio or not getattr(audio, "info", None):
        raise SystemExit(f"Failed to read audio info: {path}")
    length = getattr(audio.info, "length", None)
    if length is None:
        raise SystemExit(f"Failed to read audio duration: {path}")
    return float(length)


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_cos_url(*, bucket: str, region: str, key: str) -> str:
    encoded_key = quote(key, safe="/~")
    return f"https://{bucket}.cos.{region}.myqcloud.com/{encoded_key}"


def _get_status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status
    getter = getattr(exc, "get_status_code", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _get_error_code(exc: Exception) -> str | None:
    code = getattr(exc, "error_code", None)
    if code:
        return code
    getter = getattr(exc, "get_error_code", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def object_exists(client: CosS3Client, *, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except CosServiceError as exc:
        status = _get_status_code(exc)
        if status == 404:
            return False
        code = _get_error_code(exc)
        if code in {"NoSuchKey", "NotFound", "NoSuchResource"}:
            return False
        raise


def find_cover_for_mp3(mp3_path: Path, cover_dir: Path) -> Path | None:
    if not cover_dir.exists():
        return None
    stem = mp3_path.stem
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = cover_dir / f"{stem}{ext}"
        if candidate.exists() and candidate.is_file():
            return candidate
    for candidate in cover_dir.iterdir():
        if candidate.is_file() and candidate.stem == stem:
            return candidate
    return None


def build_cover_key(prefix: str, filename: str) -> str:
    normalized = normalize_prefix(prefix)
    return f"{normalized}{filename}" if normalized else filename


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload files in a local folder to Tencent Cloud COS."
    )
    parser.add_argument("folder", help="Local folder to upload")
    parser.add_argument(
        "--bucket",
        default=os.getenv("COS_BUCKET", ""),
        help="Bucket name, e.g. examplebucket-1250000000 (or set COS_BUCKET)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("COS_REGION", ""),
        help="COS region, e.g. ap-guangzhou (or set COS_REGION)",
    )
    parser.add_argument(
        "--secret-id",
        default=os.getenv("COS_SECRET_ID", ""),
        help="SecretId (or set COS_SECRET_ID)",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("COS_SECRET_KEY", ""),
        help="SecretKey (or set COS_SECRET_KEY)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("COS_TOKEN", ""),
        help="Temporary token if using STS (or set COS_TOKEN)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Remote key prefix (folder) in COS",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Upload files recursively (preserve subfolders)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without uploading",
    )
    parser.add_argument(
        "--cover-dir",
        default="downloads/cover",
        help="Directory containing cover images (default: downloads/cover)",
    )
    parser.add_argument(
        "--cover-prefix",
        default="SSS/cover",
        help="COS prefix for cover images (default: SSS/cover)",
    )
    parser.add_argument(
        "--csv-dir",
        default="uploads",
        help="Directory to save the JSON file (default: uploads)",
    )
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Folder not found: {folder}")

    if not args.bucket or not args.region or not args.secret_id or not args.secret_key:
        raise SystemExit(
            "Missing required credentials: --bucket, --region, --secret-id, --secret-key "
            "(or set COS_BUCKET/COS_REGION/COS_SECRET_ID/COS_SECRET_KEY)."
        )

    token = args.token if args.token else None
    client = make_client(
        secret_id=args.secret_id,
        secret_key=args.secret_key,
        region=args.region,
        token=token,
    )

    prefix = normalize_prefix(args.prefix)
    files = list(iter_files(folder, recursive=args.recursive))
    if not files:
        print("No files found.")
        return

    uploaded = 0
    skipped = 0
    rows: list[dict[str, str]] = []
    cover_dir = Path(args.cover_dir).expanduser()
    for path in files:
        key = build_key(folder, path, prefix=prefix, recursive=args.recursive)
        url = build_cos_url(bucket=args.bucket, region=args.region, key=key)
        title = path.stem
        duration = format_duration(get_audio_duration(path))
        thumb_url = THUMB_URL
        cover_path = find_cover_for_mp3(path, cover_dir)
        if cover_path:
            cover_key = build_cover_key(args.cover_prefix, cover_path.name)
            cover_url = build_cos_url(
                bucket=args.bucket,
                region=args.region,
                key=cover_key,
            )
            if args.dry_run:
                thumb_url = cover_url
            else:
                try:
                    if object_exists(client, bucket=args.bucket, key=cover_key):
                        thumb_url = cover_url
                    else:
                        client.upload_file(
                            Bucket=args.bucket,
                            LocalFilePath=str(cover_path),
                            Key=cover_key,
                        )
                        thumb_url = cover_url
                except Exception as exc:
                    print(f"Warn: cover upload failed for {cover_path}: {exc}")
        row = {
            "url": url,
            "title": title,
            "type": "audio",
            "duration": duration,
            "thumb_url": thumb_url,
            "subtitle": title,
            "tags": "letter",
            "format": "mp3",
            "status": "ready",
        }
        if args.dry_run:
            print(f"DRY-RUN: {path} -> cos://{args.bucket}/{key}")
            rows.append(row)
            continue
        if object_exists(client, bucket=args.bucket, key=key):
            print(f"Skip (exists): {path} -> {key}")
            rows.append(row)
            skipped += 1
            continue
        client.upload_file(
            Bucket=args.bucket,
            LocalFilePath=str(path),
            Key=key,
        )
        print(f"Uploaded: {path} -> {key}")
        rows.append(row)
        uploaded += 1

    output_dir = Path(args.csv_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    json_path = output_dir / f"song_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)

    print(
        f"Done. Uploaded {uploaded} file(s). Skipped {skipped} file(s). "
        f"JSON saved to {json_path}."
    )


if __name__ == "__main__":
    main()
