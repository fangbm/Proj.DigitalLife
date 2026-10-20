param(
    [double]$Hours = 0,
    [int]$Cycles = 1,
    [string]$Database = '',
    [string]$Output = '',
    [switch]$Pause, [switch]$Resume, [switch]$EmergencyStop, [switch]$Status,
    [int]$HourlyLimit = 120, [int]$DailyLimit = 500,
    [int]$MinimumRest = 30, [switch]$ForceFirstWake
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$env:PYTHONUTF8 = '1'
if (-not $Database) { $Database = Join-Path $PSScriptRoot 'life.sqlite3' }
if (-not $Output) { $Output = Join-Path $PSScriptRoot 'life-evidence' }
$env:PHONE_LIFE_DB = [System.IO.Path]::GetFullPath($Database)
# Same coordinator as start-api.ps1 / Benchmark, even with a separate life database.
$env:PHONE_LIFE_LEASE_DB = Join-Path $PSScriptRoot 'phone-life.sqlite3'
$taskArgs = @('-u', '-m', 'phonelife.cli', '--mode', 'api', '--device', 'emulator')
$taskOperator = $Pause -or $Resume -or $EmergencyStop -or $Status
if ($Pause) { $taskArgs += '--pause' }
elseif ($Resume) { $taskArgs += '--resume' }
elseif ($EmergencyStop) { $taskArgs += '--emergency-stop' }
elseif ($Status) { $taskArgs += '--status' }
else {
    if ($Hours -gt 0) { $taskArgs += @('--soak-hours', [string]$Hours) }
    else { $taskArgs += @('--cycles', [string]$Cycles) }
    $taskArgs += @('--life-output', $Output, '--hourly-api-limit', [string]$HourlyLimit,
                   '--daily-api-limit', [string]$DailyLimit, '--min-rest-seconds', [string]$MinimumRest)
    if ($ForceFirstWake) { $taskArgs += '--force' }
    $env:PHONE_LIFE_API_BASE = 'https://api.deepseek.com'
    $taskSecret = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deepseek.key.dpapi') -Raw).Trim() | ConvertTo-SecureString
    $env:PHONE_LIFE_API_KEY = [System.Net.NetworkCredential]::new('', $taskSecret).Password
    $env:PHONE_LIFE_BRAIN_MODEL = 'deepseek-flash'
    $env:PHONE_LIFE_GUI_API_BASE = $env:PHONE_LIFE_API_BASE
    $env:PHONE_LIFE_GUI_API_KEY = $env:PHONE_LIFE_API_KEY
    $env:PHONE_LIFE_GUI_MODEL = 'deepseek-flash'
    $env:PHONE_LIFE_EMULATOR_SERIAL = 'emulator-5582'
    $env:PHONE_LIFE_ALLOWED_APPS = 'com.android.settings'
    $env:PHONE_LIFE_PERSONA_PATH = Join-Path $taskRoot 'persona.life.example.md'
}
try {
    & 'C:\ProgramData\miniconda3\python.exe' @taskArgs
    $taskExit = $LASTEXITCODE
} finally {
    if (-not $taskOperator) {
        Remove-Item Env:PHONE_LIFE_API_KEY, Env:PHONE_LIFE_GUI_API_KEY -ErrorAction SilentlyContinue
    }
}
exit $taskExit
