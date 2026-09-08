from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image, ImageFile
except ImportError:
    print("Pillow is required. Install it with: python -m pip install -r requirements.txt")
    raise SystemExit(1)

ImageFile.LOAD_TRUNCATED_IMAGES = False

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".webp", ".heic", ".heif", ".avif", ".ico",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".3gp", ".mts", ".m2ts",
}
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".wma", ".opus",
}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@dataclass
class MediaFile:
    path: Path
    kind: str
    status: str = ""
    reason: str = ""
    camera: str = ""
    captured_at: datetime | None = None
    is_duplicate: bool = False
    duplicate_of: Path | None = None


DEDUPE_KEYS = {"name", "size", "date", "sha256"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find, validate, and organize photos, videos, and audio.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without moving files.")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of moving them.")
    parser.add_argument(
        "--dedupe-by",
        type=parse_dedupe_keys,
        default=(),
        metavar="KEYS",
        help="Mark duplicates when all selected keys match: name,size,date,sha256 (comma-separated).",
    )
    return parser.parse_args()


def parse_dedupe_keys(value: str) -> tuple[str, ...]:
    keys = tuple(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))
    invalid = set(keys) - DEDUPE_KEYS
    if invalid:
        allowed = ", ".join(sorted(DEDUPE_KEYS))
        raise argparse.ArgumentTypeError(f"unknown dedupe key(s): {', '.join(sorted(invalid))}; choose from {allowed}")
    return keys


def prompt_folder(prompt: str) -> Path:
    while True:
        value = input(prompt).strip().strip('"')
        if not value:
            print("Please enter a folder path.")
            continue
        path = Path(value).expanduser()
        if path.exists() and path.is_dir():
            return path.resolve()
        print(f"Folder does not exist or is not a folder: {path}")


def media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def scan_files(source: Path, output: Path) -> list[MediaFile]:
    found: list[MediaFile] = []
    visited = 0
    started = time.monotonic()
    print("\nSTEP 1/4 - Scanning for media files")
    print("This can take a while on a whole drive. Press Ctrl+C to stop safely.\n")

    for root, directories, filenames in os.walk(source, topdown=True, onerror=lambda error: None):
        root_path = Path(root)
        if is_inside(root_path, output):
            directories[:] = []
            continue
        directories[:] = [directory for directory in directories if not directory.startswith("$")]
        for filename in filenames:
            visited += 1
            path = root_path / filename
            kind = media_kind(path)
            if kind:
                found.append(MediaFile(path=path, kind=kind))
                print(f"\rFound: {len(found):,} media files | Current: {path}", end="", flush=True)
    elapsed = time.monotonic() - started
    print(f"\nScan complete: {len(found):,} media files found ({visited:,} files visited in {elapsed:.1f}s).")
    return found


def clean_folder_name(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    return value[:120] or "unknown camera"


def exif_text(exif: object, names: tuple[str, ...]) -> str:
    if not exif:
        return ""
    for key, value in exif.items():
        key_name = str(key).lower()
        if any(name in key_name for name in names):
            text = str(value).strip().replace("\x00", "")
            if text:
                return text
    return ""


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:25], pattern)
        except ValueError:
            pass
    return None


def file_date(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def validate_photo(media: MediaFile) -> None:
    try:
        with Image.open(media.path) as image:
            image.verify()
        with Image.open(media.path) as image:
            width, height = image.size
            if width < 2 or height < 2:
                raise ValueError("image has no meaningful dimensions")
            image.load()
            extrema = image.convert("L").getextrema()
            if extrema[0] == extrema[1] and extrema[0] in (0, 255):
                raise ValueError("image contains only pure black or white pixels")
            exif = image.getexif()
            make = str(exif.get(271, "")).strip()
            model = str(exif.get(272, "")).strip()
            captured = parse_datetime(str(exif.get(36867, ""))) or parse_datetime(str(exif.get(306, "")))
            media.camera = clean_folder_name(" ".join(part for part in (make, model) if part))
            media.captured_at = captured
        media.status = "valid"
    except Exception as error:
        media.status = "corrupted"
        media.reason = str(error)


def ffprobe(path: Path) -> dict | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=90, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "ffprobe rejected the file"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "ffprobe returned invalid data"}


def validate_non_photo(media: MediaFile) -> None:
    probe = ffprobe(media.path)
    if probe is None:
        media.status = "unchecked"
        media.reason = "ffprobe is not installed"
        media.captured_at = file_date(media.path)
        return
    if "error" in probe:
        media.status = "corrupted"
        media.reason = probe["error"]
        return
    tags: dict = {}
    tags.update(probe.get("format", {}).get("tags", {}))
    for stream in probe.get("streams", []):
        tags.update(stream.get("tags", {}))
    make = tags.get("make", tags.get("manufacturer", ""))
    model = tags.get("model", tags.get("device_model", ""))
    media.camera = clean_folder_name(" ".join(part for part in (str(make).strip(), str(model).strip()) if part))
    media.captured_at = (
        parse_datetime(str(tags.get("creation_time", "")))
        or parse_datetime(str(tags.get("date", "")))
        or file_date(media.path)
    )
    media.status = "valid"


