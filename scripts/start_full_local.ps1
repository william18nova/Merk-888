param([Parameter(Mandatory=$true)][string]$DataDir)
$ErrorActionPreference = 'Stop'
$Package = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Package
& (Join-Path $Package '.venv\Scripts\python.exe') -m local_pos.runtime --data-dir $DataDir serve
if ($LASTEXITCODE -ne 0) { throw 'El POS no pudo iniciar. No reinstales encima de los datos existentes.' }
