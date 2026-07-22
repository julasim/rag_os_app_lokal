"""
RAG-OS — native Desktop-Shell (Windows).

Startet den FastAPI-/MCP-Server (uvicorn) an 127.0.0.1 in einem Hintergrund-Thread
und zeigt die Admin-UI in einem WebView2-Fenster (pywebview). Dazu System-Tray,
Windows-Autostart, Toast-Benachrichtigungen und Drag&Drop-Ingest (nur Writer).

Prozess-/Thread-Modell:
  - Main-Thread : pywebview (GUI MUSS auf dem Main-Thread laufen)
  - Thread #1   : uvicorn (der ASGI-Server aus main:app)
  - Thread #2   : pystray (System-Tray)

Bewusst importiert dieses Modul auf TOP-Level NUR stdlib + `appsettings` — alle
schweren GUI-/Server-Libs (webview, pystray, uvicorn, httpx, PIL) werden erst in
den Funktionen importiert. So bleibt der Import headless/CI-prüfbar, und ein
fehlendes Optional-Paket kippt nicht den ganzen Start.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

# app/ auf den Importpfad (Dev-Start `python app/desktop.py` UND PyInstaller).
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import appsettings  # noqa: E402  (bewusst OHNE config-Import — Henne-Ei)

APP_TITLE = "RAG-OS"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------------
# Vault + Rolle bestimmen (VOR jedem config/main-Import)
# ---------------------------------------------------------------------------
def _ask_vault_folder() -> str | None:
    """Nativer Ordner-Picker (tkinter, stdlib) — braucht kein laufendes WebView."""
    try:
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(
            title="RAG-OS: Vault-Ordner wählen (NAS oder lokal)"
        )
        root.destroy()
        return path or None
    except Exception:
        return None


def _resolve_vault_and_role(default_role: str) -> tuple[str, str]:
    role = appsettings.get_role(default=default_role)
    vault = appsettings.get_vault_path()
    if not vault or not Path(vault).exists():
        chosen = _ask_vault_folder()
        if not chosen:
            _fatal("Kein Vault-Ordner gewählt — RAG-OS wird beendet.")
        appsettings.set_vault_path(chosen)
        appsettings.set_role(role)
        vault = chosen
    appsettings.add_recent_vault(vault)  # aktueller Vault immer in der Schnellwechsel-Liste
    return vault, role


def _apply_env(vault: str, role: str) -> None:
    os.environ["RAG_VAULT_PATH"] = str(vault)
    os.environ["RAG_SERVICE_ROLE"] = role


# ---------------------------------------------------------------------------
# uvicorn im Hintergrund-Thread
# ---------------------------------------------------------------------------
def _free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, port: int) -> None:
        import uvicorn

        self.port = port
        self._config = uvicorn.Config(
            "main:app", host="127.0.0.1", port=port,
            log_config=None, access_log=False,
        )
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="uvicorn"
        )

    def start(self) -> None:
        self._thread.start()

    def wait_ready(self, timeout: float = 45.0) -> bool:
        import httpx

        url = f"http://127.0.0.1:{self.port}/api/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(url, timeout=2.0).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Windows-Autostart (HKCU\...\Run) — kein Admin nötig
# ---------------------------------------------------------------------------
def _exe_command() -> str:
    if getattr(sys, "frozen", False):            # PyInstaller: exe = sys.executable
        return f'"{sys.executable}" --tray'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" --tray'


def autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_TITLE)
        return True
    except Exception:
        return False


def set_autostart(enable: bool) -> None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            if enable:
                winreg.SetValueEx(k, APP_TITLE, 0, winreg.REG_SZ, _exe_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_TITLE)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Toast
# ---------------------------------------------------------------------------
def toast(title: str, message: str) -> None:
    try:
        from windows_toasts import Toast, WindowsToaster

        t = Toast()
        t.text_fields = [title, message]
        WindowsToaster(APP_TITLE).show_toast(t)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------
def _icon_path() -> Path:
    # Neben der exe (PyInstaller-Datas) bzw. im Repo unter build/assets/.
    candidates = [
        APP_DIR / "assets" / "ragos.ico",
        APP_DIR.parent / "build" / "assets" / "ragos.ico",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _load_icon_image():
    from PIL import Image

    p = _icon_path()
    if p.exists():
        return Image.open(p)
    # Fallback: einfarbiges Ersatz-Icon, falls die .ico fehlt.
    return Image.new("RGBA", (64, 64), (37, 99, 235, 255))


# ---------------------------------------------------------------------------
# JS-Bridge (Drag&Drop-Ingest, nur Writer)
# ---------------------------------------------------------------------------
class Api:
    """Von der Seite über `window.pywebview.api.*` aufrufbar."""

    def ingest_paths(self, paths: list[str]) -> dict:
        """Kopiert fallengelassene Dateien in den Überwachungsordner → Watcher/Queue
        nehmen sie auf (nur Writer). Reiner Datei-Kopiervorgang, kein Ingest hier."""
        import shutil

        from config import settings

        if settings().is_reader:
            return {"ok": False, "error": "reader_readonly"}
        dest = settings().upload_dir
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for raw in paths or []:
            src = Path(raw)
            if src.is_file():
                try:
                    shutil.copy2(src, dest / src.name)
                    n += 1
                except Exception:
                    pass
        toast(APP_TITLE, f"{n} Datei(en) zur Indexierung übernommen")
        return {"ok": True, "copied": n}


# ---------------------------------------------------------------------------
# Fatal-Dialog
# ---------------------------------------------------------------------------
def _fatal(msg: str) -> None:
    try:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, msg)
        root.destroy()
    except Exception:
        sys.stderr.write(msg + "\n")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Controller — hält Server/Fenster/Tray und koordiniert Quit
# ---------------------------------------------------------------------------
class Shell:
    def __init__(self, start_hidden: bool, default_role: str) -> None:
        self.start_hidden = start_hidden
        self.default_role = default_role
        self.server: _Server | None = None
        self.window = None
        self.tray = None
        self._quitting = False

    # --- Tray ---
    def _build_tray(self):
        import pystray

        def _vault_submenu():
            # Dynamisch (Callable-Menu): letzte Vaults zum Schnellwechsel je Firma.
            current = appsettings.get_vault_path()
            items = []
            for r in appsettings.get_recent_vaults():
                path, label = r["path"], r.get("label") or r["path"]
                is_current = path == current
                items.append(pystray.MenuItem(
                    ("● " if is_current else "") + label,
                    lambda icon, item, p=path: self.switch_to_vault(p),
                    enabled=not is_current,
                ))
            if items:
                items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Anderen Ordner wählen…", lambda: self.switch_vault()))
            return pystray.Menu(*items)

        def _menu():
            return pystray.Menu(
                pystray.MenuItem("Öffnen", lambda: self.show(), default=True),
                pystray.MenuItem("Vault (Firma)", _vault_submenu()),
                pystray.MenuItem(
                    "Autostart",
                    lambda icon, item: set_autostart(not autostart_enabled()),
                    checked=lambda item: autostart_enabled(),
                ),
                pystray.MenuItem("Beenden", lambda: self.quit()),
            )

        return pystray.Icon(APP_TITLE, _load_icon_image(), APP_TITLE, _menu())

    def show(self) -> None:
        if self.window is not None:
            try:
                self.window.show()
            except Exception:
                pass

    def switch_to_vault(self, path: str) -> None:
        """Schnellwechsel auf einen bekannten Vault (Firma) → speichern + Neustart."""
        if not path or path == appsettings.get_vault_path():
            return
        appsettings.set_vault_path(path)
        appsettings.add_recent_vault(path)
        toast(APP_TITLE, "Vault gewechselt — RAG-OS startet neu…")
        self._restart()

    def switch_vault(self) -> None:
        chosen = _ask_vault_folder()
        if chosen:
            appsettings.set_vault_path(chosen)
            appsettings.add_recent_vault(chosen)
            toast(APP_TITLE, "Vault gewechselt — RAG-OS startet neu…")
            self._restart()

    def _restart(self) -> None:
        try:
            self.quit(_exit=False)
        finally:
            os.execv(sys.executable, [sys.executable, *sys.argv])

    def quit(self, _exit: bool = True) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.server is not None:
            self.server.stop()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
        if _exit:
            # webview.start() kehrt nach destroy() zurück; harter Fallback:
            threading.Timer(3.0, lambda: os._exit(0)).start()

    # --- Start ---
    def run(self) -> None:
        import webview

        port = _free_port()
        self.server = _Server(port)
        self.server.start()
        if not self.server.wait_ready():
            _fatal("Der RAG-OS-Server ist nicht rechtzeitig gestartet. "
                   "Details im Log: %LOCALAPPDATA%\\RAG-OS\\logs\\ragos.log")

        self.window = webview.create_window(
            APP_TITLE,
            f"http://127.0.0.1:{port}/",
            width=1280, height=860, min_size=(900, 600),
            js_api=Api(),
            hidden=self.start_hidden,
        )

        # Fenster-"X" → in den Tray minimieren statt beenden.
        def _on_closing():
            if self._quitting:
                return True
            self.window.hide()
            return False

        self.window.events.closing += _on_closing

        # Tray in eigenem Thread (pystray.run blockiert).
        try:
            self.tray = self._build_tray()
            threading.Thread(target=self.tray.run, daemon=True, name="tray").start()
        except Exception:
            self.tray = None

        toast(APP_TITLE, "RAG-OS läuft.")
        webview.start()          # blockiert auf dem Main-Thread bis window.destroy()
        # Falls das Fenster ohne quit() zerstört wurde, sauber aufräumen.
        self.quit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag-os")
    parser.add_argument("--tray", action="store_true",
                        help="minimiert im Tray starten (Autostart)")
    parser.add_argument("--role", choices=("writer", "reader"), default=None,
                        help="Rolle erzwingen (überschreibt app-settings.json)")
    args = parser.parse_args()

    # Installer-Default-Rolle (per Env vom Setup gesetzt) bzw. writer.
    default_role = args.role or os.environ.get("RAGOS_DEFAULT_ROLE", "writer")
    if args.role:
        appsettings.set_role(args.role)

    vault, role = _resolve_vault_and_role(default_role)
    _apply_env(vault, role)

    Shell(start_hidden=args.tray, default_role=role).run()


if __name__ == "__main__":
    main()
