param([Parameter(Mandatory=$true)][string]$DataDir,
      [Parameter(Mandatory=$true)][string]$PgBin,
      [Parameter(Mandatory=$true)][string]$Username,
      [string]$Python = "python", [string]$Wheelhouse = "",
      [int]$HttpPort = 8910, [int]$PgPort = 55441)
$ErrorActionPreference = 'Stop'
$Package = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Package
if (Test-Path -LiteralPath (Join-Path $DataDir 'installation.json')) { throw 'Ya está instalado. Usa start_full_local.ps1; no vuelvas a inicializar.' }
& $Python -m venv (Join-Path $Package '.venv')
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno privado de Python.' }
$LocalPython = Join-Path $Package '.venv\Scripts\python.exe'
if ($Wheelhouse) { & $LocalPython -m pip install --no-index --find-links $Wheelhouse -r requirements_full_local.txt }
else { & $LocalPython -m pip install -r requirements_full_local.txt }
if ($LASTEXITCODE -ne 0) { throw 'No se completaron las dependencias; no se creó la base.' }
& $LocalPython -m local_pos.runtime --data-dir $DataDir init --pg-bin $PgBin --username $Username --http-port $HttpPort --pg-port $PgPort
if ($LASTEXITCODE -ne 0) { throw 'Instalación no completada. Conserva los archivos y revisa el diagnóstico.' }
