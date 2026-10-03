param([Parameter(Mandatory=$true)][string]$DataDir,
      [Parameter(Mandatory=$true)][string]$PreviousPackage,
      [Parameter(Mandatory=$true)][string]$BackupDir,
      [string]$Python = 'python', [string]$Wheelhouse = '')
$ErrorActionPreference = 'Stop'
$Package = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Package
Write-Host 'Detén el POS. Conserva la carpeta del paquete anterior y no borres datos.'
$LocalPython = Join-Path $Package '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $LocalPython)) {
    & $Python -m venv (Join-Path $Package '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el Python privado; no se cambió la base.' }
}
if ($Wheelhouse) { & $LocalPython -m pip install --no-index --find-links $Wheelhouse -r requirements_full_local.txt }
else { & $LocalPython -m pip install -r requirements_full_local.txt }
if ($LASTEXITCODE -ne 0) { throw 'No se completaron las dependencias; no se cambió la base.' }
& $LocalPython -m local_pos.runtime --data-dir $DataDir upgrade --previous-package $PreviousPackage --backup-dir $BackupDir
if ($LASTEXITCODE -ne 0) { throw 'La actualización no terminó. Conserva el respaldo y sigue el diagnóstico mostrado.' }
Write-Host 'Actualización confirmada. Usa start_full_local.ps1 de ESTE paquete con el mismo DataDir.'
