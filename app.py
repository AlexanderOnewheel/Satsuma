"""Satsuma entry point: tray icon, drag hook, wheel, Send To menu."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QCursor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from .hooks import DragWatcher
from .ops import Op, ops_for
from .ui import JobToast, ResultView, app_icon, ask_params, run_action
from .wheel import MODE_NAMES, Wheel

APP = "Satsuma"
HELP = """How to use Satsuma

Drag files in File Explorer (or on the desktop) and hold Shift.
A wheel appears under your pointer. Drop the files on a format and the
converted copy is saved right beside the original.

  Shift            Convert to another format
  Ctrl + Shift     Tools (compress, trim, resize, strip metadata, merge…)
  Alt + Shift      Actions (copy path, zip, terminal, move, float…)

You can switch between rings mid-drag by changing which keys you hold.
Move off the wheel to cancel. Drop several files at once for batch jobs
(merge PDFs, join clips, combine images into one PDF).

No drag? Right-click files → Send to → Satsuma for the same options as a menu.

Everything runs locally. Nothing is uploaded.
"""


def self_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parent.parent / "satsuma_app.py"
    return [str(pyw if pyw.exists() else exe), str(launcher)]


def dispatch(op: Op, paths: list[Path]) -> None:
    if op.ui:
        run_action(op, paths)
        return
    params = ask_params(op)
    if params is None:
        return
    JobToast(op, paths, params)


def build_menu(paths: list[Path], parent: QMenu | None = None) -> QMenu:
    menu = parent or QMenu()
    for mode in ("convert", "tool", "action"):
        items = ops_for(paths, mode)
        if not items:
            continue
        sub = menu.addMenu(MODE_NAMES[mode])
        for o in items:
            act = sub.addAction(o.label + ("…" if o.params else ""))
            act.triggered.connect(lambda _=False, o=o: dispatch(o, paths))
    return menu


# ---- Windows integration ----------------------------------------------------------

def _ps(script: str) -> bool:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=0x08000000)
    return r.returncode == 0


def install_send_to() -> bool:
    cmd = self_command()
    target, args = cmd[0], " ".join(f'"{a}"' for a in cmd[1:]) + " --menu"
    link = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/SendTo/Satsuma.lnk"
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{l}');"
              "$s.TargetPath='{t}';$s.Arguments='{a}';$s.Description='Convert with Satsuma';"
              "$s.Save()").format(l=str(link).replace("'", "''"), t=target.replace("'", "''"),
                                  a=args.strip().replace("'", "''"))
    return _ps(script)


def set_startup(on: bool) -> None:
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                         winreg.KEY_SET_VALUE)
    if on:
        winreg.SetValueEx(key, APP, 0, winreg.REG_SZ,
                          " ".join(f'"{c}"' for c in self_command()))
    else:
        try:
            winreg.DeleteValue(key, APP)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


def startup_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        winreg.QueryValueEx(key, APP)
        return True
    except OSError:
        return False


# ---- modes ------------------------------------------------------------------

def run_menu_mode(app: QApplication, files: list[str]) -> int:
    """Invoked from Explorer's Send To: show the op menu at the pointer, run, exit."""
    paths = [Path(f) for f in files if Path(f).exists()]
    if not paths:
        return 1
    app.setQuitOnLastWindowClosed(True)
    menu = build_menu(paths)
    chosen = menu.exec(QCursor.pos())
    if chosen is None or not any(w.isVisible() for w in app.topLevelWidgets()):
        return 0
    return app.exec()


def run_tray(app: QApplication) -> int:
    server_name = "satsuma-single-instance"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(200):
        return 0  # already running
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    app.setQuitOnLastWindowClosed(False)
    settings = QSettings("Satsuma", "Satsuma")

    wheel = Wheel()
    wheel.chosen.connect(dispatch)
    watcher = DragWatcher(any_app=settings.value("any_app", "false") == "true")
    watcher.dragStarted.connect(lambda: wheel.arm())
    watcher.dragEnded.connect(wheel.drag_ended)
    watcher.enabled = settings.value("enabled", "true") == "true"
    watcher.start()

    tray = QSystemTrayIcon(app_icon())
    tray.setToolTip("Satsuma: hold Shift while dragging files")
    menu = QMenu()

    def toggle(key: str, attr_setter):
        def f(checked: bool):
            settings.setValue(key, "true" if checked else "false")
            attr_setter(checked)
        return f

    a_enabled = QAction("Enabled", menu, checkable=True, checked=watcher.enabled)
    a_enabled.toggled.connect(toggle("enabled", lambda v: setattr(watcher, "enabled", v)))
    a_any = QAction("Trigger from any app (not just Explorer)", menu, checkable=True,
                    checked=watcher.any_app)
    a_any.toggled.connect(toggle("any_app", lambda v: setattr(watcher, "any_app", v)))
    a_reveal = QAction("Show results in Explorer", menu, checkable=True,
                       checked=settings.value("reveal", "true") == "true")
    a_reveal.toggled.connect(toggle("reveal", lambda v: None))

    def pick_files():
        files, _ = QFileDialog.getOpenFileNames(None, "Choose files for Satsuma")
        if files:
            build_menu([Path(f) for f in files]).exec(QCursor.pos())

    a_pick = QAction("Convert files…", menu, triggered=pick_files)
    a_sendto = QAction("Add to Explorer's “Send to” menu", menu)
    a_sendto.triggered.connect(lambda: tray.showMessage(
        APP, "Added. Right-click files → Send to → Satsuma." if install_send_to()
        else "Couldn't create the Send to shortcut.", app_icon(), 4000))
    a_start = QAction("Start with Windows", menu, checkable=True, checked=startup_enabled())
    a_start.toggled.connect(set_startup)
    a_help = QAction("How to use", menu, triggered=lambda: ResultView(APP, HELP).show())
    a_quit = QAction("Quit", menu, triggered=app.quit)

    for a in (a_enabled, a_any, a_reveal, None, a_pick, a_sendto, a_start, None, a_help,
              a_quit):
        menu.addSeparator() if a is None else menu.addAction(a)
    if os.name != "nt":
        a_sendto.setEnabled(False)
        a_start.setEnabled(False)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: ResultView(APP, HELP).show()
                           if r == QSystemTrayIcon.DoubleClick else None)
    tray.show()
    if settings.value("welcomed", "false") != "true":
        tray.showMessage(APP, "Hold Shift while dragging files in Explorer to open the wheel.",
                         app_icon(), 6000)
        settings.setValue("welcomed", "true")
    app.aboutToQuit.connect(watcher.stop)
    app._keep = (wheel, watcher, tray, menu, server)  # keep references alive
    return app.exec()


def main() -> int:
    if os.name == "nt":
        try:  # let Explorer, taskbar and toasts group us under our own app id
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Satsuma.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setWindowIcon(app_icon())
    args = sys.argv[1:]
    if args and args[0] == "--menu":
        return run_menu_mode(app, args[1:])
    return run_tray(app)


if __name__ == "__main__":
    sys.exit(main())
