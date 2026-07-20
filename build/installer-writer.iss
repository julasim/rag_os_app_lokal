; Inno Setup - RAG-OS (Schreiber/Voll). Per-User-Install (kein Admin).
; Voraussetzung: build.ps1 hat dist\RAG-OS-Schreiber\ (PyInstaller) + build\models\
; erzeugt. WebView2-Bootstrapper optional unter build\redist\ ablegen.
; Kompilieren:  iscc build\installer-writer.iss

#define AppName "RAG-OS (Schreiber)"
#define AppVersion "0.1.0"
#define ExeName "RAG-OS.exe"
#define SourceDir "..\dist\RAG-OS-Schreiber"
#define RoleArg ""
#define OutputBase "RAG-OS-Schreiber-Setup"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SIMA Architecture
DefaultDirName={localappdata}\Programs\RAG-OS
DefaultGroupName=RAG-OS
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#OutputBase}
; lzma2/fast + nicht-solid: der Writer ist ~3 GB (torch/docling) -> solide LZMA2
; waere sehr langsam. Etwas groessere Setup.exe, dafuer erträgliche Compile-Zeit.
Compression=lzma2/fast
SolidCompression=no
SetupIconFile=assets\ragos.ico
UninstallDisplayIcon={app}\{#ExeName}
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "autostart"; Description: "RAG-OS bei der Anmeldung automatisch starten"; Flags: unchecked
Name: "desktopicon"; Description: "Desktop-Verknuepfung anlegen"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Query-Modelle (bge-m3 + reranker) -> Userdata, wo die Runtime sie sucht.
Source: "models\*"; DestDir: "{localappdata}\RAG-OS\models"; Flags: recursesubdirs createallsubdirs ignoreversion
; WebView2 Evergreen-Bootstrapper (optional; wird nur bei Bedarf ausgefuehrt).
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{group}\RAG-OS"; Filename: "{app}\{#ExeName}"; Parameters: "{#RoleArg}"; IconFilename: "{app}\assets\ragos.ico"
Name: "{userdesktop}\RAG-OS"; Filename: "{app}\{#ExeName}"; Parameters: "{#RoleArg}"; IconFilename: "{app}\assets\ragos.ico"; Tasks: desktopicon

[Registry]
; Autostart (nur wenn Task gewaehlt) - --tray; die Rolle kommt aus app-settings.json.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "RAG-OS"; ValueData: """{app}\{#ExeName}"" --tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Check: NeedsWebView2; Flags: waituntilterminated skipifdoesntexist
Filename: "{app}\{#ExeName}"; Parameters: "{#RoleArg}"; Description: "RAG-OS jetzt starten"; Flags: postinstall nowait skipifsilent

[Code]
function NeedsWebView2: Boolean;
var v: String;
begin
  // Evergreen-Runtime vorhanden (HKLM 64-bit oder HKCU) -> kein Install noetig.
  Result := not (
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v)
  );
end;
