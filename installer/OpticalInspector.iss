; Instalador de Optical Inspector (Inno Setup 6)
;
; - No requiere permisos de administrador: se instala por usuario en
;   %LOCALAPPDATA%\Programs\OpticalInspector.
; - No requiere nada instalado en la PC: Python, OpenCV, etc. van incluidos.
; - Configuracion y datos viven en %LOCALAPPDATA%\OpticalInspector (no se
;   borran al actualizar ni al desinstalar).
;
; Compilar con build_installer.ps1 (o build_installer.bat).

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "build\dist\OpticalInspector"
#endif

#define MyAppName "Optical Inspector"
#define MyAppExe  "OpticalInspector.exe"

[Setup]
; GUID propio de esta aplicacion: no cambiar entre versiones (permite actualizar).
AppId={{B7C1E5A2-6D1F-4E0B-9C3A-5F2D8A41C7E9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\OpticalInspector
DisableProgramGroupPage=yes
DisableDirPage=auto
; Instalacion por usuario, sin elevacion ni dialogo de UAC.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=OpticalInspector_Setup_{#MyAppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
; Cierra la app si esta abierta al actualizar (solo procesos del propio usuario).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el &Escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked
Name: "autostart"; Description: "Iniciar {#MyAppName} al iniciar sesion en Windows"; GroupDescription: "Inicio:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Plantilla documentada de configuracion, junto al .env del usuario.
Source: "..\backend\.env.example"; DestDir: "{localappdata}\OpticalInspector"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autoprograms}\{#MyAppName} - carpeta de configuracion"; Filename: "{localappdata}\OpticalInspector"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OpticalInspector"; ValueData: """{app}\{#MyAppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExe}"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
var
  DataPage: TInputDirWizardPage;

function UserEnvFile: String;
begin
  Result := ExpandConstant('{localappdata}\OpticalInspector\.env');
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(
    wpSelectDir,
    'Carpeta de datos',
    'Donde se guardaran la base de datos, imagenes, respaldos y registros.',
    'Puede ser una carpeta local o de red (por ejemplo \\servidor\optical\datos). ' +
    'Se puede cambiar despues editando el archivo .env.',
    False, '');
  DataPage.Add('');
  DataPage.Values[0] := ExpandConstant('{localappdata}\OpticalInspector\data');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // En una actualizacion ya existe el .env: no se vuelve a preguntar.
  if PageID = DataPage.ID then
    Result := FileExists(UserEnvFile);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if (CurStep = ssPostInstall) and (not FileExists(UserEnvFile)) then
  begin
    ForceDirectories(ExtractFileDir(UserEnvFile));
    SetArrayLength(Lines, 3);
    Lines[0] := '# Generado por el instalador. Vea .env.example para todas las opciones.';
    Lines[1] := 'OI_DATA_DIR=' + DataPage.Values[0];
    Lines[2] := '';
    SaveStringsToUTF8File(UserEnvFile, Lines, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usDone) and (not UninstallSilent) then
    MsgBox('Optical Inspector se desinstalo.' + #13#10 + #13#10 +
           'Sus datos y configuracion NO se eliminaron. Estan en la carpeta de datos ' +
           'elegida al instalar y en %LOCALAPPDATA%\OpticalInspector. ' +
           'Borrelas manualmente si ya no las necesita.',
           mbInformation, MB_OK);
end;
