"""Global drag detection for Windows.

A low-level mouse hook (WH_MOUSE_LL) runs on its own thread. When the left
button goes down inside File Explorer or on the desktop, then moves past the
system drag threshold while a trigger modifier (Shift by default) is held,
we emit dragStarted. The app answers by placing the drop wheel under the
pointer. The hook never blocks or swallows input; it only watches.
"""
from __future__ import annotations

import ctypes
import os
import threading

from PySide6.QtCore import QObject, Signal

EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW",
                    "#32770"}  # #32770 = common file dialogs (Open/Save As)

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WH_MOUSE_LL = 14
GA_ROOT = 2
SM_CXDRAG, SM_CYDRAG = 68, 69


class DragWatcher(QObject):
    dragStarted = Signal()
    dragEnded = Signal()

    def __init__(self, any_app: bool = False) -> None:
        super().__init__()
        self.any_app = any_app      # True = trigger from any app, not just Explorer
        self.enabled = True
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    # -- lifecycle --
    def start(self) -> None:
        if os.name != "nt":
            return  # drag hook is Windows-only; Send To / tray menu still work
        self._thread = threading.Thread(target=self._loop, name="satsuma-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT

    # -- hook thread --
    def _loop(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                             wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        dx = user32.GetSystemMetrics(SM_CXDRAG) or 4
        dy = user32.GetSystemMetrics(SM_CYDRAG) or 4
        state = {"down": None, "eligible": False, "active": False}
        buf = ctypes.create_unicode_buffer(64)

        def key(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def root_class(pt) -> str:
            hwnd = user32.WindowFromPoint(pt)
            root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
            if not root:
                return ""
            user32.GetClassNameW(root, buf, 64)
            return buf.value

        def proc(n_code, w_param, l_param):
            try:
                if n_code == 0:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if w_param == WM_LBUTTONDOWN:
                        state["down"] = (info.pt.x, info.pt.y)
                        state["active"] = False
                        state["eligible"] = self.enabled and (
                            self.any_app or root_class(info.pt) in EXPLORER_CLASSES)
                    elif w_param == WM_MOUSEMOVE and state["down"] and state["eligible"] \
                            and not state["active"]:
                        x0, y0 = state["down"]
                        if (abs(info.pt.x - x0) > dx * 2 or abs(info.pt.y - y0) > dy * 2) \
                                and key(VK_SHIFT):
                            state["active"] = True
                            self.dragStarted.emit()
                    elif w_param == WM_LBUTTONUP:
                        if state["active"]:
                            self.dragEnded.emit()
                        state.update(down=None, eligible=False, active=False)
            except Exception:
                pass  # never let an exception escape into the hook chain
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(proc)  # keep a reference so it isn't garbage collected
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        if not hook:
            return
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)
