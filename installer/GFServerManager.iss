#define AppName "Gestion Fiduciaria Server Manager"
#define AppVersion "1.0.0"
#define AppPublisher "Constructora Centenario"
#define AppExeName "GFServerManager.exe"

[Setup]
AppId={{8E28B6FB-612E-45E0-9B39-C86362C3ED6A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppMutex=GFServerManagerInstallerMutex
DefaultDirName={autopf}\GFServerManager
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\installer-output
OutputBaseFilename=GFServerManager-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Instalador de Gestion Fiduciaria Server Manager
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked
Name: "startup"; Description: "Iniciar Gestion Fiduciaria automaticamente con Windows"; GroupDescription: "Inicio automatico:"; Flags: checkedonce

[Files]
Source: "..\dist\GFServerManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{commonappdata}\ConstructoraCentenario\GFServerManager"; Permissions: users-modify
Name: "{commonappdata}\ConstructoraCentenario\GFServerManager\data"; Permissions: users-modify
Name: "{commonappdata}\ConstructoraCentenario\GFServerManager\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--install-startup-task"; Flags: runhidden waituntilterminated; Tasks: startup
Filename: "{app}\{#AppExeName}"; Description: "Iniciar {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--remove-startup-task"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStartupTask"

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
