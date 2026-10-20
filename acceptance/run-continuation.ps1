param([switch]$Live, [switch]$InjectMissingContinuation, [string]$Output='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH="src;."
$env:PYTHONUTF8='1'
if (-not $Live) { python -m acceptance.continuation; exit $LASTEXITCODE }
if (-not $InjectMissingContinuation -or -not $Output) { throw 'Use -Live -InjectMissingContinuation -Output NEW_DIRECTORY' }
try {
    $taskSecret=(Get-Content -LiteralPath data/deepseek.key.dpapi -Raw).Trim() | ConvertTo-SecureString
    $env:PHONE_LIFE_API_KEY=[System.Net.NetworkCredential]::new('',$taskSecret).Password
    $env:PHONE_LIFE_GUI_API_KEY=$env:PHONE_LIFE_API_KEY
    $env:PHONE_LIFE_API_BASE='https://api.deepseek.com'
    $env:PHONE_LIFE_GUI_API_BASE='https://api.deepseek.com'
    $env:PHONE_LIFE_BRAIN_MODEL='deepseek-flash'
    $env:PHONE_LIFE_GUI_MODEL='deepseek-flash'
    $env:PHONE_LIFE_EMULATOR_SERIAL='emulator-5582'
    $env:PHONE_LIFE_ALLOWED_APPS='com.android.settings'
    python -u -m acceptance.continuation --live --inject-missing-continuation --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'Finite probe failed; preserve output and inspect failure evidence' }
} finally {
    Remove-Item Env:PHONE_LIFE_API_KEY,Env:PHONE_LIFE_GUI_API_KEY -ErrorAction SilentlyContinue
}
