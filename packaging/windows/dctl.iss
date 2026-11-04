; Inno Setup script -> dist\dctl-<version>-windows-setup.exe
; Build:  ISCC /DVersion=0.1.0 packaging\windows\dctl.iss   (after PyInstaller made dist\windows\dctl\dctl.exe)
#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{8C2F1E8A-5B7D-4E61-9C3A-2D9F0B6A7E41}
AppName=dctl
AppVersion={#Version}
AppVerName=dctl {#Version}
AppPublisher=Vishal Singh
AppPublisherURL=https://github.com/vishal-9878/dctl
AppSupportURL=https://github.com/vishal-9878/dctl/issues
DefaultDirName={localappdata}\Programs\dctl
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=dctl-{#Version}-windows-setup
SetupIconFile=..\dctl.ico
UninstallDisplayIcon={app}\dctl.exe
UninstallDisplayName=dctl
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"
Name: "addtopath"; Description: "Add dctl to my PATH (start it from any terminal)"; Flags: unchecked

[Files]
Source: "..\..\dist\windows\dctl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\dctl"; Filename: "{app}\dctl.exe"; Comment: "Docker & PM2 dashboard for many servers"
Name: "{autodesktop}\dctl"; Filename: "{app}\dctl.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\dctl.exe"; Description: "Start dctl now"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Dir: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Path, Dir: string;
  P: Integer;
begin
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    begin
      Dir := ExpandConstant('{app}');
      P := Pos(';' + Uppercase(Dir), Uppercase(Path));
      if P > 0 then
      begin
        Delete(Path, P, Length(Dir) + 1);
        RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path);
      end;
    end;
end;
