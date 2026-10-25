"""Small shared helpers: output naming, tool discovery, file categories."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff",
             ".bmp", ".gif", ".ico", ".svg", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v", ".flv",
             ".mpg", ".mpeg", ".ts", ".3gp", ".insv"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aiff", ".aif",
             ".aac", ".wma", ".alac"}
PDF_EXT = {".pdf"}
DOC_EXT = {".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".epub", ".xps",
           ".mobi", ".fb2", ".cbz", ".pptx", ".ppt", ".xlsx", ".xls", ".odp", ".ods"}
SUB_EXT = {".srt", ".vtt"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}


def category(path: str | Path) -> str:
    p = Path(path)
    if p.is_dir():
        return "folder"
    name = p.name.lower()
    if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return "archive"
    ext = p.suffix.lower()
    for cat, exts in (("image", IMAGE_EXT), ("video", VIDEO_EXT), ("audio", AUDIO_EXT),
                      ("pdf", PDF_EXT), ("subtitle", SUB_EXT), ("archive", ARCHIVE_EXT),
                      ("document", DOC_EXT)):
        if ext in exts:
            return cat
    return "other"


def unique_path(path: str | Path) -> Path:
    """Return path, or 'name (1).ext', 'name (2).ext'... if it already exists."""
    p = Path(path)
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    if p.name.lower().endswith(".tar.gz"):
        stem, suffix = p.name[:-7], ".tar.gz"
    i = 1
    while True:
        cand = p.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1


def sibling(src: str | Path, new_ext: str, tag: str = "") -> Path:
    """Output beside the original: photo.heic -> photo.jpg, or photo-compressed.jpg."""
    s = Path(src)
    stem = s.name[:-7] if s.name.lower().endswith(".tar.gz") else s.stem
    if tag:
        stem = f"{stem}-{tag}"
    if not new_ext.startswith("."):
        new_ext = "." + new_ext
    return unique_path(s.with_name(stem + new_ext))


def app_dir() -> Path:
    """Folder the app runs from (works for PyInstaller onefile/onedir and source)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def find_tool(name: str, extra: list[str] | None = None) -> str | None:
    """Locate an external binary: env override, bundled ./bin, then PATH, then known dirs."""
    env = os.environ.get(f"SATSUMA_{name.upper()}")
    if env and Path(env).exists():
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    for base in (app_dir() / "bin", Path(sys.executable).parent / "bin"):
        if (base / exe).exists():
            return str(base / exe)
    found = shutil.which(name)
    if found:
        return found
    for d in extra or []:
        cand = Path(os.path.expandvars(d)) / exe
        if cand.exists():
            return str(cand)
    return None


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
