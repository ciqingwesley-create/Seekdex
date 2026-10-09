#include "..\build\generated\version.iss"
#ifndef Edition
  #define Edition "Standard"
#endif
[Setup]
AppId={{B2853172-9740-4A21-B7A7-BF42BC3370F8}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppPublisher={#ProductPublisher}
AppPublisherURL={#ProductHomepage}
AppSupportURL={#ProductHomepage}
DefaultDirName={autopf}\Seekdex
DefaultGroupName=Seekdex
UsePreviousAppDir=yes
UsePreviousGroup=no
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
#if Edition == "Standard"
OutputBaseFilename=Seekdex-{#ProductVersion}-Windows-x64-Setup
#else
OutputBaseFilename=Seekdex-{#ProductVersion}-Windows-x64-Setup-{#Edition}
#endif
SetupIconFile=..\src\seekdex\resources\app.ico
UninstallDisplayIcon={app}\Seekdex.exe
LicenseFile=..\LICENSE
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#WindowsVersion}
VersionInfoProductVersion={#WindowsVersion}
VersionInfoProductTextVersion={#ProductVersion}
VersionInfoTextVersion={#ProductVersion}
[InstallDelete]
; Upgrade compatibility: remove only the previous product's executable/shortcuts.
Type: files; Name: "{app}\LocalImageSearch.exe"
Type: files; Name: "{commonprograms}\LocalImageSearch\LocalImageSearch.lnk"; Check: IsAdminInstallMode
Type: files; Name: "{userprograms}\LocalImageSearch\LocalImageSearch.lnk"; Check: not IsAdminInstallMode
Type: files; Name: "{commondesktop}\LocalImageSearch.lnk"; Check: IsAdminInstallMode
Type: files; Name: "{userdesktop}\LocalImageSearch.lnk"; Check: not IsAdminInstallMode
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
[Files]
Source: "..\dist\Seekdex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#if Edition == "Preinstalled"
Source: "..\build\preinstalled-models\*"; DestDir: "{app}\preinstalled-models"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
[Icons]
Name: "{group}\Seekdex"; Filename: "{app}\Seekdex.exe"
Name: "{autodesktop}\Seekdex"; Filename: "{app}\Seekdex.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\Seekdex.exe"; Description: "启动 Seekdex"; Flags: nowait postinstall skipifsilent
; No UninstallDelete entry for LocalAppData: upgrades and uninstall retain user data.
