"""Windows向け透過時計。Python 3.9以降、追加の実行時依存なし。"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import colorchooser, font as tkfont, messagebox, simpledialog

APP_NAME = "DigitalClockOverlay"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "app.log"
TRANSPARENT_COLOR = "#000001"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_CONFIG = {
    "font_color": "#00FF00", "font_size": 80,
    "pos_x": 100, "pos_y": 100, "autostart": False,
    "font_family": "DSEG7 Classic Mini Light",
}
LOGGER = logging.getLogger(APP_NAME)


def normalize_config(data):
    cfg = DEFAULT_CONFIG.copy()
    if not isinstance(data, dict):
        return cfg
    for key in ("pos_x", "pos_y"):
        value = data.get(key)
        if type(value) is int and -100000 <= value <= 100000:
            cfg[key] = value
    size = data.get("font_size")
    if type(size) is int and 10 <= size <= 300:
        cfg["font_size"] = size
    for key in ("font_color", "font_family"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            cfg[key] = value.strip()
    if type(data.get("autostart")) is bool:
        cfg["autostart"] = data["autostart"]
    return cfg


def load_config():
    try:
        return normalize_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))
    except FileNotFoundError:
        return DEFAULT_CONFIG.copy()
    except (OSError, ValueError):
        LOGGER.exception("設定の読み込みに失敗。既定値で起動します")
        return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=CONFIG_DIR,
            prefix="config-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, CONFIG_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_logging():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    except OSError:
        # ログ保存不可でも時計自体の起動は妨げない。
        LOGGER.addHandler(logging.NullHandler())


def startup_command():
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    return subprocess.list2cmdline([str(interpreter), str(Path(__file__).resolve())])


def set_autostart(enabled):
    import winreg
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def read_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            command, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(command)
    except FileNotFoundError:
        return False


def work_areas():
    user32 = ctypes.windll.user32
    areas = []

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL

    @callback_type
    def visit(monitor, dc, rect, data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            areas.append((r.left, r.top, r.right, r.bottom))
        return True

    if not user32.EnumDisplayMonitors(None, None, visit, 0) or not areas:
        raise RuntimeError("モニターの作業領域を取得できませんでした")
    return areas


def clamp_position(x, y, width, height, areas):
    candidates = []
    for left, top, right, bottom in areas:
        nx = max(left, min(x, max(left, right - width)))
        ny = max(top, min(y, max(top, bottom - height)))
        candidates.append(((nx - x) ** 2 + (ny - y) ** 2, nx, ny))
    _, x, y = min(candidates)
    return x, y


def move_window(root, x, y):
    # Tk geometryの右端基準の負符号と、仮想画面の負座標を区別する。
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
    if not hwnd or not user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
        raise RuntimeError("ウィンドウ位置の変更に失敗しました")


class ClockApp:
    def __init__(self, root, diagnostic=False, reset_position=False):
        self.root = root
        self.diagnostic = diagnostic
        self.cfg = load_config()
        self.timer = None
        self.drag_origin = None
        root.withdraw()
        root.title(APP_NAME)
        root.report_callback_exception = self.callback_error
        root.attributes("-topmost", True)
        root.overrideredirect(not diagnostic)
        background = "#202020" if diagnostic else TRANSPARENT_COLOR
        root.configure(bg=background)
        if not diagnostic:
            try:
                root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
            except tk.TclError:
                LOGGER.exception("背景透過を設定できません。通常背景で続行します")
                background = "#202020"
                root.configure(bg=background)
        self.validate_appearance()
        self.label = tk.Label(
            root, text="00:00", font=self.clock_font(),
            fg=self.cfg["font_color"], bg=background, padx=8, pady=4,
        )
        self.label.pack()
        self.build_menu()
        self.label.bind("<ButtonPress-1>", self.start_drag)
        self.label.bind("<B1-Motion>", self.drag)
        self.label.bind("<ButtonRelease-1>", self.end_drag)
        root.bind("<Button-3>", self.show_menu)
        root.bind("<Escape>", lambda event: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()
        root.update_idletasks()
        x, y = (100, 100) if reset_position else (self.cfg["pos_x"], self.cfg["pos_y"])
        self.place_visible(x, y)
        root.deiconify()
        root.lift()
        root.after_idle(lambda: self.place_visible(x, y))

    def validate_appearance(self):
        try:
            color = self.root.winfo_rgb(self.cfg["font_color"])
            if color == self.root.winfo_rgb(TRANSPARENT_COLOR):
                raise ValueError("文字色が透過色と同じです")
        except (tk.TclError, ValueError):
            LOGGER.warning("文字色が不正なため既定色へ戻します")
            self.cfg["font_color"] = DEFAULT_CONFIG["font_color"]
        families = {name.casefold() for name in tkfont.families(self.root)}
        if self.cfg["font_family"].casefold() not in families:
            LOGGER.warning("指定フォントなし: %s", self.cfg["font_family"])
            self.cfg["font_family"] = "Consolas" if "consolas" in families else "Arial"

    def clock_font(self):
        return self.cfg["font_family"], self.cfg["font_size"]

    def build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="文字色を変更", command=self.change_color)
        self.menu.add_command(label="文字サイズを変更", command=self.change_size)
        try:
            enabled = read_autostart()
        except OSError:
            LOGGER.exception("自動起動設定を取得できません")
            enabled = self.cfg["autostart"]
        self.cfg["autostart"] = enabled
        self.autostart = tk.BooleanVar(master=self.root, value=enabled)
        self.menu.add_checkbutton(
            label="Windows起動時に自動起動", variable=self.autostart,
            command=self.toggle_autostart,
        )
        self.menu.add_separator()
        self.menu.add_command(label="位置とサイズを保存", command=self.persist_position)
        self.menu.add_command(label="位置をリセット", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="終了", command=self.close)

    def place_visible(self, x, y):
        self.root.update_idletasks()
        x, y = clamp_position(
            x, y, self.root.winfo_reqwidth(), self.root.winfo_reqheight(), work_areas()
        )
        move_window(self.root, x, y)

    def show_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def persist(self):
        try:
            save_config(self.cfg)
        except OSError as exc:
            LOGGER.exception("設定保存に失敗")
            messagebox.showerror("保存エラー", str(exc), parent=self.root)

    def persist_position(self):
        self.cfg.update(pos_x=self.root.winfo_x(), pos_y=self.root.winfo_y())
        self.persist()

    def reset_position(self):
        self.place_visible(100, 100)
        self.persist_position()

    def change_color(self):
        _, color = colorchooser.askcolor(
            color=self.cfg["font_color"], title="文字色を選択", parent=self.root
        )
        if not color:
            return
        if self.root.winfo_rgb(color) == self.root.winfo_rgb(TRANSPARENT_COLOR):
            messagebox.showwarning("文字色", "この色は背景透過に使うため選択できません。", parent=self.root)
            return
        self.cfg["font_color"] = color
        self.label.configure(fg=color)
        self.persist()

    def change_size(self):
        size = simpledialog.askinteger(
            "文字サイズ", "文字サイズを入力してください (10〜300):",
            initialvalue=self.cfg["font_size"], minvalue=10, maxvalue=300,
            parent=self.root,
        )
        if size is not None:
            self.cfg["font_size"] = size
            self.label.configure(font=self.clock_font())
            self.place_visible(self.root.winfo_x(), self.root.winfo_y())
            self.persist_position()

    def toggle_autostart(self):
        enabled = self.autostart.get()
        try:
            set_autostart(enabled)
        except OSError as exc:
            LOGGER.exception("自動起動設定に失敗")
            self.autostart.set(self.cfg["autostart"])
            messagebox.showerror("自動起動設定エラー", str(exc), parent=self.root)
            return
        self.cfg["autostart"] = enabled
        self.persist()

    def start_drag(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag(self, event):
        if self.drag_origin is not None:
            sx, sy, wx, wy = self.drag_origin
            move_window(self.root, wx + event.x_root - sx, wy + event.y_root - sy)

    def end_drag(self, event):
        self.drag_origin = None
        self.place_visible(self.root.winfo_x(), self.root.winfo_y())
        self.persist_position()

    def tick(self):
        now = datetime.now()
        self.label.configure(text=now.strftime("%H:%M"))
        self.timer = self.root.after(1000 - now.microsecond // 1000, self.tick)

    def callback_error(self, kind, value, traceback):
        LOGGER.error("GUI処理エラー", exc_info=(kind, value, traceback))
        messagebox.showerror("処理エラー", f"{value}\n\nログ: {LOG_PATH}", parent=self.root)

    def close(self):
        try:
            self.persist_position()
        finally:
            if self.timer is not None:
                self.root.after_cancel(self.timer)
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Windows向け透過デジタル時計")
    parser.add_argument("--diagnostic", action="store_true", help="枠あり・不透明背景で起動")
    parser.add_argument("--reset-position", action="store_true", help="保存位置を無視して起動")
    args = parser.parse_args()
    setup_logging()
    root = None
    try:
        if sys.platform != "win32":
            raise RuntimeError("このプログラムはWindows専用です")
        root = tk.Tk()
        ClockApp(root, args.diagnostic, args.reset_position)
        root.mainloop()
        return 0
    except Exception as exc:
        LOGGER.exception("起動・実行に失敗")
        text = f"時計を起動できませんでした。\n{exc}\n\nログ保存先: {LOG_PATH}"
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
        elif sys.stderr is not None:
            print(text, file=sys.stderr)
        return 1
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
