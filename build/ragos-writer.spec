# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec — RAG-OS (Schreiber/Voll): Docling+OCR+torch + Query-Modelle.
# Aufruf aus dem Repo-Root:  pyinstaller build/ragos-writer.spec
#
# Voraussetzungen (Writer-Build-Umgebung, Python 3.14):
#   pip install -e app[writer,dev]          # base + docling/torch/... + pyinstaller
#   python build/build-frontend.ps1         # erzeugt app/ui_static
#   python build/fetch-models.py            # erzeugt build/models (bge-m3 + reranker)
#   python build/make-icon.py               # erzeugt build/assets/ragos.ico
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.getcwd())
APP = os.path.join(ROOT, "app")

datas, binaries, hiddenimports = [], [], []

# Dritt-Pakete mit Daten/Native-Libs vollständig einsammeln.
for pkg in (
    "docling", "docling_core", "docling_parse", "rapidocr_onnxruntime",
    "fastembed", "onnxruntime", "lancedb", "transformers", "tokenizers",
    "pywebview", "pystray", "networkx", "mcp", "torch", "torchvision",
    # dynamisch ladende Submodule (uvicorn-Loops/Protokolle, SQLAlchemy-Dialekte):
    "uvicorn", "sqlalchemy", "aiosqlite", "greenlet", "anyio",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

# Eigener App-Code (top-level Module + Pakete). main:app wird von uvicorn per
# String importiert → PyInstaller sieht es nur als Hidden-Import.
hiddenimports += ["main", "config", "logger", "appsettings"]
for pkg in ("api", "auth", "backup", "db", "export", "graph", "ingest",
            "maintenance", "mcp_server", "pipelines", "services"):
    hiddenimports += collect_submodules(pkg)

# App-Daten: gebautes Frontend + Icon. Die Query-Modelle legt der INSTALLER nach
# %LOCALAPPDATA%\RAG-OS\models (dort sucht sie die Runtime) — nicht in die exe.
datas += [(os.path.join(APP, "ui_static"), "ui_static")]
datas += [(os.path.join(ROOT, "build", "assets", "ragos.ico"), "assets")]


# Header/Test-Baeume von torch aus dem Bundle nehmen (Ballast). ACHTUNG: die tief
# verschachtelten *.dist-info\licenses (Pfad >260 -> Inno-Fehler) fuegt der
# PyInstaller-torch-Hook UNABHAENGIG von diesem Filter hinzu -> die werden erst
# NACH dem Build in build.ps1 (Remove-DistInfoLicenses) per robocopy gepurgt.
def _keep(dest: str) -> bool:
    d = dest.replace("\\", "/").lower()
    return not (
        ".dist-info/licenses" in d
        or "/torch/include/" in d
        or "/torch/test/" in d
    )


datas = [(s, d) for (s, d) in datas if _keep(d)]

block_cipher = None

a = Analysis(
    [os.path.join(APP, "desktop.py")],
    pathex=[APP],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="RAG-OS",
    console=False,                 # windowed; für Debug tmp. True setzen
    icon=os.path.join(ROOT, "build", "assets", "ragos.ico"),
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="RAG-OS-Schreiber",
)
