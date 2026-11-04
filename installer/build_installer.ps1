<#
.SYNOPSIS
  Genera el instalador de Optical Inspector (frontend -> exe -> instalador).

.DESCRIPTION
  1. Compila el frontend (npm run build).
  2. Empaqueta el backend con PyInstaller (carpeta, sin extraccion a %TEMP%).
  3. Prueba el ejecutable (arranca, sirve la web y la API, crea su base de datos).
  4. Compila installer\OpticalInspector.iss con Inno Setup.

  Resultado: installer\Output\OpticalInspector_Setup_<version>.exe

  Requisitos SOLO en la PC que genera el instalador: Python 3.11+ (64 bits),
  Node.js y Inno Setup 6. La PC del usuario final no necesita nada.

.EXAMPLE
  .\build_installer.ps1
  .\build_installer.ps1 -Version 1.2.0
  .\build_installer.ps1 -SkipFrontend -SkipPip     # reconstruccion rapida
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipFrontend,
    [switch]$SkipPip,
    [switch]$SkipSmokeTest,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$Installer = $PSScriptRoot
$Root      = Split-Path -Parent $Installer
$Backend   = Join-Path $Root 'backend'
$Frontend  = Join-Path $Root 'frontend'
$Build     = Join-Path $Installer 'build'
$Dist      = Join-Path $Build 'dist'
$Work      = Join-Path $Build 'work'
$AppDir    = Join-Path $Dist 'OpticalInspector'
$ExePath   = Join-Path $AppDir 'OpticalInspector.exe'

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments, [string]$WorkDir = $Root)
    Push-Location $WorkDir
    try {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Fallo (codigo $LASTEXITCODE): $File $($Arguments -join ' ')" }
    }
    finally { Pop-Location }
}

# ------------------------------------------------------------ version
if (-not $Version) {
    $versionFile = Join-Path $Installer 'VERSION'
    $Version = if (Test-Path $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { '1.0.0' }
}
Write-Host "Optical Inspector - version $Version" -ForegroundColor Green

# ------------------------------------------------------------ herramientas
Step 'Buscando herramientas'

$python = $null
foreach ($candidate in @($env:PYTHON, (Join-Path $Backend '.venv\Scripts\python.exe'), (Join-Path $Root '.venv\Scripts\python.exe'))) {
    if ($candidate -and (Test-Path $candidate)) { $python = $candidate; break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source }
}
if (-not $python) { throw 'No se encontro Python. Instale Python 3.11+ (64 bits) o defina la variable PYTHON.' }
$bits = & $python -c "import struct;print(struct.calcsize('P')*8)"
if ($bits -ne '64') { throw "Python debe ser de 64 bits (es de $bits)." }
Write-Host "Python base : $python ($(& $python --version))"

$iscc = $null
$isccCandidates = @(
    $env:ISCC_PATH,
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
)
foreach ($candidate in $isccCandidates) {
    if ($candidate -and (Test-Path $candidate)) { $iscc = $candidate; break }
}
if (-not $iscc) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $iscc = $found.Source }
}
if (-not $iscc) { throw 'No se encontro Inno Setup 6 (ISCC.exe). Descarguelo de https://jrsoftware.org/isdl.php o defina ISCC_PATH.' }
Write-Host "ISCC   : $iscc"

if (-not $SkipFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'No se encontro npm (Node.js).' }
    Write-Host "Node   : $(node --version)"
}

# ------------------------------------------------------------ limpieza
if ($Clean -and (Test-Path $Build)) {
    Step 'Limpiando compilacion anterior'
    Remove-Item $Build -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Build | Out-Null

# ------------------------------------------------------------ frontend
if ($SkipFrontend) {
    Write-Host "`n(frontend omitido: se usa frontend\dist existente)" -ForegroundColor Yellow
}
else {
    Step 'Compilando el frontend'
    if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
        Invoke-Checked 'npm' @('ci') $Frontend
    }
    Invoke-Checked 'npm' @('run', 'build') $Frontend
}
if (-not (Test-Path (Join-Path $Frontend 'dist\index.html'))) { throw 'Falta frontend\dist\index.html.' }

