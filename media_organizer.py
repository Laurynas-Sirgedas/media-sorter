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
    is_thumbnail: bool = False
    width: int = 0
    height: int = 0
    audio_title: str = ""
    audio_artist: str = ""
    audio_album_artist: str = ""
    bitrate: int = 0


DEDUPE_KEYS = {"name", "size", "date", "sha256", "stem", "visual", "audio"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find, validate, and organize photos, videos, and audio.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without moving files.")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of moving them.")
    parser.add_argument(
        "--dedupe-by",
        type=parse_dedupe_keys,
        default=(),
        metavar="KEYS",
        help="Mark duplicates when all selected keys match: name,size,date,sha256,stem,visual,audio (comma-separated).",
    )
    parser.add_argument(
        "--visual-threshold",
        type=int,
        default=24,
        metavar="N",
        help="Max perceptual hash difference (0-256 bits) for the 'visual' dedupe key to treat photos as the same photo, even resized or recompressed. Every candidate pair is compared exhaustively using two independent hashes that must both agree, for maximum accuracy (default: 24).",
    )
    parser.add_argument(
        "--trash-duplicates",
        action="store_true",
        help="Move duplicates to a Trash Bin folder instead of a Duplicates folder, for manual removal.",
    )
    parser.add_argument(
        "--separate-audio",
        action="store_true",
        help="Place audio files directly in an Audio folder instead of a camera-model folder.",
    )
    parser.add_argument(
        "--strip-repaired-suffix",
        action="store_true",
        help="Remove the '_repaired' keyword (and its numbered variants) from filenames when organizing.",
    )
    parser.add_argument(
        "--strip-numeric-suffix",
        action="store_true",
        help="Remove a trailing random numeric ID, such as '_170385735', appended by sync or recovery tools.",
    )
    parser.add_argument(
        "--exclude-thumbnails",
        action="store_true",
        help="Move small thumbnail-sized photos (see --thumbnail-max-size) to the Trash Bin folder.",
    )
    parser.add_argument(
        "--thumbnail-max-size",
        type=int,
        default=320,
        metavar="PIXELS",
        help="Photos where both width and height are below this size are treated as thumbnails (default: 320).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rescan an already organized folder (source and output may be the same) to fix corrupted or duplicate files.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        metavar="PATH",
        help="Folder or drive to scan. Skips the interactive prompt.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="Existing output folder or drive. Skips the interactive prompt.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run unattended: skip the final confirmation and use 'unknown device' automatically without asking.",
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


def scan_files(source: Path, output: Path, exclude_output: bool = True) -> list[MediaFile]:
    found: list[MediaFile] = []
    visited = 0
    started = time.monotonic()
    print("\nSTEP 1/4 - Scanning for media files")
    print("This can take a while on a whole drive. Press Ctrl+C to stop safely.\n")

    for root, directories, filenames in os.walk(source, topdown=True, onerror=lambda error: None):
        root_path = Path(root)
        if exclude_output and is_inside(root_path, output):
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
    return value[:120] or "unknown device"


def normalize_stem(stem: str) -> str:
    """Strip recovery-tool noise (hashes, '(deleted ...)', '_repaired') so patterned duplicates match."""
    text = stem
    text = re.sub(r"\(deleted[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"b['\"][0-9a-f]{8,}['\"]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[`'][0-9]{6,}[`']", "", text)
    text = re.sub(r"[_\-\s]*\d+[_\-\s]*repaired", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_\-\s]*repaired", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_\-\s]{2,}", "_", text)
    text = text.strip("_- ")
    return text.casefold() or stem.casefold()


def strip_repaired_suffix(filename: str) -> str:
    path = Path(filename)
    stem = re.sub(r"[ _-]*repaired", "", path.stem, flags=re.IGNORECASE)
    stem = stem.strip(" _-")
    return f"{stem or path.stem}{path.suffix}"


def strip_numeric_suffix(filename: str) -> str:
    """Remove a trailing random numeric ID such as '_170385735' appended by sync/recovery tools."""
    path = Path(filename)
    stem = re.sub(r"[ _-]\d{4,}$", "", path.stem)
    stem = stem.strip(" _-")
    return f"{stem or path.stem}{path.suffix}"


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


def longest_flat_run(lines: list[list[int]], tolerance: int) -> int:
    longest = 0
    run = 0
    for line in lines:
        if max(line) - min(line) <= tolerance:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def detect_block_corruption(image: Image.Image) -> str | None:
    """Flag large uniform bands typical of a truncated/partial JPEG decode."""
    size = 128
    tolerance = 4
    min_run = max(1, int(size * 0.15))
    pixels = list(image.convert("L").resize((size, size)).getdata())
    rows = [pixels[row * size:(row + 1) * size] for row in range(size)]
    columns = [pixels[col::size] for col in range(size)]
    if longest_flat_run(rows, tolerance) >= min_run or longest_flat_run(columns, tolerance) >= min_run:
        return "image contains a large uniform block, likely a truncated or corrupted decode"
    return None


def validate_photo(media: MediaFile, exclude_thumbnails: bool = False, thumbnail_max_size: int = 320) -> None:
    try:
        with Image.open(media.path) as image:
            image.verify()
        with Image.open(media.path) as image:
            width, height = image.size
            if width < 2 or height < 2:
                raise ValueError("image has no meaningful dimensions")
            media.width, media.height = width, height
            if exclude_thumbnails and max(width, height) < thumbnail_max_size:
                media.is_thumbnail = True
            image.load()
            extrema = image.convert("L").getextrema()
            if extrema[0] == extrema[1] and extrema[0] in (0, 255):
                raise ValueError("image contains only pure black or white pixels")
            block_issue = detect_block_corruption(image)
            if block_issue:
                raise ValueError(block_issue)
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
            ["ffprobe", "-v", "error", "-show_entries", "format=bit_rate:format_tags:stream_tags", "-of", "json", str(path)],
            capture_output=True, timeout=90, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        return {"error": error or "ffprobe rejected the file"}
    try:
        output = result.stdout.decode("utf-8", errors="replace")
        return json.loads(output)
    except json.JSONDecodeError:
        return {"unchecked": "ffprobe returned invalid or undecodable metadata"}


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
    if "unchecked" in probe:
        media.status = "unchecked"
        media.reason = probe["unchecked"]
        media.captured_at = file_date(media.path)
        return
    tags: dict = {}
    tags.update(probe.get("format", {}).get("tags", {}))
    for stream in probe.get("streams", []):
        tags.update(stream.get("tags", {}))
    make = tags.get("make", tags.get("manufacturer", ""))
    model = tags.get("model", tags.get("device_model", ""))
    media.camera = clean_folder_name(" ".join(part for part in (str(make).strip(), str(model).strip()) if part))
    media.audio_title = str(tags.get("title", "")).strip()
    subtitle = str(tags.get("subtitle", "")).strip()
    if subtitle:
        media.audio_title = f"{media.audio_title} {subtitle}".strip()
    artist = tags.get("artist") or tags.get("contributing_artist") or tags.get("performer") or ""
    media.audio_artist = str(artist).strip()
    album_artist = tags.get("album_artist") or tags.get("albumartist") or ""
    media.audio_album_artist = str(album_artist).strip()
    try:
        media.bitrate = int(float(probe.get("format", {}).get("bit_rate", 0)))
    except (TypeError, ValueError):
        media.bitrate = 0
    media.captured_at = (
        parse_datetime(str(tags.get("creation_time", "")))
        or parse_datetime(str(tags.get("date", "")))
        or file_date(media.path)
    )
    media.status = "valid"


def validate(media: MediaFile, exclude_thumbnails: bool = False, thumbnail_max_size: int = 320) -> None:
    if media.kind == "photo":
        validate_photo(media, exclude_thumbnails, thumbnail_max_size)
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


def dhash_photo(path: Path, hash_size: int = 16) -> str | None:
    """Perceptual hash: same result for the same photo resized or recompressed differently."""
    try:
        with Image.open(path) as image:
            grey = image.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            pixels = list(grey.getdata())
    except Exception:
        return None
    bits = 0
    width = hash_size + 1
    for row in range(hash_size):
        offset = row * width
        for col in range(hash_size):
            bits = (bits << 1) | int(pixels[offset + col] > pixels[offset + col + 1])
    return format(bits, f"0{hash_size * hash_size // 4}x")


def ahash_photo(path: Path, hash_size: int = 16) -> str | None:
    """Average hash: a second, independent perceptual signal to confirm a dHash match."""
    try:
        with Image.open(path) as image:
            grey = image.convert("L").resize((hash_size, hash_size), Image.LANCZOS)
            pixels = list(grey.getdata())
    except Exception:
        return None
    average = sum(pixels) / len(pixels)
    bits = 0
    for value in pixels:
        bits = (bits << 1) | int(value > average)
    return format(bits, f"0{hash_size * hash_size // 4}x")


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def visual_cluster(items: list[MediaFile], threshold: int) -> list[list[MediaFile]]:
    """Exhaustively compare every candidate pair; a match requires BOTH dHash and aHash to agree."""
    n = len(items)
    if n < 2:
        return []
    dhashes: list[str | None] = [None] * n
    ahashes: list[str | None] = [None] * n
    buckets: dict[float, list[int]] = {}
    for index, item in enumerate(items):
        dhashes[index] = dhash_photo(item.path)
        ahashes[index] = ahash_photo(item.path)
        if dhashes[index] is None or ahashes[index] is None:
            continue
        aspect = round(item.width / item.height, 2) if item.height else 0.0
        buckets.setdefault(aspect, []).append(index)
        print(f"\rHashed {index + 1:,}/{n:,} photos for visual comparison", end="", flush=True)
    print()

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for indices in buckets.values():
        for position, i in enumerate(indices):
            for j in indices[position + 1:]:
                if (
                    hamming_distance(dhashes[i], dhashes[j]) <= threshold
                    and hamming_distance(ahashes[i], ahashes[j]) <= threshold
                ):
                    union(i, j)

    groups: dict[int, list[MediaFile]] = {}
    for index, item in enumerate(items):
        if dhashes[index] is None:
            continue
        groups.setdefault(find(index), []).append(item)
    return [group for group in groups.values() if len(group) > 1]


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
            elif key == "stem":
                values.append(normalize_stem(media.path.stem))
            elif key == "audio":
                if media.kind != "audio" or not media.audio_title.strip():
                    return None
                values.append(media.audio_title.strip().casefold())
                values.append(media.audio_artist.strip().casefold())
                values.append(media.audio_album_artist.strip().casefold())
        except OSError:
            return None
    return tuple(values)


def pick_representative(group: list[MediaFile]) -> MediaFile:
    """Prefer the highest-resolution photo, or highest-bitrate song, in a duplicate group."""
    photos = [item for item in group if item.kind == "photo" and item.width and item.height]
    if photos:
        return max(photos, key=lambda item: item.width * item.height)
    audio_items = [item for item in group if item.kind == "audio" and item.bitrate]
    if audio_items:
        return max(audio_items, key=lambda item: item.bitrate)
    return group[0]


def mark_duplicates(media: list[MediaFile], keys: tuple[str, ...], visual_threshold: int = 6) -> int:
    if not keys:
        return 0
    print(f"\nSTEP 3/4 - Checking duplicates by: {', '.join(keys)}")
    duplicates = 0
    candidates = [item for item in media if item.status != "corrupted"]

    if "visual" in keys:
        remainder = tuple(key for key in keys if key != "visual")
        photo_candidates = [item for item in candidates if item.kind == "photo"]
        folder_groups: dict[object, list[MediaFile]] = {}
        for item in photo_candidates:
            # Group by detected camera when known, so scattered same-device photos are still
            # compared together; fall back to the current folder for unknown-device photos.
            group_key = item.camera if item.camera else item.path.parent
            folder_groups.setdefault(group_key, []).append(item)
        clusters: list[list[MediaFile]] = []
        for group_index, group_items in enumerate(folder_groups.values(), 1):
            print(f"\nGroup {group_index:,}/{len(folder_groups):,}: {len(group_items):,} photos")
            clusters.extend(visual_cluster(group_items, threshold=visual_threshold))
        for cluster in clusters:
            if remainder:
                groups: dict[tuple[object, ...], list[MediaFile]] = {}
                for item in cluster:
                    subkey = dedupe_key(item, remainder)
                    if subkey is not None:
                        groups.setdefault(subkey, []).append(item)
                subclusters = list(groups.values())
            else:
                subclusters = [cluster]
            for group in subclusters:
                if len(group) < 2:
                    continue
                representative = pick_representative(group)
                for item in group:
                    if item is not representative:
                        item.is_duplicate = True
                        item.duplicate_of = representative.path
                        duplicates += 1
        print(f"Checked {len(photo_candidates):,} photos for visual duplicates.")
        print(f"Duplicate check complete: {duplicates:,} duplicate files found.")
        return duplicates

    groups: dict[tuple[object, ...], list[MediaFile]] = {}
    for index, item in enumerate(candidates, 1):
        key = dedupe_key(item, keys)
        if key is not None:
            groups.setdefault(key, []).append(item)
        print(f"\rChecked: {index:,}/{len(candidates):,}", end="", flush=True)
    print()
    for group in groups.values():
        if len(group) < 2:
            continue
        representative = pick_representative(group)
        for item in group:
            if item is not representative:
                item.is_duplicate = True
                item.duplicate_of = representative.path
                duplicates += 1
    print(f"Duplicate check complete: {duplicates:,} duplicate files found.")
    return duplicates


def choose_unknown_name(media: Iterable[MediaFile]) -> str:
    samples = [item for item in media if item.camera == ""][:5]
    print("\nSome valid media files do not contain camera maker/model metadata.")
    for item in samples:
        print(f"  {item.path}")
    answer = input("Folder name for these files [unknown device]: ").strip()
    return clean_folder_name(answer or "unknown device")


def move_media(
    media: list[MediaFile],
    output: Path,
    unknown_name: str,
    dry_run: bool,
    copy: bool,
    separate_audio: bool = False,
    trash_duplicates: bool = False,
    strip_repaired: bool = False,
    strip_numeric: bool = False,
) -> None:
    print("\nSTEP 4/4 - Organizing files")
    counts = {"valid": 0, "unchecked": 0, "corrupted": 0}
    skipped_in_place = 0
    duplicate_count = sum(1 for item in media if item.is_duplicate)
    thumbnail_count = sum(1 for item in media if item.is_thumbnail and not item.is_duplicate)
    for index, item in enumerate(media, 1):
        counts[item.status] += 1
        if item.status == "corrupted":
            destination_folder = output / "Corrupted"
        elif item.is_duplicate:
            destination_folder = output / ("Trash Bin" if trash_duplicates else "Duplicates")
        elif item.is_thumbnail:
            destination_folder = output / "Trash Bin"
        elif separate_audio and item.kind == "audio":
            destination_folder = output / "Audio"
        else:
            camera = item.camera or unknown_name
            destination_folder = output / camera
        target_name = strip_repaired_suffix(item.path.name) if strip_repaired else item.path.name
        target_name = strip_numeric_suffix(target_name) if strip_numeric else target_name
        prospective_destination = destination_folder / target_name
        if prospective_destination.resolve() == item.path.resolve():
            skipped_in_place += 1
            print(f"\r{index:,}/{len(media):,} SKIP (already in place): {item.path}", end="", flush=True)
            continue
        destination = unique_destination(prospective_destination)
        action = "COPY" if copy else "MOVE"
        print(f"\r{index:,}/{len(media):,} {action}: {item.path.name} -> {destination}", end="", flush=True)
        if not dry_run:
            destination_folder.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(item.path, destination)
            else:
                shutil.move(str(item.path), str(destination))
    print(f"\nDone. Valid: {counts['valid']:,}; unchecked: {counts['unchecked']:,}; corrupted: {counts['corrupted']:,}.")
    if duplicate_count:
        bin_name = "Trash Bin" if trash_duplicates else "Duplicates"
        print(f"{duplicate_count:,} duplicate files were placed in '{bin_name}' for review.")
    if thumbnail_count:
        print(f"{thumbnail_count:,} thumbnail-sized photos were placed in 'Trash Bin' for review.")
    if skipped_in_place:
        print(f"Skipped {skipped_in_place:,} files already in their correct location.")
    if counts["unchecked"]:
        print("Note: unchecked video/audio files were placed with valid media because ffprobe was unavailable.")


def resolve_folder_arg(path: Path | None, prompt: str) -> Path | None:
    if path is None:
        return prompt_folder(prompt)
    resolved = path.expanduser()
    if not (resolved.exists() and resolved.is_dir()):
        print(f"Folder does not exist or is not a folder: {resolved}")
        return None
    return resolved.resolve()


def main() -> int:
    args = parse_args()
    print("Media Organizer")
    source = resolve_folder_arg(args.source, "1) Folder or drive to scan (example C:\\): ")
    output = resolve_folder_arg(args.output, "2) Existing output folder or drive (example F:\\): ")
    if source is None or output is None:
        return 2
    if args.in_place:
        print("IN-PLACE MODE: rescanning the destination itself to fix corrupted or duplicate files.")
    elif source == output or is_inside(output, source):
        print("The output folder must not be inside the scan folder, or it could be scanned again.")
        return 2
    if args.dry_run:
        print("DRY RUN: no files will be changed.")
    if args.yes:
        print("UNATTENDED MODE: no further confirmation will be requested.")

    media = scan_files(source, output, exclude_output=not args.in_place)
    if not media:
        return 0

    print("\nSTEP 2/4 - Validating media")
    for index, item in enumerate(media, 1):
        validate(item, args.exclude_thumbnails, args.thumbnail_max_size)
        print(f"\rChecked: {index:,}/{len(media):,} | {item.status.upper():10} | {item.path.name}", end="", flush=True)
    print()
    corrupted = [item for item in media if item.status == "corrupted"]
    mark_duplicates(media, args.dedupe_by, visual_threshold=args.visual_threshold)
    unknown = [item for item in media if item.status != "corrupted" and not item.is_duplicate and not item.camera]
    print(f"Validation complete: {len(corrupted):,} corrupted; {len(unknown):,} without camera metadata.")
    if unknown and not args.yes:
        unknown_name = choose_unknown_name(unknown)
    else:
        unknown_name = "unknown device"
        if unknown:
            print(f"{len(unknown):,} files without camera metadata will use 'unknown device' (unattended mode).")

    if not args.yes:
        answer = input("Start organizing files now? [y/N]: ".strip()).strip().lower()
        if answer not in {"y", "yes"}:
            print("No files were changed.")
            return 0
    move_media(
        media, output, unknown_name, args.dry_run, args.copy,
        separate_audio=args.separate_audio, trash_duplicates=args.trash_duplicates,
        strip_repaired=args.strip_repaired_suffix,
        strip_numeric=args.strip_numeric_suffix,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. No further files were processed.")
        raise SystemExit(130)
