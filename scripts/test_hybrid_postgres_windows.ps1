param(
    [string]$PostgresRoot = (Join-Path $env:LOCALAPPDATA 'NovaPOS-TestTools/pgsql'),
    [string]$Python = 'python',
    [int]$Port = 55432
)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Puerto de prueba inválido.' }
$bin = Join-Path $PostgresRoot 'bin'
foreach ($name in @('initdb.exe', 'pg_ctl.exe', 'createdb.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bin $name))) { throw "Falta PostgreSQL: $name" }
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "El puerto $Port ya está ocupado. No se utilizará una base preexistente."
}
$root = Join-Path $env:LOCALAPPDATA 'NovaPOS-TestTools'
$run = Join-Path $root ('run-' + [guid]::NewGuid().ToString('N'))
$data = Join-Path $run 'data'
$pwfile = Join-Path $run 'init-password.txt'
New-Item -ItemType Directory -Path $run | Out-Null
$password = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText($pwfile, $password, (New-Object Text.UTF8Encoding($false)))
$names = @('PGPASSWORD', 'HYBRID_TEST_POSTGRES', 'HYBRID_TEST_POSTGRES_PORT', 'HYBRID_TEST_POSTGRES_PASSWORD')
$old = @{}
foreach ($name in $names) { $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = $false
$exitCode = 1
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & (Join-Path $bin 'initdb.exe') -D $data -U nova_hybrid_ci -A scram-sha-256 -E UTF8 --locale=C "--pwfile=$pwfile"
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el clúster ficticio.' }
    Remove-Item -LiteralPath $pwfile
    & (Join-Path $bin 'pg_ctl.exe') -D $data -l (Join-Path $run 'postgres.log') -o "-h 127.0.0.1 -p $Port -c max_connections=30" -w -t 30 start
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar PostgreSQL de pruebas.' }
    $started = $true
    $env:PGPASSWORD = $password
    & (Join-Path $bin 'createdb.exe') -h 127.0.0.1 -p $Port -U nova_hybrid_ci nova_hybrid_ci
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear la base ficticia.' }
    $env:HYBRID_TEST_POSTGRES = 'yes-local-disposable'
    $env:HYBRID_TEST_POSTGRES_PORT = [string]$Port
    $env:HYBRID_TEST_POSTGRES_PASSWORD = $password
    & $Python -B manage.py test mainApp.test_hybrid mainApp.test_hybrid_recovery mainApp.test_hybrid_postgres mainApp.test_hybrid_migration mainApp.test_hybrid_acceptance mainApp.test_cash_receipt mainApp.test_ptm --settings=NovaSoft.hybrid_test_postgres_settings --noinput
    $exitCode = $LASTEXITCODE
}
finally {
    if ($started) {
        & (Join-Path $bin 'pg_ctl.exe') -D $data -m fast -w -t 30 stop
        if ($LASTEXITCODE -ne 0) { Write-Warning "Revisa el proceso de pruebas: $data"; $exitCode = 1 }
    }
    if (Test-Path -LiteralPath $pwfile) { Remove-Item -LiteralPath $pwfile }
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process') }
    $password = $null
    Pop-Location
    Write-Host "Datos ficticios y log de esta ejecución: $run"
}
exit $exitCode
