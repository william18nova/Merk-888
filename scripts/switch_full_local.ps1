param([string]$DataDir = (Join-Path $env:LOCALAPPDATA 'NovaPOS-Piloto\datos'),
      [string]$Username = '')
$ErrorActionPreference = 'Stop'
$Package = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Package
Write-Host 'Relevo: primero cierra y sincroniza el turno anterior; después detén el POS con Ctrl+C.'
Write-Host 'El nuevo cajero debe tener su turno abierto en la nube. No se borran pendientes.'
if (-not $Username) { $Username = Read-Host 'Usuario del siguiente cajero en la nube' }
if (-not $Username.Trim()) { throw 'Falta el usuario del siguiente cajero.' }
& (Join-Path $Package '.venv\Scripts\python.exe') -m local_pos.runtime --data-dir $DataDir switch-user --username $Username.Trim()
if ($LASTEXITCODE -ne 0) { throw 'Relevo no completado. Conserva los datos y reintenta con el mismo usuario.' }
Write-Host 'Ahora usa start_full_local.ps1 con el mismo DataDir para volver a abrir el POS.'
