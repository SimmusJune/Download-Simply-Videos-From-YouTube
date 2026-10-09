from __future__ import annotations

import argparse
import json
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from download import (
    DEFAULT_CONCURRENT_WORKERS,
    MAX_CONCURRENT_WORKERS,
    download_urls_with_titles,
    sanitize_filename,
)

YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s\"'<>]+",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_ID_PATTERNS = [
    re.compile(r'data-video-id=["\']([A-Za-z0-9_-]{11})["\']', re.IGNORECASE),
    re.compile(r'data-youtube-id=["\']([A-Za-z0-9_-]{11})["\']', re.IGNORECASE),
    re.compile(r'videoId["\']\s*:\s*["\']([A-Za-z0-9_-]{11})', re.IGNORECASE),
]


def _extract_youtube_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if "youtu.be" in host:
        candidate = path.lstrip("/").split("/")[0]
        return candidate if YOUTUBE_ID_RE.match(candidate) else None
    if "youtube.com" in host:
        if path.startswith("/watch"):
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            return video_id if video_id and YOUTUBE_ID_RE.match(video_id) else None
        if path.startswith("/embed/") or path.startswith("/shorts/"):
            parts = path.split("/")
            candidate = parts[2] if len(parts) > 2 else ""
            return candidate if YOUTUBE_ID_RE.match(candidate) else None
    return None


def extract_youtube_links(html: str) -> list[str]:
    seen: set[str] = set()
    ordered_ids: list[str] = []

    for match in YOUTUBE_URL_RE.finditer(html):
        video_id = _extract_youtube_id_from_url(match.group(0))
        if video_id and video_id not in seen:
            seen.add(video_id)
            ordered_ids.append(video_id)

    for pattern in YOUTUBE_ID_PATTERNS:
        for match in pattern.finditer(html):
            video_id = match.group(1)
            if video_id not in seen:
                seen.add(video_id)
                ordered_ids.append(video_id)

    return [f"https://www.youtube.com/watch?v={video_id}" for video_id in ordered_ids]