def validate(media: MediaFile) -> None:
    if media.kind == "photo":
        validate_photo(media)
    else:
        validate_non_photo(media)


def unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    for number in range(1, 10000):
        candidate = destination.with_name(f"{destination.stem} ({number}){destination.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create a unique destination for {destination.name}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dedupe_key(media: MediaFile, keys: tuple[str, ...]) -> tuple[object, ...] | None:
    values: list[object] = []
    for key in keys:
        try:
            if key == "name":
                values.append(media.path.name.casefold())
            elif key == "size":
                values.append(media.path.stat().st_size)
            elif key == "date":
                captured = media.captured_at or file_date(media.path)
                values.append(captured.isoformat())
            elif key == "sha256":
                values.append(sha256_file(media.path))
        except OSError:
            return None
    return tuple(values)


def mark_duplicates(media: list[MediaFile], keys: tuple[str, ...]) -> int:
    if not keys:
        return 0
    print(f"\nSTEP 3/4 - Checking duplicates by: {', '.join(keys)}")
    seen: dict[tuple[object, ...], MediaFile] = {}
    duplicates = 0
    candidates = [item for item in media if item.status != "corrupted"]
    for index, item in enumerate(candidates, 1):
        key = dedupe_key(item, keys)
        if key is not None:
            original = seen.setdefault(key, item)
            if original is not item:
                item.is_duplicate = True
                item.duplicate_of = original.path
                duplicates += 1
        print(f"\rChecked duplicates: {index:,}/{len(candidates):,} | Duplicates: {duplicates:,}", end="", flush=True)
    print(f"\nDuplicate check complete: {duplicates:,} duplicate files found.")
    return duplicates


def choose_unknown_name(media: Iterable[MediaFile]) -> str:
    samples = [item for item in media if item.camera == ""][:5]
    print("\nSome valid media files do not contain camera maker/model metadata.")
    for item in samples:
        print(f"  {item.path}")
    answer = input("Folder name for these files [unknown camera]: ").strip()
    return clean_folder_name(answer or "unknown camera")


def move_media(media: list[MediaFile], output: Path, unknown_name: str, dry_run: bool, copy: bool) -> None:
    print("\nSTEP 4/4 - Organizing files")
    counts = {"valid": 0, "unchecked": 0, "corrupted": 0}
    for index, item in enumerate(media, 1):
        if item.status == "corrupted":
            destination_folder = output / "Corrupted"
        elif item.is_duplicate:
            destination_folder = output / "Duplicates"
        else:
            camera = item.camera or unknown_name
            destination_folder = output / camera
        destination = unique_destination(destination_folder / item.path.name)
        counts[item.status] += 1
        action = "COPY" if copy else "MOVE"
        print(f"\r{index:,}/{len(media):,} {action}: {item.path.name} -> {destination}", end="", flush=True)
        if not dry_run:
            destination_folder.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(item.path, destination)
            else:
                shutil.move(str(item.path), str(destination))
    print(f"\nDone. Valid: {counts['valid']:,}; unchecked: {counts['unchecked']:,}; corrupted: {counts['corrupted']:,}.")
    if counts["unchecked"]:
        print("Note: unchecked video/audio files were placed with valid media because ffprobe was unavailable.")


def main() -> int:
    args = parse_args()
    print("Media Organizer")
    source = prompt_folder("1) Folder or drive to scan (example C:\\): ")
    output = prompt_folder("2) Existing output folder or drive (example F:\\): ")
    if source == output or is_inside(output, source):
        print("The output folder must not be inside the scan folder, or it could be scanned again.")
        return 2
    if args.dry_run:
        print("DRY RUN: no files will be changed.")

    media = scan_files(source, output)
    if not media:
        return 0

    print("\nSTEP 2/4 - Validating media")
    for index, item in enumerate(media, 1):
        validate(item)
        print(f"\rChecked: {index:,}/{len(media):,} | {item.status.upper():10} | {item.path.name}", end="", flush=True)
    print()
    corrupted = [item for item in media if item.status == "corrupted"]
    mark_duplicates(media, args.dedupe_by)
    unknown = [item for item in media if item.status != "corrupted" and not item.is_duplicate and not item.camera]
    print(f"Validation complete: {len(corrupted):,} corrupted; {len(unknown):,} without camera metadata.")
    unknown_name = choose_unknown_name(unknown) if unknown else "unknown camera"

    answer = input("Start organizing files now? [y/N]: ".strip()).strip().lower()
    if answer not in {"y", "yes"}:
        print("No files were changed.")
        return 0
    move_media(media, output, unknown_name, args.dry_run, args.copy)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. No further files were processed.")
        raise SystemExit(130)
