#define MyAppName "Atlas"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Atlas"
#define MyAppExeName "Atlas.exe"
[Setup]
AppId={{B4D5A7BC-8E8A-4D72-9D11-202610050001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Atlas
DefaultGroupName=Atlas
OutputDir=.
OutputBaseFilename=Atlas-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
[Files]
Source: "Atlas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Atlas"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Atlas"; Filename: "{app}\{#MyAppExeName}"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Atlas"; Flags: nowait postinstall skipifsilent
