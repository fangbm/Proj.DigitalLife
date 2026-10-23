param(
    [ValidateSet('Smoke','Start','Status','Stop','Pause')][string]$Command = 'Start',
    [Parameter(Mandatory=$true)][string]$Session,
    [ValidateSet(1,24)][int]$Hours = 1,
    [ValidateSet(10)][Nullable[int]]$Minutes = $null,
    [string]$Profile = '', [string]$LifeDatabase = '', [string]$SourceDatabase = '',
    [string]$SourceDevice = 'emulator', [string]$AfterOneHour = ''
)
$ErrorActionPreference = 'Stop'
if ($Minutes -and ($PSBoundParameters.ContainsKey('Hours') -or $Command -eq 'Smoke')) { throw 'Use Minutes 10 for Start, or Hours 1/24; do not combine durations.' }
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$env:PYTHONUTF8 = '1'
if (-not $Profile) { $Profile = Join-Path $PSScriptRoot 'phase123-find-x6-01/device-probe-02/pending-profile.private.json' }
if (-not $LifeDatabase) { $LifeDatabase = Join-Path $PSScriptRoot 'dedicated-life/agent.sqlite3' }
if ($Command -in @('Smoke','Start') -and -not (Test-Path -LiteralPath $LifeDatabase) -and -not $SourceDatabase) {
    $SourceDatabase = Join-Path $PSScriptRoot 'staged-final-24h-candidate-01/formal-24h-candidate/agent.sqlite3'
}
$taskArgs = @('-u','-m','phonelife.dedicated_device',$Command.ToLowerInvariant(),'--session',$Session)
if ($Command -in @('Smoke','Start')) {
    $taskArgs += @('--profile',$Profile,'--life-db',$LifeDatabase,'--source-device',$SourceDevice,
                   '--lease-db',(Join-Path $PSScriptRoot 'phone-life.sqlite3'))
    if ($Minutes) { $taskArgs += @('--minutes',[string]$Minutes) } else { $taskArgs += @('--hours',[string]$Hours) }
    if ($SourceDatabase) { $taskArgs += @('--source-db',$SourceDatabase) }
    if ($AfterOneHour) { $taskArgs += @('--after-one-hour',$AfterOneHour) }
}
# Only the explicitly started autonomous run loads the existing API credential.
# Smoke/status/stop never decrypt it, create model clients or call a model.
try {
    if ($Command -eq 'Start') {
        if (-not $env:PHONE_LIFE_API_BASE) { $env:PHONE_LIFE_API_BASE = 'https://api.deepseek.com' }
        if (-not $env:PHONE_LIFE_GUI_API_BASE) { $env:PHONE_LIFE_GUI_API_BASE = $env:PHONE_LIFE_API_BASE }
        if (-not $env:PHONE_LIFE_BRAIN_MODEL) { $env:PHONE_LIFE_BRAIN_MODEL = 'deepseek-flash' }
        if (-not $env:PHONE_LIFE_GUI_MODEL) { $env:PHONE_LIFE_GUI_MODEL = 'deepseek-flash' }
        if (-not $env:PHONE_LIFE_PERSONA_PATH) { $env:PHONE_LIFE_PERSONA_PATH = Join-Path $taskRoot 'persona.life.example.md' }
        if (-not $env:PHONE_LIFE_API_KEY) {
            $taskSecret = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deepseek.key.dpapi') -Raw).Trim() | ConvertTo-SecureString
            $env:PHONE_LIFE_API_KEY = [System.Net.NetworkCredential]::new('', $taskSecret).Password
        }
        if (-not $env:PHONE_LIFE_GUI_API_KEY) { $env:PHONE_LIFE_GUI_API_KEY = $env:PHONE_LIFE_API_KEY }
    }
    & 'C:\ProgramData\miniconda3\python.exe' @taskArgs
    $taskExit = $LASTEXITCODE
} finally {
    if ($Command -eq 'Start') {
        Remove-Item Env:PHONE_LIFE_API_KEY,Env:PHONE_LIFE_GUI_API_KEY -ErrorAction SilentlyContinue
        $taskSecret = $null
    }
}
exit $taskExit