def extract_songs(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    song_list = soup.find("ul", id="all-songs-list", class_="all-songs")
    if song_list is None:
        return []

    songs: list[dict[str, str]] = []
    for item in song_list.find_all("li", recursive=False):
        link = item.find("a", href=True)
        if not link:
            continue

        title = _extract_song_title(link)
        url = link.get("href", "").strip()
        if title and url:
            songs.append({"title": title, "url": url})

    return songs


def extract_song_covers(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    song_list = soup.find("ul", id="all-songs-list", class_="all-songs")
    if song_list is None:
        return []

    covers: list[dict[str, str]] = []
    for item in song_list.find_all("li", recursive=False):
        link = item.find("a")
        if not link:
            continue
        title = _extract_song_title(link)
        if not title:
            continue
        img = item.find("img")
        if not img:
            continue
        img_url = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-original")
            or ""
        ).strip()
        if img_url:
            covers.append({"title": title, "img_url": img_url})

    return covers


def _extract_song_title(link: Tag) -> str:
    title = ""
    title_node = link.find("span", class_="episodeTitle")
    if title_node:
        title = title_node.get_text(" ", strip=True)
    if not title:
        text = link.get_text(" ", strip=True)
        if text:
            title = text.replace("NEW!", "").strip()
    if not title:
        img = link.find("img", alt=True)
        if img and img.get("alt"):
            title = img["alt"].strip()
    return title


def _resolve_image_url(img_url: str, base_url: str | None) -> str:
    if not img_url:
        return ""
    parsed = urlparse(img_url)
    if parsed.scheme and parsed.netloc:
        return img_url
    if not parsed.scheme and parsed.netloc:
        return f"https:{img_url}"
    if base_url:
        return urljoin(base_url, img_url)
    return img_url


def _guess_extension(url: str, content_type: str | None) -> str:
    suffix = Path(urlparse(url).path).suffix
    if suffix:
        return suffix
    if content_type:
        mime = content_type.split(";", 1)[0].strip()
        ext = mimetypes.guess_extension(mime) or ""
        return ext
    return ""


def download_cover_images(
    covers: list[dict[str, str]],
    *,
    output_dir: Path,
    base_url: str | None,
    timeout: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )

    used_titles: dict[str, int] = {}
    downloaded = skipped = failed = 0

    for cover in covers:
        title = cover.get("title", "").strip()
        img_url = cover.get("img_url", "").strip()
        if not title or not img_url:
            failed += 1
            continue

        resolved_url = _resolve_image_url(img_url, base_url)
        if not resolved_url:
            failed += 1
            continue

        safe_title = sanitize_filename(title) or "untitled"
        count = used_titles.get(safe_title, 0) + 1
        used_titles[safe_title] = count
        filename_base = safe_title if count == 1 else f"{safe_title} ({count})"

        ext = _guess_extension(resolved_url, None)
        if ext:
            target = output_dir / f"{filename_base}{ext}"
            if target.exists():
                skipped += 1
                continue
        print(f"⬇️  Downloading cover for '{title}' from {resolved_url}...")
        try:
            response = session.get(resolved_url, timeout=timeout, stream=True)
            response.raise_for_status()
            ext = ext or _guess_extension(
                resolved_url, response.headers.get("Content-Type")
            )
            if not ext:
                ext = ".jpg"
            target = output_dir / f"{filename_base}{ext}"
            if target.exists():
                skipped += 1
                response.close()
                continue
            with target.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        file.write(chunk)
            downloaded += 1
        except Exception:
            failed += 1

    print(
        f"🖼️  Covers: downloaded {downloaded}, skipped {skipped}, failed {failed}."
    )


def _extract_base_href(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    base = soup.find("base", href=True)
    if base and base.get("href"):
        return str(base["href"]).strip() or None
    return None


def fetch_html(url: str, session: requests.Session, timeout: float) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def enrich_songs_with_youtube(
    songs: list[dict[str, str]],
    *,
    limit: int | None,
    delay: float,
    timeout: float,
) -> list[dict[str, object]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    results: list[dict[str, object]] = []

    for index, song in enumerate(songs):
        if limit is not None and index >= limit:
            break
        try:
            html = fetch_html(song["url"], session, timeout)
            youtube_urls = extract_youtube_links(html)
            result = {
                "title": song["title"],
                "url": song["url"],
                "youtube_urls": youtube_urls,
            }
        except Exception as exc:
            result = {
                "title": song.get("title", ""),
                "url": song.get("url", ""),
                "youtube_urls": [],
                "error": str(exc),
            }
        results.append(result)
        if delay > 0:
            time.sleep(delay)

    return results


def filter_existing_downloads(
    songs: list[dict[str, str]],
    output_dir: Path,
) -> list[dict[str, str]]:
    if not output_dir.exists():
        return songs

    filtered: list[dict[str, str]] = []
    skipped = 0
    for song in songs:
        title = song.get("title", "").strip()
        if not title:
            filtered.append(song)
            continue
        safe_title = sanitize_filename(title)
        if not safe_title:
            filtered.append(song)
            continue
        matches = list(output_dir.glob(f"{safe_title}.*"))
        if matches:
            skipped += 1
            continue
        filtered.append(song)

    if skipped:
        print(f"⏭️  Skipping {skipped} song(s) already in downloads.")

    return filtered


def build_download_items(results: list[dict[str, object]]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    used_titles: dict[str, int] = {}

    for entry in results:
        title = str(entry.get("title", "")).strip()
        youtube_urls = entry.get("youtube_urls") or []
        if not isinstance(youtube_urls, list):
            continue
        for url in youtube_urls:
            base_title = title or "untitled"
            count = used_titles.get(base_title, 0) + 1
            used_titles[base_title] = count
            final_title = base_title if count == 1 else f"{base_title} ({count})"
            items.append((url, final_title))

    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract song titles and URLs from tmp/sss.html",
    )
    parser.add_argument(
        "html_path",
        nargs="?",
        default="tmp/sss.html",
        help="Path to the HTML file to parse",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit how many songs to fetch",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between requests",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download videos for extracted YouTube URLs",
    )
    parser.add_argument(
        "--download-covers",
        action="store_true",
        help="Download cover images from <img> tags",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for downloads",
    )
    parser.add_argument(
        "--cover-output",
        default=None,
        help="Output directory for cover images (default: downloads/cover)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for resolving relative image src values",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help=f"Concurrent downloads (1-{MAX_CONCURRENT_WORKERS})",
    )
    args = parser.parse_args()

    html = Path(args.html_path).read_text(encoding="utf-8")
    songs = extract_songs(html)
    output_dir = Path(args.output) if args.output else Path.cwd() / "downloads"
    songs = filter_existing_downloads(songs, output_dir)


    if args.download_covers:
        covers = extract_song_covers(html)
        if not covers:
            print("\n❌ No cover images found to download.")
        else:
            cover_dir = (
                Path(args.cover_output)
                if args.cover_output
                else Path.cwd() / "downloads" / "cover"
            )
            base_url = args.base_url or _extract_base_href(html)
            download_cover_images(
                covers,
                output_dir=cover_dir,
                base_url=base_url,
                timeout=args.timeout,
            )

    if args.download:
        results = enrich_songs_with_youtube(
            songs,
            limit=args.limit,
            delay=args.delay,
            timeout=args.timeout,
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        items = build_download_items(results)
        if not items:
            print("\n❌ No YouTube URLs found to download.")
            return
        max_workers = args.max_workers or DEFAULT_CONCURRENT_WORKERS
        max_workers = max(1, min(MAX_CONCURRENT_WORKERS, max_workers))
        download_urls_with_titles(
            items,
            audio_only=True,
            output_path=args.output,
            max_workers=max_workers,
        )


if __name__ == "__main__":
    main()
