# Satsuma
A Windows take on Tangerine for Mac. Drag files in File Explorer, hold **Shift**, and a wheel of formats appears under your pointer. Drop on a format and the converted copy lands beside the original. Everything runs locally.
![wheels](docs/wheels.png)

| Hold while dragging | Ring | What's on it |
|---|---|---|
| Shift | Convert | Images: JPG PNG HEIC WEBP TIFF BMP GIF ICO SVG PDF · Video: MP4 MOV MKV WEBM AVI WMV GIF MP3 · Audio: MP3 M4A WAV FLAC OGG OPUS AIFF · PDF: pages to PNG/JPG/WEBP, TXT, DOCX, EPUB · Docs: PDF TXT DOCX EPUB · SRT↔VTT · Archives: extract, ZIP, TAR.GZ |
| Ctrl + Shift | Tools | Compress, compress to exact size or %, trim, speed, split, join, snapshot, crop to 9:16/1:1, rotate, mute, bleep, normalize loudness, mono/stereo, resize, B&W, show/strip metadata, read QR, merge/split/compress PDFs, extract PDF images, images → one PDF, collage |
| Alt + Shift | Actions | Copy path, copy file, open terminal here, show in Explorer, move to, copy to, zip, float, open with |

You can change which keys you hold mid-drag to switch rings. Moving off the wheel cancels. Multi-file drops run as a batch, and merge/join/combine ops appear when you drop 2+ files. Ops that need input (target size, trim points) ask once and remember your last answer.

No drag? Right-click files → **Send to → Satsuma** gives the same options as a menu. Turn this on once from the tray menu.

## Run it

```powershell
# From this folder, in PowerShell (needs Python 3.10+):
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1 -RunOnly      # set up a venv, fetch FFmpeg, run from source
.\build.ps1               # build dist\Satsuma\Satsuma.exe (FFmpeg bundled)
```

Then use the tray icon to switch on **Start with Windows** and **Add to "Send to" menu**.

Some things are optional:
- **Office files → PDF** needs LibreOffice or Microsoft Office. Word, PowerPoint and Excel are driven via COM.
- **RAR/7z** needs 7-Zip. Windows 11's built-in `tar` is used as a fallback.
- **Read QR** needs `opencv-python-headless`, which is in `requirements.txt`.

## How Tangerine works, and how this maps to Windows

| Piece | Tangerine (macOS, inferred) | Satsuma (Windows) |
|---|---|---|
| Notice a drag | Global `NSEvent` monitor for left-mouse-dragged + modifier flags | `WH_MOUSE_LL` low-level hook on its own thread (`hooks.py`). It only arms when the press starts in Explorer, the desktop or a file dialog, then fires once the pointer passes the system drag threshold with Shift down. It never swallows input. |
| Show the wheel | Borderless, non-activating `NSPanel` at the cursor, registered as a drag destination | Frameless, non-activating, always-on-top Qt window that accepts drops (`wheel.py`). It's painted at 1/255 alpha until a real OLE file drag enters, so a Shift+rubber-band selection never shows a phantom wheel. |
| Know the file type | Read file URLs from the drag pasteboard | `dragEnterEvent` reads the dropped paths. The wheel fills from `ops.ops_for(paths, mode)`. |
| Switch rings | Modifier flags | Modifier state reported with each OLE `DragOver`, read live in `dragMoveEvent` |
| Convert | AVFoundation / ImageIO / PDFKit | FFmpeg (video/audio), Pillow + pillow-heif (images), PyMuPDF (PDF, EPUB, XPS), python-docx, zipfile/tarfile/7-Zip |
| Progress + reveal | Small progress window, then reveal in Finder | Toast card in the bottom-right with Cancel (kills FFmpeg), then `explorer /select,` |
| Menu bar app | `LSUIElement` menu bar extra | System tray icon, `HKCU\…\Run` for startup, single instance via `QLocalServer` |

### Windows gotchas this handles
- **Shift+drag means "move" in Explorer.** Every drop is accepted as `CopyAction`, so your originals are never deleted.
- **Copy file to clipboard** sets `Preferred DropEffect = COPY` so that pasting in Explorer copies rather than moves.
- **Elevation:** Windows blocks drag-drop between admin and non-admin windows (UIPI). Run Satsuma at the same level as Explorer, i.e. normally, not as admin.
- **High-DPI / multi-monitor:** the wheel is positioned from Qt's logical cursor position and clamped to the current screen.

## Layout

```
satsuma/
  hooks.py   global drag detection (Win32 low-level mouse hook via ctypes)
  wheel.py   radial drop wheel
  ops.py     every convert/tool/action + registry (82 ops)
  media.py   FFmpeg runner with progress + cancel, codec presets
  ui.py      param prompt, progress toast, result viewer, Float shelf, shell helpers
  app.py     tray, settings, Send To menu mode (--menu files…)
tests/       pytest suite that runs every op against generated samples
```

Adding an operation means writing one function in `ops.py` with an `@op(...)` decorator. The wheel and the Send To menu pick it up automatically.

```powershell
.\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest tests -q
```
