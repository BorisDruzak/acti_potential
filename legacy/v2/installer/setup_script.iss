; ACTI Document Manager - Inno Setup Script

#define MyAppName "ACTI Document Manager"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "Boris"
#define MyAppURL "https://yourcompany.com"
#define MyAppExeName "ACTI_DocumentManager.exe"

[Setup]
; Основные настройки
AppId={{c1e745dc-525c-4509-92be-0ebceed761d9}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=LICENSE.txt
OutputDir=installer\output
OutputBaseFilename=ACTI_DocumentManager_Setup
SetupIconFile=convertio.in_example.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Главный exe и все зависимости
Source: "dist\ACTI_DocumentManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Конфигурационные файлы
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; Иконка
Source: "installer\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Создаем папку для данных
Name: "{app}\data"; Permissions: users-full

[Icons]
; Ярлык в меню Пуск
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Ярлык на рабочем столе
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Запустить приложение после установки
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Проверка версии Windows
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('Эта программа требует 64-битную версию Windows.', mbError, MB_OK);
    Result := False;
  end;
end;

// Создание структуры папок при первом запуске
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    DataPath := ExpandConstant('{app}\data');
    // Создаем базовую структуру
    ForceDirectories(DataPath);
    SaveStringToFile(DataPath + '\README.txt', 
      'Здесь будут храниться базы данных и файлы документов.' + #13#10 +
      'Не удаляйте эту папку вручную!', False);
  end;
end;