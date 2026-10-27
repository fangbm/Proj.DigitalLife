param(
    [ValidateSet('Doctor','Run','Status','Stop','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SocialReceive')][string]$Command = 'Run',
    [string]$Session = '',
    [ValidateRange(0.01,10)][double]$Minutes = 10,
    [string]$Profile = 'find-x6',
    [string]$Python = 'python',
    [switch]$Emergency,
    [switch]$EventsOnly,
    [switch]$ResumeProjection
)
$ErrorActionPreference = 'Stop'
if ($Command -in @('Run','Status','Stop','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SocialReceive') -and -not $Session) { throw 'Session is required.' }
if ($Emergency -and $Command -ne 'Stop') { throw 'Emergency applies only to Stop.' }
if ($EventsOnly -and $Command -ne 'M3Probe') { throw 'EventsOnly applies only to M3Probe.' }
if ($ResumeProjection -and $Command -ne 'AudioProbe') { throw 'ResumeProjection applies only to AudioProbe after explicit observation review.' }
$v2Args = @('-u','-m','digitallife_v2',$Command.ToLowerInvariant())
if ($Command -eq 'VisionProbe') { $v2Args = @('-u','-m','acceptance.vision_probe') }
if ($Command -eq 'M3Probe') { $v2Args = @('-u','-m','acceptance.m3_content_events') }
if ($Command -eq 'ReadingProbe') { $v2Args = @('-u','-m','acceptance.reading_probe') }
if ($Command -eq 'SocialReceive') { $v2Args = @('-u','-m','acceptance.social_receive') }
if ($Command -eq 'AudioProbe') { $v2Args = @('-u','-m','acceptance.audio_probe') }
if ($EventsOnly) { $v2Args += '--events-only' }
if ($ResumeProjection) { $v2Args += '--resume-projection-prompt' }
if ($Command -in @('Run','Doctor','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SocialReceive')) { $v2Args += @('--profile',$Profile) }
if ($Session) {
    if (-not [IO.Path]::IsPathRooted($Session)) { $Session = Join-Path $PSScriptRoot $Session }
    $v2Args += @('--session',$Session)
}
if ($Command -eq 'Run') { $v2Args += @('--minutes',$Minutes.ToString([Globalization.CultureInfo]::InvariantCulture)) }
if ($Emergency) { $v2Args += '--emergency' }
$v2OldPath = $env:PYTHONPATH
$v2OldKey = $env:PHONE_LIFE_API_KEY
$v2OldUtf8 = $env:PYTHONUTF8
$v2LoadedKey = $false
try {
    $env:PYTHONPATH = $PSScriptRoot
    $env:PYTHONUTF8 = '1'
    # Only an explicitly authorized Run may decrypt the existing credential. No secret file is modified.
    if ($Command -in @('Run','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SocialReceive') -and -not $env:DIGITALLIFE_V2_API_KEY -and -not $env:PHONE_LIFE_API_KEY) {
        $v2SecretPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'data/deepseek.key.dpapi'
        if (Test-Path -LiteralPath $v2SecretPath) {
            $v2Secure = (Get-Content -LiteralPath $v2SecretPath -Raw).Trim() | ConvertTo-SecureString
            $env:PHONE_LIFE_API_KEY = [Net.NetworkCredential]::new('', $v2Secure).Password
            $v2LoadedKey = $true
        }
    }
    & $Python @v2Args
    $v2Exit = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $v2OldPath
    $env:PYTHONUTF8 = $v2OldUtf8
    if ($v2LoadedKey) { $env:PHONE_LIFE_API_KEY = $v2OldKey }
    $v2Secure = $null
}
exit $v2Exit
