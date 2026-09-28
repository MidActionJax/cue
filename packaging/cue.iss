; Inno Setup script for Cue. Build with packaging/build.ps1, which passes:
;   /DAppVersion=0.1.0-beta  /DAppVersionNum=0.1.0  /DDistDir=<PyInstaller dist\Cue>  /O<output dir>
; Silent install example:
;   Cue-Setup.exe /VERYSILENT /DIR="E:\Programs\Cue" /DATADIR="E:\Cue"

#ifndef AppVersion
  #define AppVersion "0.1.0-beta"
#endif
#ifndef AppVersionNum
  #define AppVersionNum "0.1.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\Cue"
#endif

[Setup]
AppId={{7C2F4E4B-9D1A-4B3E-9C57-1E0C5A6B2F11}
AppName=Cue
AppVersion={#AppVersion}
AppVerName=Cue {#AppVersion}
VersionInfoVersion={#AppVersionNum}
AppPublisher=Jaxon Doolittle
AppPublisherURL=https://github.com/MidActionJax/cue
AppSupportURL=https://github.com/MidActionJax/cue/issues
AppCopyright=Copyright (c) 2026 Jaxon Doolittle
; per-user install: no admin prompt, and the scheduled task runs as you
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\Cue
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputBaseFilename=Cue-Setup-{#AppVersion}
SetupIconFile=..\assets\cue.ico
UninstallDisplayIcon={app}\Cue.exe
UninstallDisplayName=Cue
WizardStyle=modern
WizardImageFile=..\assets\installer_side.bmp
WizardSmallImageFile=..\assets\installer_small.bmp
Compression=lzma2/normal
SolidCompression=yes
LZMANumBlockThreads=8
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: worklog; Description: "Refresh my work brief daily at 7:30 (reads your Claude Code / Cowork sessions)"; GroupDescription: "Work brief:"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "register_task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; same AppUserModelID the app sets, so a pinned shortcut and the running app share one taskbar button
Name: "{autoprograms}\Cue"; Filename: "{app}\Cue.exe"; AppUserModelID: "MidActionJax.Cue"; Comment: "Real-time call copilot"
Name: "{autodesktop}\Cue"; Filename: "{app}\Cue.exe"; AppUserModelID: "MidActionJax.Cue"; Comment: "Real-time call copilot"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\register_task.ps1"" -Exe ""{app}\Cue.exe"""; Flags: runhidden; Tasks: worklog; StatusMsg: "Scheduling the daily work brief..."
Filename: "{app}\Cue.exe"; Description: "Launch Cue"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Cue.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitCue"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\register_task.ps1"" -Remove"; Flags: runhidden; RunOnceId: "RemoveTask"

[UninstallDelete]
; your data folder (config, profiles, call notes) is never touched
Type: files; Name: "{app}\cue_home.txt"

[Code]
var
  DataPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir,
    'Where should Cue keep your data?',
    'Your settings, profiles, call notes and the Whisper speech model (about 1.6 GB).',
    'Cue keeps everything it records and learns in this folder. It stays put if you uninstall or upgrade. ' +
    'Pick a drive with a few GB free, then click Next.',
    False, '');
  DataPage.Add('');
  DataPage.Values[0] := ExpandConstant('{param:DATADIR|' +
    GetPreviousData('DataDir', ExpandConstant('{%USERPROFILE}\Cue')) + '}');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDir', DataPage.Values[0]);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { upgrading: let a running Cue save its call and exit first }
  if FileExists(ExpandConstant('{app}\Cue.exe')) then
    Exec(ExpandConstant('{app}\Cue.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(DataPage.Values[0]);
    SetArrayLength(Lines, 1);
    Lines[0] := DataPage.Values[0];
    SaveStringsToUTF8File(ExpandConstant('{app}\cue_home.txt'), Lines, False);
  end;
end;
