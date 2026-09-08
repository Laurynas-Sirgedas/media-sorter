# Media Organizer for Windows

An interactive command-line tool for recovering and organizing photos, videos, and audio after a device or drive migration.

The organizer scans a folder or an entire drive, validates media, reads camera metadata when available, and places files directly into camera-model folders. There are no year or month subfolders, so Windows Explorer can sort each folder by date.

## Features

- Recursively scans folders and drives for common photo, video, and audio formats.
- Displays scan and validation progress, including file counts.
- Validates images by decoding their contents, checking dimensions, and detecting pure black or white images.
- Uses `ffprobe` for video and audio probing when FFmpeg is installed.
- Reads camera maker and model metadata, such as `Apple iPhone XS`.
- Prompts for a folder name when media has no camera metadata.
- Places files with missing camera metadata in `unknown camera` by default.
- Moves unreadable media to `Corrupted` for manual review.
- Optionally detects duplicates using name, size, date, SHA-256, or a combination of those keys.
- Moves detected duplicates to `Duplicates` for review instead of deleting them.
- Never overwrites an existing file; name collisions receive a numeric suffix.
- Supports dry runs and copying instead of moving.

## Example output

```text
F:\Media Organized\
├── Apple iPhone XS\
│   ├── IMG_0001.JPG
│   └── IMG_0002.MOV
├── unknown camera\
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

### Recommended first run

Use a dry run with a small test folder before processing an entire drive:

```powershell
py media_organizer.py --dry-run
```

To preserve the original files and place copies in the destination, use:

```powershell
py media_organizer.py --copy
```

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
- `sha256`: exact file-content hash, calculated in streaming chunks.

When multiple keys are selected, all of them must match. The first file found is kept in its normal camera folder; later matching files are placed in `Duplicates`. Duplicate files are never deleted. Use `--dry-run` to review the planned result before moving or copying anything.

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
- `name` or `date` alone can produce broad matches. For safer results, prefer `sha256` or combine it with other keys.
- Image validation can detect unreadable files and pure black or white images, but no automated check can identify every visually damaged image.
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
