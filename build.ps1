param(
    [switch]$SkipInstaller,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-File($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "No se encontro el archivo requerido: $Path"
    }
}

Write-Step "Validando raiz del proyecto"
Assert-File ".\main.py"
Assert-File ".\app\metadata.py"
Assert-File ".\GFServerManager.spec"

Write-Step "Limpiando salidas anteriores"
Remove-Item -LiteralPath ".\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".\dist" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".\installer-output" -Recurse -Force -ErrorAction SilentlyContinue

Write-Step "Preparando entorno de compilacion"
$BuildVenv = Join-Path $Root ".build-venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $BuildPython)) {
    python -m venv $BuildVenv
}
& $BuildPython -m pip install --upgrade pip
& $BuildPython -m pip install -r requirements.txt
& $BuildPython -m pip install -r requirements-build.txt

Write-Step "Ejecutando validaciones previas"
& $BuildPython -m compileall .\app .\main.py
& $BuildPython -c "import main; from app.metadata import APP_VERSION; assert APP_VERSION == '1.0.0'; print('imports ok')"

Write-Step "Compilando con PyInstaller"
& $BuildPython -m PyInstaller --noconfirm .\GFServerManager.spec

$ExePath = Join-Path $Root "dist\GFServerManager\GFServerManager.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "PyInstaller finalizo sin crear el ejecutable esperado: $ExePath"
}
Write-Host "Ejecutable creado: $ExePath" -ForegroundColor Green

if (-not $SkipSmokeTest) {
    Write-Step "Ejecutando prueba basica del ejecutable"
    $Existing = Get-Process -Name "GFServerManager" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath }
    if ($Existing) {
        throw "Ya existen procesos GFServerManager antes del smoke test. Cierrelos antes de compilar."
    }
    $Process = Start-Process -FilePath $ExePath -PassThru
    Start-Sleep -Seconds 8
    $Running = @(Get-Process -Name "GFServerManager" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath })
    if ($Running.Count -ne 1) {
        $Running | Stop-Process -Force -ErrorAction SilentlyContinue
        throw "Smoke test fallo: se esperaban 1 proceso y se encontraron $($Running.Count)."
    }
    Stop-Process -Id $Process.Id -Force
    Start-Sleep -Seconds 5
    $Remaining = @(Get-Process -Name "GFServerManager" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath })
    if ($Remaining.Count -ne 0) {
        $Remaining | Stop-Process -Force -ErrorAction SilentlyContinue
        throw "Smoke test fallo: GFServerManager reaparecio despues de cerrar el proceso."
    }
}

Write-Step "Comprobando datos persistentes y ausencia de secretos empaquetados"
$DataRoot = Join-Path $env:PROGRAMDATA "ConstructoraCentenario\GFServerManager"
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "logs") | Out-Null
if (Test-Path ".\dist\GFServerManager\data\server_manager.json") {
    throw "El paquete contiene server_manager.json, lo cual no esta permitido."
}
if (Get-ChildItem ".\dist\GFServerManager" -Recurse -File -Force | Where-Object { $_.Name -in @(".env", "server_manager.log") }) {
    throw "El paquete contiene archivos locales sensibles o logs."
}

if (-not $SkipInstaller) {
    Write-Step "Buscando Inno Setup"
    $isccPath = $null
    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            $isccPath = $Candidate
            break
        }
    }
    if (-not $isccPath) {
        $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
        if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) {
            $isccPath = $command.Source
        }
    }
    if ($isccPath -and (Test-Path -LiteralPath $isccPath)) {
        New-Item -ItemType Directory -Force -Path ".\installer-output" | Out-Null
        & $isccPath ".\installer\GFServerManager.iss"
        $InstallerPath = Join-Path $Root "installer-output\GFServerManager-Setup-1.0.0.exe"
        if (-not (Test-Path -LiteralPath $InstallerPath)) {
            throw "Inno Setup finalizo sin crear el instalador esperado: $InstallerPath"
        }
        Write-Host "ISCC.exe detectado: $isccPath" -ForegroundColor Green
        Write-Host "Instalador creado: $InstallerPath" -ForegroundColor Green
    } else {
        Write-Warning "No se encontro Inno Setup. Rutas esperadas: $($Candidates -join '; ') o ISCC.exe disponible en PATH."
    }
}

Write-Step "Build finalizado"
Write-Host "Ejecutable: $ExePath" -ForegroundColor Green
