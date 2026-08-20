param(
    # This is deliberately the same single source used by Spring's datasource.
    [string]$DbUrl = $(if ($env:DB_URL) { $env:DB_URL } else { "jdbc:postgresql://localhost:5432/smart_stock_acceptance" }),
    [string]$Username = $(if ($env:DB_USERNAME) { $env:DB_USERNAME } else { "postgres" })
)

$ErrorActionPreference = "Stop"

if (-not $DbUrl.StartsWith("jdbc:postgresql://", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing destructive reset: DB_URL must be a PostgreSQL JDBC URL."
}

$target = [System.Uri]$DbUrl.Substring(5)
$Database = [System.Uri]::UnescapeDataString($target.AbsolutePath.TrimStart('/'))
if ($target.UserInfo -or [string]::IsNullOrWhiteSpace($target.Host) -or
    $Database.Contains('/') -or -not $Database.EndsWith("_acceptance", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing destructive reset: DB_URL database name must end with '_acceptance' (got '$Database')."
}
$targetConnection = $DbUrl.Substring(5)

$resources = Join-Path $PSScriptRoot "..\src\main\resources"
$resetSql = Join-Path $PSScriptRoot "reset-acceptance.sql"
$seedSql = Join-Path $resources "data.sql"
$acceptanceSeedSql = Join-Path $resources "acceptance-data.sql"

# One psql process and one transaction ensure a failed seed rolls back the truncate.
& psql -X -v ON_ERROR_STOP=1 --single-transaction -U $Username -d $targetConnection `
    -f $resetSql -f $seedSql -f $acceptanceSeedSql
if ($LASTEXITCODE -ne 0) { throw "Acceptance reset transaction failed (psql exit code $LASTEXITCODE)." }

Write-Host "Acceptance database '$Database' reset to its deterministic baseline."
