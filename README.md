# Media Organizer for Windows

An interactive command-line tool for recovering and organizing photos, videos, and audio after a device or drive migration.

The organizer scans a folder or an entire drive, validates media, reads camera metadata when available, and places files directly into camera-model folders. There are no year or month subfolders, so Windows Explorer can sort each folder by date.

## Features

- Recursively scans folders and drives for common photo, video, and audio formats.
- Displays scan and validation progress, including file counts.
- Validates images by decoding their contents, checking dimensions, and detecting pure black/white or large uniform-color blocks typical of truncated decodes.
- Uses `ffprobe` for video and audio probing when FFmpeg is installed.
- Reads camera maker and model metadata, such as `Apple iPhone XS`.
- Prompts for a folder name when media has no camera metadata.
- Places files with missing camera metadata in `unknown device` by default.
- Optionally places all audio files directly in an `Audio` folder with `--separate-audio`.
- Moves unreadable media to `Corrupted` for manual review.
- Optionally excludes small thumbnail-sized photos with `--exclude-thumbnails`, moving them to `Trash Bin`.
- Optionally detects duplicates using name, size, date, SHA-256, a perceptual visual hash, or a combination of those keys.
- Moves detected duplicates to `Duplicates` for review, or to `Trash Bin` with `--trash-duplicates`.
- Supports `--in-place` to safely rescan an already organized folder and fix newly found corruption or duplicates.
- Never overwrites an existing file; name collisions receive a numeric suffix.
- Supports dry runs and copying instead of moving.

## Example output

```text
F:\Media Organized\
├── Apple iPhone XS\
│   ├── IMG_0001.JPG
│   └── IMG_0002.MOV
├── unknown device\
│   └── recording.mp4
├── Duplicates\
│   └── IMG_0001 (1).JPG
└── Corrupted\
    └── damaged.jpg
```

The camera folder name is taken from the file metadata. The exact name may vary by device and file format, for example `Apple iPhone XS`, `iPhone XS`, or a custom name you provide.

## Requirements

- Windows 10 or newer
- Python 3.11 or newer
- Pillow, installed from `requirements.txt`
- FFmpeg is optional but recommended for video and audio validation

## Installation

1. Install Python from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
2. Open PowerShell or Command Prompt in this repository.
3. Install the image-processing dependency:

   ```powershell
   py -m pip install -r requirements.txt
   ```

### Optional: install FFmpeg

FFmpeg provides `ffprobe`, which lets the organizer inspect video and audio streams and read more metadata.

Using Windows Package Manager:

```powershell
winget install Gyan.FFmpeg.Shared
```

Close and reopen the terminal, then verify the installation:

```powershell
ffprobe -version
```

If `ffprobe` is not installed, videos and audio are reported as `unchecked` rather than being incorrectly labeled as healthy or corrupted.

## Usage

Run the interactive organizer:

```powershell
py media_organizer.py
```

The program asks for:

1. The source folder or drive to scan, such as `C:\`.
2. An existing destination folder or drive, such as `F:\Media Organized`.

It then scans, validates, asks how to name files without camera metadata, and requests final confirmation before changing anything.

### Run and forget (fully unattended)

To run the tool without answering any prompts, provide the folders on the command line and add `--yes`:

```powershell
py media_organizer.py --source F:\ --output F:\Media Organized --yes --dedupe-by sha256,stem,visual --trash-duplicates
```

- `--source` and `--output` skip the two folder prompts.
- `--yes` skips the unknown-device naming prompt (uses `unknown device` automatically) and the final confirmation.

Always test the same command with `--dry-run` first, since an unattended run applies changes immediately with no chance to review.

### Recommended first run

Use a dry run with a small test folder before processing an entire drive:

```powershell
py media_organizer.py --dry-run
```

To preserve the original files and place copies in the destination, use:

```powershell
py media_organizer.py --copy
```

### Already ran the program without any flags?

If you already organized a folder using a plain run (no flags), you don't need to start over. Rerun the tool with `--in-place`, pointing both prompts at that same already organized folder, combined with whichever cleanup flags you need:

```powershell
py media_organizer.py --in-place --dry-run --dedupe-by sha256,stem,visual,audio --trash-duplicates --separate-audio --strip-repaired-suffix --strip-numeric-suffix --exclude-thumbnails
```

Review the dry-run output, then drop `--dry-run` to apply it. Files already in the correct place are skipped automatically; only new corruption, duplicates, thumbnails, and audio files are moved.

### Duplicate detection

Duplicate detection is opt-in. Choose one or more comma-separated keys with `--dedupe-by`:

```powershell
# Recommended: exact file-content matching
py media_organizer.py --dry-run --dedupe-by sha256

