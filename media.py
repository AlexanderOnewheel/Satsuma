"""FFmpeg wrapper with progress reporting and cancellation."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from .util import find_tool

ProgressFn = Callable[[float], None]

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
_FFMPEG_DIRS = [r"%ProgramFiles%\ffmpeg\bin", r"%LOCALAPPDATA%\Microsoft\WinGet\Links",
                r"C:\ffmpeg\bin", r"%USERPROFILE%\scoop\shims"]


class Cancelled(Exception):
    pass


class MissingTool(RuntimeError):
    pass


def ffmpeg() -> str:
    exe = find_tool("ffmpeg", _FFMPEG_DIRS)
    if not exe:
        raise MissingTool("FFmpeg not found. Put ffmpeg.exe in Satsuma\\bin or run "
                          "'winget install Gyan.FFmpeg'.")
    return exe


def ffprobe() -> str:
    exe = find_tool("ffprobe", _FFMPEG_DIRS)
    if not exe:
        raise MissingTool("ffprobe not found (it ships alongside ffmpeg).")
    return exe


def probe(path: str | Path) -> dict:
    out = subprocess.run([ffprobe(), "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


def duration(path: str | Path) -> float:
    try:
        return float(probe(path)["format"].get("duration") or 0)
    except Exception:
        return 0.0


def has_stream(path: str | Path, kind: str) -> bool:
    try:
        return any(s.get("codec_type") == kind for s in probe(path)["streams"])
    except Exception:
        return False


class Runner:
    """Runs one ffmpeg command at a time; cancel() kills it from another thread."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def run(self, args: list[str], total_seconds: float = 0.0,
            progress: ProgressFn | None = None) -> None:
        if self.cancelled:
            raise Cancelled()
        cmd = [ffmpeg(), "-hide_banner", "-y", "-nostdin", "-loglevel", "error",
               "-progress", "pipe:1", "-nostats", *args]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, errors="replace", creationflags=_NO_WINDOW)
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if progress and total_seconds > 0 and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    progress(min(0.999, us / 1e6 / total_seconds))
                except ValueError:
                    pass
        err = self.proc.stderr.read() if self.proc.stderr else ""
        code = self.proc.wait()
        if self.cancelled:
            raise Cancelled()
        if code != 0:
            raise RuntimeError(err.strip().splitlines()[-1] if err.strip() else
                               f"ffmpeg exited with {code}")


# ---- codec presets -----------------------------------------------------------

VIDEO_PRESETS: dict[str, list[str]] = {
    "mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k"],
    "mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
             "-c:a", "libopus", "-b:a", "128k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-q:a", "3"],
    "wmv": ["-c:v", "wmv2", "-q:v", "3", "-c:a", "wmav2", "-b:a", "160k"],
}

AUDIO_PRESETS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
    "aiff": ["-c:a", "pcm_s16be"],
}

GIF_FILTER = ("fps={fps},scale='min({w},iw)':-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a")
