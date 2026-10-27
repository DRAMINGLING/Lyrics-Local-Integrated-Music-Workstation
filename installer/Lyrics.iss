#define AppName "Lyrics"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E477B56-50A9-49B8-BBE9-0E9B119D88A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lyrics Project
DefaultDirName={localappdata}\Programs\Lyrics
DefaultGroupName=Lyrics
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Lyrics-Setup-{#AppVersion}-Windows-x64
SetupIconFile=..\assets\Lyrics.ico
UninstallDisplayIcon={app}\assets\Lyrics.ico
UninstallDisplayName=Lyrics
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ChangesAssociations=no
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
english.AdditionalShortcuts=Additional shortcuts:
english.DesktopIconTask=Create a desktop shortcut
english.CpuRuntimeShortcut=Install or repair CPU runtime
english.CudaRuntimeShortcut=Install or repair CUDA runtime
english.InstallingRuntime=Installing the CPU runtime and Python dependencies. An Internet connection is required.
chinesesimplified.AdditionalShortcuts=其他快捷方式：
chinesesimplified.DesktopIconTask=创建桌面快捷方式
chinesesimplified.CpuRuntimeShortcut=安装或修复 CPU 运行环境
chinesesimplified.CudaRuntimeShortcut=安装或修复 CUDA 运行环境
chinesesimplified.InstallingRuntime=正在安装 CPU 运行环境和 Python 依赖，需要保持网络连接。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:AdditionalShortcuts}"; Flags: unchecked

[Dirs]
Name: "{app}\input"
Name: "{app}\output"
Name: "{app}\covers"

[Files]
Source: "..\AutoLrcAligner.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\InstallAutoLrc.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LaunchLyrics.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\VERSION"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\model-presets\README.md"; DestDir: "{app}\model-presets"; Flags: ignoreversion
Source: "..\model-presets\LICENSE"; DestDir: "{app}\model-presets"; Flags: ignoreversion
Source: "..\model-presets\预设1.pt"; DestDir: "{app}\model-presets"; Flags: ignoreversion
Source: "..\auto-lrc-engine\*"; DestDir: "{app}\auto-lrc-engine"; Excludes: ".venv\*,__pycache__\*,*.pyc,.pytest_cache\*,.runtime-mode,tests\*,checkpoints\*,lid.176.ftz,demofile\*,.claude\*,CLAUDE.md,*.ipynb,*.pdf,Pipfile.lock"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Lyrics"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\LaunchLyrics.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\Lyrics.ico"
Name: "{group}\{cm:CpuRuntimeShortcut}"; Filename: "{app}\InstallAutoLrc.cmd"; WorkingDir: "{app}"
Name: "{group}\{cm:CudaRuntimeShortcut}"; Filename: "{app}\InstallAutoLrc.cmd"; Parameters: "--cuda"; WorkingDir: "{app}"
Name: "{autodesktop}\Lyrics"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\LaunchLyrics.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\Lyrics.ico"; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\auto-lrc-engine\setup.ps1"""; WorkingDir: "{app}"; StatusMsg: "{cm:InstallingRuntime}"; Flags: waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\auto-lrc-engine\.venv"
Type: files; Name: "{app}\auto-lrc-engine\.runtime-mode"

[Code]
function HasPythonLauncherVersion(Version: String): Boolean;
var
  ResultCode: Integer;
begin
  if Exec(
    ExpandConstant('{sys}\cmd.exe'),
    '/C py -' + Version + ' -c "import struct,sys;sys.exit(0 if struct.calcsize(''P'') == 8 else 1)" >nul 2>&1',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    Result := ResultCode = 0
  else
    Result := False;
end;

function InitializeSetup(): Boolean;
begin
  Result := HasPythonLauncherVersion('3.10') or HasPythonLauncherVersion('3.11');
  if not Result then
    MsgBox(
      'Lyrics requires 64-bit Python 3.10 or 3.11 with the Python Launcher (py). Install it from https://www.python.org/downloads/windows/ and run this installer again.',
      mbError,
      MB_OK
    );
end;
