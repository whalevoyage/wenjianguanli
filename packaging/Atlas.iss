#define MyAppName "Atlas"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Atlas"
#define MyAppExeName "Atlas.exe"
[Setup]
AppId={{B4D5A7BC-8E8A-4D72-9D11-202610050001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 按用户安装到 %LOCALAPPDATA%，不写 Program Files —— 与程序自身的数据目录一致，
; 也不需要管理员权限（学校/公司电脑常常没有）。要改回全机安装就换回
; {autopf} + PrivilegesRequired=admin。
DefaultDirName={localappdata}\Atlas
DefaultGroupName=Atlas
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=Atlas-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
[Files]
Source: "Atlas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{userprograms}\Atlas"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\Atlas"; Filename: "{app}\{#MyAppExeName}"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Atlas"; Flags: nowait postinstall skipifsilent
