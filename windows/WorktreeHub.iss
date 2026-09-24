#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{A981EE19-5CAB-42F0-97C4-47D4AA3DDBD1}
AppName=Worktree Hub
AppVersion={#MyAppVersion}
AppPublisher=Worktree Hub contributors
DefaultDirName={autopf}\Worktree Hub
DefaultGroupName=Worktree Hub
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=Worktree-Hub-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "..\dist\WorktreeHub.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Worktree Hub"; Filename: "{app}\WorktreeHub.exe"
Name: "{autodesktop}\Worktree Hub"; Filename: "{app}\WorktreeHub.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\WorktreeHub.exe"; Description: "启动 Worktree Hub"; Flags: nowait postinstall skipifsilent
