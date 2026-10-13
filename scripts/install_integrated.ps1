param(
    [Parameter(Mandatory=$true)][string]$PgBin,
    [int]$Port = 55460,
    [string]$WslDistribution,
    [string]$WslUser
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
if (Test-Path -LiteralPath (Join-Path $root '.novapos-runtime.json')) {
    throw 'Este proyecto ya tiene una instalación registrada. No se reemplazó. Usa python manage.py runserver.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Puerto inválido.' }
if ([bool]$WslDistribution -ne [bool]$WslUser) { throw 'Indica distribución y usuario WSL juntos, o ninguno.' }
& python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Usa Python 3.10 a 3.13'"
if ($LASTEXITCODE -ne 0) { throw 'Python compatible no disponible.' }
& python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el entorno Python.' }
$python = Join-Path $root '.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'No se completó la instalación de dependencias.' }
$params = @('scripts/prepare_integrated_install.py', '--pg-bin', $PgBin, '--port', "$Port")
if ($WslDistribution) { $params += @('--wsl-distribution', $WslDistribution, '--wsl-user', $WslUser) }
& $python @params
if ($LASTEXITCODE -ne 0) { throw 'Preparación interrumpida. Conserva los archivos y revisa el diagnóstico; no desactives Windows Security.' }
Write-Host 'Preparación terminada. Falta vincular explícitamente usuario, caja y turno con activate_integrated.py --apply.'
Write-Host 'Para trabajar: .\.venv\Scripts\Activate.ps1 y python manage.py runserver'
