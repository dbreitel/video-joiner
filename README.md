# Video Joiner

A command-line tool that concatenates multiple video files from a directory into a single file, sorted by filename. Optimized for H.265/HEVC video — preserves original quality whenever possible by avoiding unnecessary re-encoding.

## Features

- **Lossless joining** — when all files share the same codec settings, streams are copied directly with zero quality loss and near-instant speed
- **Automatic re-encoding** — when files have different codecs, resolutions, or framerates, the tool re-encodes to H.265 at CRF 18 (visually lossless)
- **File probing** — every file is inspected with `ffprobe` before joining, displaying codec, resolution, framerate, and duration
- **Live progress** — shows percentage complete with elapsed and total duration during the join
- **Pattern filtering** — select specific files with glob patterns or default to all recognized video formats
- **Error resilience** — unreadable files are skipped with a warning, corrupt probes are caught gracefully, and Ctrl+C terminates cleanly

## Requirements

- **Python** 3.10+
- **ffmpeg** and **ffprobe** (included with ffmpeg)

### Installing ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (with Chocolatey)
choco install ffmpeg
```

## Usage

```
python join.py <directory> [options]
```

### Arguments

| Argument | Description |
|---|---|
| `directory` | Path to the directory containing video files |

### Options

| Option | Description |
|---|---|
| `-o`, `--output PATH` | Output file path. Defaults to `<directory>/joined.mp4` |
| `-p`, `--pattern PATTERN` | Glob pattern to filter files by name (e.g. `"*.mp4"`, `"scene_*.mkv"`) |
| `-h`, `--help` | Show help message |

### Examples

**Join all video files in a directory:**

```bash
python join.py /path/to/videos
```

**Join only MP4 files:**

```bash
python join.py /path/to/videos -p "*.mp4"
```

**Join files matching a naming pattern:**

```bash
python join.py /path/to/videos -p "clip_*.mp4"
```

**Specify output file:**

```bash
python join.py /path/to/videos -o /output/final.mp4
```

**Combine pattern and output:**

```bash
python join.py /path/to/videos -p "scene_*.mkv" -o ~/Desktop/compilation.mp4
```

## How It Works

### 1. Discovery

The tool scans the specified directory for video files sorted alphabetically by filename. Without a pattern, it matches these extensions: `.mp4`, `.mkv`, `.mov`, `.ts`, `.m2ts`, `.hevc`, `.avi`, `.webm`.

### 2. Probing

Each file is inspected with `ffprobe` to extract:

- Video codec (e.g. `hevc`, `h264`)
- Resolution (e.g. `1920x1080`)
- Framerate (e.g. `30/1`)
- Pixel format (e.g. `yuv420p`)
- Duration

Files that cannot be probed are skipped with a warning.

### 3. Compatibility Check

All probed files are compared against the first file. If **all** files share the same codec, resolution, framerate, and pixel format, they are considered compatible.

### 4. Joining

**Compatible files (stream copy):**

Uses ffmpeg's concat demuxer with `-c copy`. This copies raw packets without decoding or encoding, which means:

- No quality loss whatsoever
- Very fast (limited only by disk I/O)
- Minimal CPU and memory usage

**Incompatible files (re-encode):**

Uses ffmpeg's `filter_complex concat` filter to decode, combine, and re-encode:

- **Video:** H.265 with CRF 18 (`-preset medium`) — visually lossless
- **Audio:** AAC at 192 kbps
- **Container:** MP4 with `faststart` for streaming compatibility
- **Tag:** `hvc1` for Apple device compatibility

The tool reports which files caused the mismatch so you can investigate.

## Output Example

### Compatible files (stream copy)

```
Found 3 files, probing...
  clip_001.mp4  [1920x1080 hevc 30/1fps] (2m 15s)
  clip_002.mp4  [1920x1080 hevc 30/1fps] (3m 42s)
  clip_003.mp4  [1920x1080 hevc 30/1fps] (1m 08s)

Total duration: 7m 05s
All files are compatible — using stream copy (no re-encoding).

  Progress: 100.0% (7m 05s / 7m 05s)
Done — /path/to/videos/joined.mp4 (842.3 MB)
```

### Incompatible files (re-encode)

```
Found 3 files, probing...
  intro.mp4     [1280x720 h264 24/1fps] (0m 30s)
  main.mkv      [1920x1080 hevc 30/1fps] (5m 12s)
  outro.mov     [1920x1080 hevc 30/1fps] (0m 15s)

Total duration: 5m 57s
Files have different settings — re-encoding to H.265 (CRF 18).

  Mismatch: intro.mp4 (1280x720 h264 24/1fps)

  Progress:  72.4% (4m 18s / 5m 57s)
```

## File Ordering

Files are sorted alphabetically by filename. To control the join order, use a naming convention with zero-padded numbers:

```
001_intro.mp4
002_chapter1.mp4
003_chapter2.mp4
010_credits.mp4
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `ffmpeg not found` | Install ffmpeg — see [Requirements](#requirements) |
| `No video files found` | Check the directory path and ensure files have recognized extensions, or use `-p` with a matching pattern |
| `Output already exists` | Delete the existing file or use `-o` to specify a different output path |
| Files join but video glitches at cut points | Input files likely have different GOP structures. The tool will stream-copy if codecs match, but container-level differences can cause this. Re-run after modifying one file to force a mismatch, triggering re-encode |
| Re-encoding is slow | This is expected — H.265 encoding is CPU-intensive. CRF 18 with `medium` preset balances quality and speed. For faster encoding at slightly larger file sizes, you can edit `join.py` and change `-preset medium` to `-preset fast` |

## License

MIT
