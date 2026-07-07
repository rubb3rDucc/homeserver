#!/usr/bin/env python3
"""
Station ingest processor.

Watches drop folders under /station/incoming, and for each new video:
  - "split" folders (e.g. commercials): finds black-frame gaps with ffmpeg
    blackdetect, cuts the file into individual spots (trimming the black),
    then normalizes loudness + resolution/fps on each spot.
  - "normalize" folders (e.g. bumpers/idents): normalizes the whole file,
    no splitting.

Output goes to /station/library/<type>/, which ErsatzTV points its filler
libraries at. Originals are archived to /station/processed/ so they are not
re-processed. Nothing is deleted; the archive is safe to clear by hand.

Pure stdlib + the ffmpeg/ffprobe binaries. No Python deps to install.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Config (all overridable via environment in docker-compose)
# --------------------------------------------------------------------------- #
STATION = Path(os.environ.get("STATION_DIR", "/station"))
INCOMING = STATION / "incoming"
LIBRARY = STATION / "library"
PROCESSED = STATION / "processed"
LOGS = STATION / "logs"

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))

TARGET_WIDTH = int(os.environ.get("TARGET_WIDTH", "1920"))
TARGET_HEIGHT = int(os.environ.get("TARGET_HEIGHT", "1080"))
TARGET_FPS = os.environ.get("TARGET_FPS", "30")

LOUDNORM_I = os.environ.get("LOUDNORM_I", "-16")
LOUDNORM_TP = os.environ.get("LOUDNORM_TP", "-1.5")
LOUDNORM_LRA = os.environ.get("LOUDNORM_LRA", "11")

MIN_SPOT = float(os.environ.get("MIN_SPOT_SECONDS", "5"))
MAX_SPOT = float(os.environ.get("MAX_SPOT_SECONDS", "180"))

# blackdetect sensitivity: min black duration (s) and pixel-black threshold.
BLACK_MIN_DUR = os.environ.get("BLACK_MIN_DUR", "0.10")
BLACK_PIX_TH = os.environ.get("BLACK_PIX_TH", "0.10")

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m4v",
    ".flv", ".wmv", ".mpg", ".mpeg", ".m2ts",
}

# Which incoming subfolders to watch and how to treat them.
#   mode "split"     -> blackdetect + cut into spots, then normalize each
#   mode "normalize" -> normalize the whole file as one clip
WATCH = [
    ("commercials", "split"),
    ("bumpers", "normalize"),
]

# --------------------------------------------------------------------------- #
# Logging: to stdout (docker logs) and to /station/logs/processor.log
# --------------------------------------------------------------------------- #
LOGS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS / "processor.log"),
    ],
)
log = logging.getLogger("station")


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #
def ffprobe_duration(src: Path) -> float:
    """Return media duration in seconds, or 0.0 if it can't be read."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return 0.0


_BLACK_RE = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)")


