#!/usr/bin/env python3
"""Concatenate H.265 videos from a directory into a single file."""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".ts", ".m2ts", ".hevc", ".avi", ".webm"}


def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
    except FileNotFoundError:
        sys.exit("Error: ffmpeg/ffprobe not found. Install them (e.g. brew install ffmpeg).")


def find_videos(directory: Path, pattern: str | None) -> list[Path]:
    if pattern:
        files = [
            f
            for f in directory.iterdir()
            if f.is_file() and fnmatch.fnmatch(f.name, pattern)
        ]
    else:
        files = [
            f
            for f in directory.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ]
    files.sort(key=lambda f: f.name)
    return files


def probe_video(path: Path) -> dict | None:
    """Extract codec info from a video file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path.as_posix(),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"  Warning: cannot probe {path.name}: {e}", file=sys.stderr)
        return None

    video_info = None
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_info is None:
            video_info = {
                "codec": stream.get("codec_name", ""),
                "width": int(stream.get("width", 0)),
                "height": int(stream.get("height", 0)),
                "fps": stream.get("r_frame_rate", ""),
                "profile": stream.get("profile", ""),
                "pix_fmt": stream.get("pix_fmt", ""),
                "duration": float(data.get("format", {}).get("duration", 0)),
            }
        elif stream.get("codec_type") == "audio":
            has_audio = True

    if video_info:
        video_info["has_audio"] = has_audio
    return video_info


def check_compatibility(probes: list[dict]) -> bool:
    """Return True if all files can be concat-copied without re-encoding."""
    if not probes:
        return False
    ref = probes[0]
    for p in probes[1:]:
        if (
            p["codec"] != ref["codec"]
            or p["width"] != ref["width"]
            or p["height"] != ref["height"]
            or p["fps"] != ref["fps"]
            or p["pix_fmt"] != ref["pix_fmt"]
            or p["has_audio"] != ref["has_audio"]
        ):
            return False
    return True


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def parse_ffmpeg_progress(line: str, total_duration: float) -> str | None:
    """Parse ffmpeg stderr for time= progress and return a status string."""
    match = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", line)
    if not match:
        return None
    h, m, s, _ = (int(g) for g in match.groups())
    current = h * 3600 + m * 60 + s
    if total_duration > 0:
        pct = min(current / total_duration * 100, 100)
        return f"\r  Progress: {pct:5.1f}% ({format_duration(current)} / {format_duration(total_duration)})"
    return f"\r  Progress: {format_duration(current)}"


def run_ffmpeg(cmd: list[str], total_duration: float) -> None:
    """Run ffmpeg with live progress output."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for line in iter(process.stderr.readline, ""):
            status = parse_ffmpeg_progress(line, total_duration)
            if status:
                print(status, end="", flush=True)
        process.wait()
        print()  # newline after progress
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        sys.exit("\nAborted.")

    if process.returncode != 0:
        sys.exit(f"ffmpeg failed with exit code {process.returncode}")


