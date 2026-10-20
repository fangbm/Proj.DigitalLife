param(
    [ValidateSet('Prepare','Run','Status','Pause','Resume','EmergencyStop')][string]$Action='Status',
    [Parameter(Mandatory=$true)][string]$Session,
    [ValidateSet('mock','api')][string]$Mode='api',
    [double]$Seconds=86400,
    [switch]$PrivateEvidence,
    [string]$ApproveFingerprint='',
    [int]$HourlyLimit=120, [int]$DailyLimit=500, [int]$GoalRequestLimit=32,
    [int]$ActionLimit=15, [int]$TaskTimeout=300,
    [string]$ReviewedSession='', [string]$ReviewReference=''
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH=Join-Path $taskRoot 'src'
$env:PYTHONUTF8='1'
$env:PHONE_LIFE_API_BASE='https://api.deepseek.com'
$env:PHONE_LIFE_GUI_API_BASE='https://api.deepseek.com'
$env:PHONE_LIFE_BRAIN_MODEL='deepseek-flash'
$env:PHONE_LIFE_GUI_MODEL='deepseek-flash'
$env:PHONE_LIFE_EMULATOR_SERIAL='emulator-5582'
$env:PHONE_LIFE_ALLOWED_APPS='com.android.settings'
$taskCommand=@{Prepare='prepare';Run='run';Status='status';Pause='pause';Resume='resume';EmergencyStop='emergency-stop'}[$Action]
$taskArgs=@('-u','-m','phonelife.longrun',$taskCommand,'--session',$Session)
if ($Action -eq 'Prepare') {
    $taskArgs+=@('--mode',$Mode,'--seconds',[string]$Seconds)
    $taskArgs+=@('--hourly-limit',[string]$HourlyLimit,'--daily-limit',[string]$DailyLimit,
        '--goal-request-limit',[string]$GoalRequestLimit,'--action-limit',[string]$ActionLimit,'--task-timeout',[string]$TaskTimeout)
    if ($PrivateEvidence) {$taskArgs+='--private-evidence'}
    if ($ReviewedSession -or $ReviewReference) {$taskArgs+=@('--reviewed-session',$ReviewedSession,'--review-reference',$ReviewReference)}
}
if ($ApproveFingerprint) {$taskArgs+=@('--approve-fingerprint',$ApproveFingerprint)}
try {
    if ($Action -eq 'Run' -and $Mode -eq 'api') {
        $taskSecret=(Get-Content -LiteralPath data/deepseek.key.dpapi -Raw).Trim() | ConvertTo-SecureString
        $env:PHONE_LIFE_API_KEY=[System.Net.NetworkCredential]::new('',$taskSecret).Password
        $env:PHONE_LIFE_GUI_API_KEY=$env:PHONE_LIFE_API_KEY
    }
    python @taskArgs
    if ($LASTEXITCODE -ne 0) {throw 'Soak command failed. Terminal safety stops cannot Resume; preserve records, complete manual review, then prepare a new linked finite session.'}
} finally {
    Remove-Item Env:PHONE_LIFE_API_KEY,Env:PHONE_LIFE_GUI_API_KEY -ErrorAction SilentlyContinue
}
