param([switch]$Once, [switch]$Force, [switch]$Acceptance,
      [string]$Goal, [switch]$ResetSettings, [int]$BenchmarkTrials = 0,
      [string]$BenchmarkOutput, [string]$EvidenceDir)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PHONE_LIFE_API_BASE = 'https://api.deepseek.com'
$taskSecret = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deepseek.key.dpapi') -Raw).Trim() | ConvertTo-SecureString
$env:PHONE_LIFE_API_KEY = [System.Net.NetworkCredential]::new('', $taskSecret).Password
$env:PHONE_LIFE_BRAIN_MODEL = 'deepseek-flash'
$env:PHONE_LIFE_GUI_API_BASE = $env:PHONE_LIFE_API_BASE
$env:PHONE_LIFE_GUI_API_KEY = $env:PHONE_LIFE_API_KEY
$env:PHONE_LIFE_GUI_MODEL = 'deepseek-flash'
$env:PHONE_LIFE_EMULATOR_SERIAL = 'emulator-5582'
$env:PHONE_LIFE_DB = Join-Path $PSScriptRoot 'phone-life.sqlite3'
$env:PHONE_LIFE_ALLOWED_APPS = 'com.android.chrome'
if ($ResetSettings) { $env:PHONE_LIFE_ALLOWED_APPS += ',com.android.settings' }
$env:PHONE_LIFE_PERSONA_PATH = Join-Path $taskRoot 'persona.example.md'
if ($Acceptance) {
    $env:PHONE_LIFE_PERSONA_PATH = Join-Path $PSScriptRoot 'acceptance-persona.md'
}
$taskArgs = @('-u', '-m', 'phonelife.cli', '--mode', 'api', '--device', 'emulator')
if ($BenchmarkTrials -gt 0) {
    $taskArgs += @('--benchmark-about-phone', [string]$BenchmarkTrials, '--benchmark-output', $BenchmarkOutput)
} elseif ($Goal) {
    $taskArgs += @('--goal', $Goal)
} elseif ($Once) {
    $taskArgs += @('--cycles', '1')
    if ($Force) { $taskArgs += '--force' }
} else {
    $taskArgs += '--forever'
}
if ($ResetSettings) { $taskArgs += '--reset-settings' }
if ($EvidenceDir) { $taskArgs += @('--evidence-dir', $EvidenceDir) }
try {
    & 'C:\ProgramData\miniconda3\python.exe' @taskArgs
    $taskResult = $LASTEXITCODE
} finally {
    Remove-Item Env:PHONE_LIFE_API_KEY, Env:PHONE_LIFE_GUI_API_KEY -ErrorAction SilentlyContinue
}
exit $taskResult
