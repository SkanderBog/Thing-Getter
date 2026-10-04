#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
[Setup]
AppId=ThingGetter.Desktop
AppName=Thing-Getter
AppVersion={#AppVersion}
AppPublisher=Thing-Getter contributors
AppPublisherURL=https://github.com/SkanderBog/Thing-Getter
DefaultDirName={localappdata}\Programs\Thing-Getter
DefaultGroupName=Thing-Getter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=Thing-Getter-{#AppVersion}-windows-x64-setup
SetupIconFile=..\desktop\assets\icon.ico
UninstallDisplayIcon={app}\Thing-Getter.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\Thing-Getter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Thing-Getter"; Filename: "{app}\Thing-Getter.exe"
Name: "{autodesktop}\Thing-Getter"; Filename: "{app}\Thing-Getter.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\Thing-Getter.exe"; Description: "Launch Thing-Getter"; Flags: nowait postinstall skipifsilent
