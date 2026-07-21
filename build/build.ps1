# RAG-OS Windows-Build: Frontend -> Icon -> Query-Modelle -> PyInstaller -> Inno Setup.
# Beide Installer lassen sich aus EINER Umgebung bauen, die die Writer-Extras hat
# (die reader.spec schliesst torch/docling per `excludes` aus -> schlank).
#
# Vorbereitung (einmalig, Python 3.14):
#   python -m venv .venv ; .\.venv\Scripts\Activate.ps1
#   pip install -e app[writer,dev]
#   # (WebView2-Bootstrapper optional nach build\redist\ legen: MicrosoftEdgeWebview2Setup.exe)
#
# Aufruf:  .\build\build.ps1                 # beide Installer
#          .\build\build.ps1 -Target reader  # nur Leser
#          .\build\build.ps1 -SkipModels     # Modelle nicht neu backen
param(
    [ValidateSet("all", "writer", "reader")]
    [string]$Target = "all",
    [switch]$SkipModels,
    [switch]$SkipFrontend
)
$ErrorActionPreference = "Stop"
$build = $PSScriptRoot
$root = Split-Path -Parent $build
$work = Join-Path $root "dist\pyi-work"
$dist = Join-Path $root "dist"

# torch/docling & Co. bringen tief verschachtelte *.dist-info\licenses-Baeume mit
# (>260 Zeichen -> Inno Setup bricht mit "Pfad nicht gefunden" ab). Der PyInstaller-
# torch-Hook fuegt sie unabhaengig vom Spec hinzu -> hier NACH dem Build purgen
# (robocopy ist long-path-fest). Runtime braucht die Lizenz-Kopien nicht.
function Remove-DistInfoLicenses([string]$distName) {
    $internal = Join-Path $dist "$distName\_internal"
    if (-not (Test-Path $internal)) { return }
    $empty = Join-Path $env:TEMP "rag_empty_lic"
    New-Item -ItemType Directory -Force $empty | Out-Null
    $n = 0
    Get-ChildItem $internal -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue | ForEach-Object {
        $lic = Join-Path $_.FullName "licenses"
        if (Test-Path $lic) {
            robocopy $empty $lic /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
            Remove-Item $lic -Recurse -Force -ErrorAction SilentlyContinue
            $n++
        }
    }
    Remove-Item $empty -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "   $distName : $n Lizenz-Baeume entfernt"
}

Push-Location $root
try {
    if (-not $SkipFrontend) {
        Write-Host "== [1/5] Frontend -> ui_static ==" -ForegroundColor Cyan
        & (Join-Path $build "build-frontend.ps1")
    }

    Write-Host "== [2/5] Icon ==" -ForegroundColor Cyan
    python (Join-Path $build "make-icon.py")

    if (-not $SkipModels) {
        Write-Host "== [3/5] Modelle (e5-large + Reranker + Docling + Tokenizer) ==" -ForegroundColor Cyan
        python (Join-Path $build "fetch-models.py")
    }

    Write-Host "== [4/5] PyInstaller ==" -ForegroundColor Cyan
    # WICHTIG: eigener --workpath, sonst schreibt PyInstaller in unseren build\-Quellordner.
    if ($Target -in @("all", "writer")) {
        pyinstaller --noconfirm --workpath $work --distpath $dist (Join-Path $build "ragos-writer.spec")
        Remove-DistInfoLicenses "RAG-OS-Schreiber"
    }
    if ($Target -in @("all", "reader")) {
        pyinstaller --noconfirm --workpath $work --distpath $dist (Join-Path $build "ragos-reader.spec")
        Remove-DistInfoLicenses "RAG-OS-Leser"
    }

    Write-Host "== [5/5] Inno Setup ==" -ForegroundColor Cyan
    if ($Target -in @("all", "writer")) { iscc (Join-Path $build "installer-writer.iss") }
    if ($Target -in @("all", "reader")) { iscc (Join-Path $build "installer-reader.iss") }

    Write-Host "`nFertig. Installer:" -ForegroundColor Green
    Get-ChildItem $dist -Filter "*Setup.exe" | Select-Object Name, @{N = "MB"; E = { [math]::Round($_.Length / 1MB, 1) } }
} finally {
    Pop-Location
}
