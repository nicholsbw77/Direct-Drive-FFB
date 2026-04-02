; build/installer/setup.iss
; Inno Setup script — compiles to a single Windows installer exe.
;
; Prerequisites:
;   1. Run build\build_exe.bat first so dist\DirtFFB\ exists.
;   2. Install Inno Setup 6+: https://jrsoftware.org/isinfo.php
;   3. Compile:  iscc build\installer\setup.iss
;      Or open this file in the Inno Setup IDE and press F9.

#define MyAppName      "Dirt FFB Plugin"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "DirtFFB Project"
#define MyAppExeName   "DirtFFB.exe"
#define MyAppDir       "..\..\dist\DirtFFB"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\DirtFFB
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\..\dist
OutputBaseFilename=DirtFFB_Setup_v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\DirtFFB.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";    Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uncomment the line below to also remove user settings on uninstall:
; Type: filesandordirs; Name: "{userappdata}\DirtFFB"