# Require every selected key to match
py media_organizer.py --dry-run --dedupe-by name,size,date

# Combine metadata and exact-content checks
py media_organizer.py --dry-run --dedupe-by name,size,sha256
```

Supported keys:

- `name`: filename, compared without case differences.
- `size`: file size in bytes.
- `date`: capture date when available, otherwise the file modification timestamp.
- `sha256`: exact file-content hash, calculated in streaming chunks. Only matches byte-identical files.
- `stem`: filename with recovery-tool noise removed, such as `(deleted <hash>)` segments, backtick-wrapped hash/timestamp tokens, and `_repaired` suffixes. This matches patterns like `DSC00363_2_repaired.jpg` and `DSC00363_30_repaired.jpg` to the same base name `DSC00363`.
- `visual`: two independent perceptual hashes (dHash and aHash) of the photo's content, compared exhaustively against every other photo. Unlike `sha256`, this matches the same photo even if it was resized or re-saved at a different quality, since it compares what the image looks like rather than its exact bytes. A pair is only treated as a duplicate when **both** hashes agree, which reduces false positives. Applies to photos only; videos and audio are not compared. Controlled by `--visual-threshold` (default `24` out of 256 bits per hash; lower is stricter).
- `audio`: song metadata (title, contributing artist, and album artist), read from the audio file's tags via `ffprobe`. Case-insensitive and ignores the filename entirely, so `song_170385735.mp3` and `song (copy).mp3` still match if they're tagged as the same track. Requires a non-empty title; files without a title are never matched by this key. Applies to audio only.

`stem` cannot match files that use entirely different naming schemes for the same photo (for example, a camera-generated name versus a metadata-based name assembled by a recovery tool). `visual` is the reliable option for that case, since it compares the actual image content instead of the filename.

`visual` compares candidate photos within the same folder against each other (further grouped by matching aspect ratio for speed, not as an approximation), rather than across your entire library. This matches how devices are typically organized: it finds the same photo whether it appears near the top or the bottom of a folder like `EVA-L19`, without wrongly merging visually similar photos that happen to sit in a different device's folder. This is intentionally thorough rather than fast, and can take noticeably longer on folders with many photos. Progress is printed per folder while hashing.

When multiple keys are selected, all of them must match. Among files in the same duplicate group, the photo with the highest resolution, or the song with the highest bit rate, is kept in its normal folder; the rest are placed in `Duplicates` (or `Trash Bin` with `--trash-duplicates`). For other duplicates (or when resolution/bit rate can't be compared), the first file found is kept. Use `--dry-run` to review the planned result before moving or copying anything.

To find duplicate songs by metadata and keep only the highest-bit-rate copy:

```powershell
py media_organizer.py --dry-run --dedupe-by audio --trash-duplicates
```

To catch the same photo across different resolutions or recompression, and require it to also carry the same camera:

```powershell
py media_organizer.py --dry-run --dedupe-by visual