# ------------------------------------------------------------ backend
Step 'Preparando el entorno de compilacion (venv limpio)'
# Un venv dedicado evita que PyInstaller arrastre paquetes ajenos instalados
# globalmente (Qt, etc.) y hace el resultado reproducible.
$venv = Join-Path $Build 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Invoke-Checked $python @('-m', 'venv', $venv)
    $SkipPip = $false
}
$python = $venvPython
if (-not $SkipPip) {
    Invoke-Checked $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '--upgrade', 'pip') $Backend
    Invoke-Checked $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements.txt') $Backend
}
Invoke-Checked $python @((Join-Path $Installer 'make_icon.py')) $Root

Step 'Empaquetando el backend con PyInstaller (puede tardar unos minutos)'
if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
Invoke-Checked $python @('-m', 'PyInstaller', 'main.spec', '--noconfirm', '--clean',
                         '--distpath', $Dist, '--workpath', $Work) $Backend
if (-not (Test-Path $ExePath)) { throw "No se genero $ExePath" }
$sizeMb = [math]::Round(((Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 0)
Write-Host "Aplicacion empaquetada: $sizeMb MB"

# ------------------------------------------------------------ prueba de humo
if (-not $SkipSmokeTest) {
    Step 'Probando el ejecutable (perfil de usuario limpio y aislado)'
    $port = 8765
    $sandbox = Join-Path ([IO.Path]::GetTempPath()) ("oi_smoke_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

    # Perfil de usuario aislado: no toca tus datos reales.
    $saved = @{ LOCALAPPDATA = $env:LOCALAPPDATA; OI_PORT = $env:OI_PORT; OI_NO_BROWSER = $env:OI_NO_BROWSER }
    $env:LOCALAPPDATA = $sandbox; $env:OI_PORT = "$port"; $env:OI_NO_BROWSER = '1'
    $process = $null
    try {
        $process = Start-Process -FilePath $ExePath -PassThru
        $ready = $false
        for ($i = 0; $i -lt 90 -and -not $ready; $i++) {
            Start-Sleep -Milliseconds 500
            if ($process.HasExited) { throw 'El ejecutable se cerro al arrancar.' }
            try {
                $null = Invoke-WebRequest "http://127.0.0.1:$port/api/camera/status" -UseBasicParsing -TimeoutSec 2
                $ready = $true
            } catch { }
        }
        if (-not $ready) { throw 'El servidor no respondio en 45 s.' }

        $page = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 5
        if ($page.Content -notmatch '<div id="root"') { throw 'No se sirve el frontend.' }

        $login = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/config/login" `
                 -ContentType 'application/json' -Body '{"password":"admin"}' -TimeoutSec 10
        if (-not $login.token) { throw 'El panel de configuracion no responde.' }

        $db = Get-ChildItem $sandbox -Recurse -Filter 'optical_inspector.db' -ErrorAction SilentlyContinue
        if (-not $db) { throw 'No se creo la base de datos.' }
        Write-Host 'OK: arranca, sirve la web, la API y crea su base de datos.' -ForegroundColor Green
    }
    finally {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        foreach ($name in $saved.Keys) { Set-Item -Path "Env:$name" -Value $saved[$name] -ErrorAction SilentlyContinue }
        if ($null -eq $saved.OI_PORT) { Remove-Item Env:OI_PORT -ErrorAction SilentlyContinue }
        if ($null -eq $saved.OI_NO_BROWSER) { Remove-Item Env:OI_NO_BROWSER -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 800
        Remove-Item $sandbox -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------ instalador
Step 'Compilando el instalador con Inno Setup'
Invoke-Checked $iscc @("/DMyAppVersion=$Version", "/DSourceDir=$AppDir", '/Q', 'OpticalInspector.iss') $Installer

$setup = Join-Path $Installer "Output\OpticalInspector_Setup_$Version.exe"
if (-not (Test-Path $setup)) { throw 'Inno Setup no genero el instalador.' }
$setupMb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host "`nListo: $setup ($setupMb MB)" -ForegroundColor Green
