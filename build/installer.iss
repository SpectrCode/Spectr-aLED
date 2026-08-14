; Script generated for Inno Setup to create installer for Spectr_aLED
; Place this file in the bild folder and run build_installer.bat

#define MyAppName "Spectr_aLED"
#define MyAppVersion "1.0"
#define MyAppPublisher "Spectr_aLED Team"
#define MyAppExeName "Spectr_aLED.exe"
#define SourceDir "..\dist\Spectr_aLed\"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=Spectr_aLED_Setup
Compression=lzma2/max
SolidCompression=yes
SetupIconFile=..\img\ico.ico
WizardImageFile=..\img\ico.png
WizardSmallImageFile=..\img\ico.png
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "eng"; MessagesFile: "compiler:Default.isl"
Name: "rus"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"