def detect_black_regions(src: Path):
    """Return a list of (start, end) black regions detected in the file."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(src),
            "-vf", f"blackdetect=d={BLACK_MIN_DUR}:pix_th={BLACK_PIX_TH}",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    regions = [(float(a), float(b)) for a, b in _BLACK_RE.findall(proc.stderr)]
    return regions


def content_segments(duration: float, black_regions):
    """
    Turn black regions into the content spans between them (black trimmed off).
    Returns a list of (start, end) tuples.
    """
    segments = []
    cursor = 0.0
    for bs, be in sorted(black_regions):
        if bs - cursor > 0.01:
            segments.append((cursor, bs))
        cursor = max(cursor, be)
    if duration - cursor > 0.01:
        segments.append((cursor, duration))
    return segments


def normalize(src: Path, dst: Path, start=None, dur=None) -> bool:
    """Re-encode [start, start+dur] (or whole file) to the channel standard."""
    vf = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={TARGET_FPS}"
    )
    af = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += [
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg failed for %s: %s", dst.name, result.stderr.strip()[-500:])
        dst.unlink(missing_ok=True)
        return False
    return True


# --------------------------------------------------------------------------- #
# Per-file processing
# --------------------------------------------------------------------------- #
def unique_path(path: Path) -> Path:
    """Avoid clobbering: foo.mp4 -> foo_1.mp4 -> foo_2.mp4 ..."""
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def archive(src: Path, kind: str):
    dest_dir = PROCESSED / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(unique_path(dest_dir / src.name)))


def process_split(src: Path, out_dir: Path):
    duration = ffprobe_duration(src)
    if duration <= 0:
        log.warning("skip (unreadable duration): %s", src.name)
        return
    black = detect_black_regions(src)
    segments = content_segments(duration, black)
    log.info("%s: %.0fs, %d black gap(s) -> %d segment(s)",
             src.name, duration, len(black), len(segments))

    single = len(segments) <= 1
    kept = 0
    for idx, (start, end) in enumerate(segments, 1):
        seg_dur = end - start
        if seg_dur < MIN_SPOT:
            log.info("  drop segment %d (%.1fs < min %.0fs)", idx, seg_dur, MIN_SPOT)
            continue
        if seg_dur > MAX_SPOT and not single:
            log.info("  drop segment %d (%.1fs > max %.0fs)", idx, seg_dur, MAX_SPOT)
            continue
        if seg_dur > MAX_SPOT and single:
            log.warning("  no black gaps found; keeping whole %.0fs clip as one "
                        "spot (may need manual splitting): %s", seg_dur, src.name)
        dst = unique_path(out_dir / f"{src.stem}_spot{idx:02d}.mp4")
        if normalize(src, dst, start=start, dur=seg_dur):
            kept += 1
            log.info("  wrote %s (%.1fs)", dst.name, seg_dur)
    log.info("%s: produced %d spot(s)", src.name, kept)


def process_normalize(src: Path, out_dir: Path):
    dst = unique_path(out_dir / f"{src.stem}.mp4")
    if normalize(src, dst):
        log.info("normalized %s -> %s", src.name, dst.name)


def process_file(src: Path, kind: str, mode: str):
    out_dir = LIBRARY / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("processing [%s/%s]: %s", kind, mode, src.name)
    try:
        if mode == "split":
            process_split(src, out_dir)
        else:
            process_normalize(src, out_dir)
        archive(src, kind)
    except Exception:  # keep the daemon alive on any single-file failure
        log.exception("error processing %s", src.name)


# --------------------------------------------------------------------------- #
# Watch loop (poll-based: robust across bind mounts; only acts on files whose
# size has been stable for one interval, so in-flight downloads are skipped)
# --------------------------------------------------------------------------- #
def main():
    for kind, _ in WATCH:
        (INCOMING / kind).mkdir(parents=True, exist_ok=True)
        (LIBRARY / kind).mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    log.info("station-processor up. watching %s (every %ss). target %sx%s@%sfps, "
             "loudnorm I=%s TP=%s LRA=%s",
             INCOMING, POLL_INTERVAL, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS,
             LOUDNORM_I, LOUDNORM_TP, LOUDNORM_LRA)

    seen_sizes = {}  # path -> last observed size
    while True:
        for kind, mode in WATCH:
            watch_dir = INCOMING / kind
            for src in sorted(watch_dir.iterdir()) if watch_dir.exists() else []:
                if not src.is_file() or src.suffix.lower() not in VIDEO_EXTS:
                    continue
                if src.name.startswith("."):
                    continue
                try:
                    size = src.stat().st_size
                except FileNotFoundError:
                    continue
                key = str(src)
                if seen_sizes.get(key) != size:
                    # New or still-growing file: remember size, wait one cycle.
                    seen_sizes[key] = size
                    continue
                process_file(src, kind, mode)
                seen_sizes.pop(key, None)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