def join_copy(videos: list[Path], output: Path, total_duration: float) -> None:
    """Join via concat demuxer with stream copy (no re-encoding)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as concat_list:
        for video in videos:
            escaped = str(video.resolve()).replace("'", "'\\''")
            concat_list.write(f"file '{escaped}'\n")
        concat_list_path = concat_list.name

    cmd = [
        "ffmpeg", "-hide_banner",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output.as_posix(),
    ]

    try:
        run_ffmpeg(cmd, total_duration)
    finally:
        Path(concat_list_path).unlink(missing_ok=True)


def join_reencode(
    videos: list[Path], probes: list[dict], output: Path, total_duration: float
) -> None:
    """Join via ffmpeg filter_complex concat — re-encodes to H.265.

    Handles mixed resolutions/framerates by scaling and resampling each input
    to match the highest resolution and framerate found across all files.
    Generates silent audio for inputs that have no audio stream.
    """
    # Determine target resolution and framerate from the largest/fastest input
    target_w = max(p["width"] for p in probes)
    target_h = max(p["height"] for p in probes)
    target_fps = max(probes, key=_parse_fps)["fps"]
    has_any_audio = any(p["has_audio"] for p in probes)

    inputs = []
    for v in videos:
        inputs.extend(["-i", v.as_posix()])

    n = len(videos)
    filter_parts = []

    for i, p in enumerate(probes):
        # Scale and set framerate for each video stream
        filter_parts.append(
            f"[{i}:v:0]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={target_fps},setsar=1[v{i}]"
        )

        if has_any_audio:
            if p["has_audio"]:
                filter_parts.append(f"[{i}:a:0]aresample=44100[a{i}]")
            else:
                # Generate silent audio matching this clip's duration
                filter_parts.append(
                    f"anullsrc=r=44100:cl=stereo[silence{i}];"
                    f"[silence{i}]atrim=duration={p['duration']}[a{i}]"
                )

    # Build concat input string — must be interleaved: [v0][a0][v1][a1]...
    if has_any_audio:
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
        concat_str = f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]"
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        concat_str = f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"

    filter_str = ";".join(filter_parts) + ";" + concat_str

    cmd = [
        "ffmpeg", "-hide_banner",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[outv]",
    ]

    if has_any_audio:
        cmd.extend(["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"])

    cmd.extend([
        "-c:v", "libx265",
        "-crf", "18",
        "-preset", "medium",
        "-movflags", "+faststart",
        "-tag:v", "hvc1",
        output.as_posix(),
    ])

    run_ffmpeg(cmd, total_duration)


def _parse_fps(probe: dict) -> float:
    """Parse fractional fps string like '30/1' into a float for comparison."""
    try:
        num, den = probe["fps"].split("/")
        return int(num) / int(den)
    except (ValueError, ZeroDivisionError):
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join video files from a directory into one file."
    )
    parser.add_argument("directory", type=Path, help="Directory containing video files")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: <directory>/joined.mp4)",
    )
    parser.add_argument(
        "-p", "--pattern",
        type=str,
        default=None,
        help="Filename glob pattern to filter files (e.g. 'scene_*.mp4')",
    )
    args = parser.parse_args()

    if not args.directory.is_dir():
        sys.exit(f"Error: {args.directory} is not a directory.")

    check_ffmpeg()

    videos = find_videos(args.directory, args.pattern)
    if not videos:
        sys.exit(f"No video files found in {args.directory}"
                 + (f" matching '{args.pattern}'" if args.pattern else ""))

    output = args.output or args.directory / "joined.mp4"
    if output.exists():
        sys.exit(f"Error: {output} already exists. Remove it or use -o to specify another path.")

    # Probe all files
    print(f"Found {len(videos)} files, probing...")
    probes: list[dict] = []
    skipped: list[Path] = []
    for v in videos:
        info = probe_video(v)
        if info:
            probes.append(info)
            dur = format_duration(info["duration"])
            audio_tag = "" if info["has_audio"] else " no-audio"
            print(f"  {v.name}  [{info['width']}x{info['height']} {info['codec']} "
                  f"{info['fps']}fps{audio_tag}] ({dur})")
        else:
            skipped.append(v)
            print(f"  {v.name}  [SKIPPED — could not probe]")

    if skipped:
        videos = [v for v in videos if v not in skipped]
        if not videos:
            sys.exit("Error: no valid video files after probing.")
        print(f"\nSkipping {len(skipped)} unreadable file(s).")

    total_duration = sum(p["duration"] for p in probes)
    print(f"\nTotal duration: {format_duration(total_duration)}")

    compatible = check_compatibility(probes)
    if compatible:
        print("All files are compatible — using stream copy (no re-encoding).\n")
        join_copy(videos, output, total_duration)
    else:
        print("Files have different settings — re-encoding to H.265 (CRF 18).\n")
        ref = probes[0]
        for i, p in enumerate(probes):
            diffs = []
            if p["codec"] != ref["codec"]:
                diffs.append(f"codec: {p['codec']}")
            if p["width"] != ref["width"] or p["height"] != ref["height"]:
                diffs.append(f"res: {p['width']}x{p['height']}")
            if p["fps"] != ref["fps"]:
                diffs.append(f"fps: {p['fps']}")
            if p["has_audio"] != ref["has_audio"]:
                diffs.append("no audio" if not p["has_audio"] else "has audio")
            if diffs:
                print(f"  Mismatch: {videos[i].name} ({', '.join(diffs)})")
        print()
        join_reencode(videos, probes, output, total_duration)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Done — {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
