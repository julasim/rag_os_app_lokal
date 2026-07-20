# Baut das React-Frontend und kopiert das Ergebnis nach app/ui_static/,
# von wo FastAPI (app/main.py) die SPA serviert. Frueher machte das der Docker-
# Multi-Stage-Build; lokal uebernimmt es dieses Skript.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # Repo-Root
$frontend = Join-Path $root "app\frontend"
$uiStatic = Join-Path $root "app\ui_static"

Write-Host "==> Frontend-Build in $frontend"
Push-Location $frontend
try {
    if (Test-Path (Join-Path $frontend "package-lock.json")) {
        npm ci
    } else {
        npm install
    }
    npm run build                                  # tsc && vite build -> dist/
} finally {
    Pop-Location
}

$dist = Join-Path $frontend "dist"
if (-not (Test-Path $dist)) { throw "Build-Ausgabe fehlt: $dist" }

Write-Host "==> Kopiere dist/ -> app/ui_static/"
if (Test-Path $uiStatic) { Remove-Item -Recurse -Force $uiStatic }
New-Item -ItemType Directory -Force -Path $uiStatic | Out-Null
Copy-Item -Recurse -Force (Join-Path $dist "*") $uiStatic

Write-Host "==> Fertig. ui_static enthaelt:"
Get-ChildItem $uiStatic | Select-Object -ExpandProperty Name
