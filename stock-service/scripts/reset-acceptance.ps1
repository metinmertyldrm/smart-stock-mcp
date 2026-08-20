param(
    [string]$Database = $(if ($env:ACCEPTANCE_DB_NAME) { $env:ACCEPTANCE_DB_NAME } else { "smart_stock_acceptance" }),
    [string]$HostName = $(if ($env:DB_HOST) { $env:DB_HOST } else { "localhost" }),
    [string]$Port = $(if ($env:DB_PORT) { $env:DB_PORT } else { "5432" }),
    [string]$Username = $(if ($env:DB_USERNAME) { $env:DB_USERNAME } else { "postgres" })
)

$ErrorActionPreference = "Stop"

if (-not $Database.EndsWith("_acceptance")) {
    throw "Refusing destructive reset: database name must end with '_acceptance' (got '$Database')."
}

$resources = Join-Path $PSScriptRoot "..\src\main\resources"
$resetSql = Join-Path $PSScriptRoot "reset-acceptance.sql"
$seedSql = Join-Path $resources "data.sql"
$acceptanceSeedSql = Join-Path $resources "acceptance-data.sql"

& psql -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $Username -d $Database -f $resetSql
if ($LASTEXITCODE -ne 0) { throw "Acceptance schema reset failed." }

& psql -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $Username -d $Database -f $seedSql -f $acceptanceSeedSql
if ($LASTEXITCODE -ne 0) { throw "Acceptance seed load failed." }

Write-Host "Acceptance database '$Database' reset to its deterministic baseline."