# Stricter: only match near-identical visuals AND the same file size
py media_organizer.py --dry-run --dedupe-by visual,size --visual-threshold 12
```

To move duplicates to a separate `Trash Bin` folder instead of `Duplicates`, for manual review and removal:

```powershell
py media_organizer.py --dedupe-by sha256 --trash-duplicates
```

Duplicates are never deleted automatically. Review the `Trash Bin` folder and delete its contents yourself once you are satisfied.

### Cleaning up recovery-tool filenames

Some photo recovery tools append a `_repaired` marker (and a numbered variant such as `_2_repaired`) to filenames. To remove just the `_repaired` keyword while keeping the rest of the filename intact:

```powershell
py media_organizer.py --strip-repaired-suffix
```

For example, `DSC00363_2_repaired.jpg` becomes `DSC00363_2.jpg`.

Some sync or recovery tools instead append a random numeric ID, such as `song_170385735.mp3`. To remove that pattern:

```powershell
py media_organizer.py --strip-numeric-suffix
```

This only strips a trailing separator followed by 4 or more digits, so short/meaningful numbers (like a track number `song_12.mp3`) are left untouched. Both flags can be combined.

### Rerunning on an already organized folder

If earlier organizing left corrupted or duplicate files mixed in with good ones (for example, after a first pass without `--dedupe-by`), rerun the tool with `--in-place` and point both prompts at the same already organized folder:

```powershell
py media_organizer.py --in-place --dry-run --dedupe-by sha256
```

In-place mode allows the source and destination to be the same folder. Files already in their correct location are skipped; newly detected corrupted or duplicate files are moved to `Corrupted` or `Duplicates` without being copied needlessly.

### Separating audio files

By default, audio files are organized by device/camera metadata like photos and videos. To keep all audio together instead:

```powershell
py media_organizer.py --separate-audio
```

All audio files are then placed directly in an `Audio` folder, regardless of embedded metadata.

### Excluding thumbnail-sized photos

Recovered folders sometimes contain small thumbnail copies (for example `160x120` or `256x171`) alongside the full-size photo. To move these out of the way:

```powershell
py media_organizer.py --exclude-thumbnails

# Adjust the size threshold (default: 320 pixels on the longer side)
py media_organizer.py --exclude-thumbnails --thumbnail-max-size 400
```

A photo is treated as a thumbnail when both its width and height are below the threshold. Matching photos are moved to `Trash Bin` for manual review, never deleted automatically.

You can also double-click `run_media_organizer.bat` from Windows Explorer. Add `--dry-run` or `--copy` after the batch file name when launching it from a terminal.

## Processing steps

The workflow is intentionally sequential:

1. **Scan**: find supported media recursively and show progress.
2. **Validate**: inspect image contents and probe video/audio when possible.
3. **Deduplicate**: optionally compare selected file keys and identify duplicates.
4. **Organize**: move or copy files into camera folders after confirmation.

Corrupted files are never deleted. They are moved to the destination's `Corrupted` folder so they can be reviewed separately.

## Supported formats

The script recognizes common formats including:

- Photos: JPG, JPEG, PNG, GIF, BMP, TIFF, WebP, HEIC, HEIF, AVIF, and ICO
- Video: MP4, M4V, MOV, AVI, MKV, WMV, WebM, 3GP, MTS, and M2TS
- Audio: MP3, M4A, AAC, WAV, FLAC, OGG, WMA, and Opus

## Safety and limitations

- Test with `--dry-run` first, especially when scanning an entire drive.
- The source and destination must not be the same folder, and the destination must not be inside the source scan path.
- The script does not overwrite existing files. It creates names such as `photo (1).jpg` when necessary.
- Duplicate detection is not enabled unless `--dedupe-by` is provided.
- `name`, `date`, or `stem` alone can produce broad matches. For safer results, prefer `sha256`/`visual` or combine them with other keys.
- `visual` compares image content, not filenames. It cannot detect cropped, rotated, or heavily edited copies, only the same photo at a different resolution or compression quality.
- `--trash-duplicates` moves files to a `Trash Bin` folder; it does not delete anything automatically.
- `--exclude-thumbnails` also moves matching photos to `Trash Bin`; it does not delete anything automatically.
- `--in-place` is the only way to scan and organize the same folder. Without it, the source and destination must not overlap.
- Image validation can detect unreadable files, pure black or white images, and large uniform blocks from truncated decodes, but no automated check can identify every visually damaged image.
- Video and audio validation requires `ffprobe`. Without it, those files remain `unchecked` and are preserved with the other media.
- Capture dates are not used to create folders. Use Windows Explorer's Date taken, Media created, or Date modified columns to sort the files.

## Project files

```text
media_organizer.py       Main interactive program
requirements.txt         Python dependencies
run_media_organizer.bat  Windows double-click launcher
```

## License

No license has been selected for this project yet. Add a license before redistributing it publicly.
