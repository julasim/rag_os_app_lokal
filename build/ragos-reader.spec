# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec — RAG-OS (Leser/schlank): NUR Query. Kein Docling/torch/OCR.
# Aufruf aus dem Repo-Root:  pyinstaller build/ragos-reader.spec
#
# Voraussetzungen (Leser-Build-Umgebung, Python 3.14):
#   pip install -e app[dev]                 # NUR base (kein [writer]!) + pyinstaller
#   python build/build-frontend.ps1         # app/ui_static
#   python build/fetch-models.py            # build/models (bge-m3 + reranker)
#   python build/make-icon.py               # build/assets/ragos.ico
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.getcwd())
APP = os.path.join(ROOT, "app")

datas, binaries, hiddenimports = [], [], []

# NUR der schlanke Query-Stack (KEIN torch/docling/rapidocr). uvicorn/sqlalchemy/
# aiosqlite/anyio laden Submodule dynamisch (Loops, Protokolle, DB-Dialekte) ->
# vollstaendig einsammeln, sonst ModuleNotFoundError zur Laufzeit (z.B. aiosqlite).
for pkg in (
    "fastembed", "onnxruntime", "lancedb", "transformers", "tokenizers",
    "pywebview", "pystray", "networkx", "mcp",
    "uvicorn", "sqlalchemy", "aiosqlite", "greenlet", "anyio",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

# Eigener App-Code. WICHTIG: ingest NICHT als ganzes Paket einsammeln
# (ingest.pipeline/parsers/docling_ingest ziehen die Writer-Deps). Nur die
# leichten, vom Reader tatsächlich importierten Module.
hiddenimports += ["main", "config", "logger", "appsettings",
                  "ingest.queue", "ingest.graph_refs"]
for pkg in ("api", "auth", "backup", "db", "export", "graph",
            "maintenance", "mcp_server", "pipelines", "services"):
    hiddenimports += collect_submodules(pkg)

# ui_static + Icon in die exe; Query-Modelle legt der Installer nach LOCALAPPDATA.
datas += [(os.path.join(APP, "ui_static"), "ui_static")]
datas += [(os.path.join(ROOT, "build", "assets", "ragos.ico"), "assets")]

block_cipher = None

a = Analysis(
    [os.path.join(APP, "desktop.py")],
    pathex=[APP],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Writer-Last hart ausschließen → schlanker Leser-Installer.
    excludes=[
        "torch", "torchvision", "docling", "docling_core", "docling_parse",
        "rapidocr_onnxruntime", "fitz", "pymupdf", "docx", "python_docx",
        "magic", "openpyxl", "bs4", "beautifulsoup4", "ocrmypdf",
        "tkinter.test", "test", "pytest",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="RAG-OS",
    console=False,
    icon=os.path.join(ROOT, "build", "assets", "ragos.ico"),
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="RAG-OS-Leser",
)
