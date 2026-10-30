param(
    [ValidateSet('Doctor','Run','Status','Stop','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SpeechProbe','NativeVoiceProbe','VoiceStatus','VoiceSynthesize','VoiceSend','SocialReceive','M4Probe','M4Listen','M5Probe','M5Listen','GroupProbe','SocialInspect')][string]$Command = 'Run',
    [string]$Session = '',
    [ValidateRange(0.01,10)][double]$Minutes = 10,
    [string]$Profile = 'find-x6',
    [string]$Python = 'python',
    [switch]$Emergency,
    [switch]$EventsOnly,
    [switch]$ResumeProjection,
    [ValidateSet('qq','wechat','bilibili')][string]$SocialApp='qq',
    [ValidateSet('auto_reply','proactive_dm')][string]$SocialKind='auto_reply',
    [string]$ResumeOutbound='',
    [string]$SocialBinding='',
    [string[]]$InspectStep=@(),
    [switch]$InspectCurrent,
    [string]$GroupHint='',
    [string]$GroupReceipt='',
    [string]$RouteMarker='',
    [switch]$RequestedNow,
    [ValidateSet('watch','comment','share','link','review','reply','native')][string]$MediaKind='watch',
    [string]$SourceId='',
    [string]$ThreadId='',
    [ValidateSet('mic','voice_communication')][string]$MicrophoneSource='voice_communication',
    [string]$VoiceText='',
    [string]$VoiceRef='',
    [string]$VoiceTarget='self',
    [ValidateSet('dm','group')][string]$VoiceTargetKind='dm',
    [string]$VoiceIntent=''
)
$ErrorActionPreference = 'Stop'
if ($Command -in @('Run','Status','Stop','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SpeechProbe','NativeVoiceProbe','SocialReceive','M4Probe','M4Listen','M5Probe','M5Listen','GroupProbe','SocialInspect') -and -not $Session) { throw 'Session is required.' }
if ($Emergency -and $Command -ne 'Stop') { throw 'Emergency applies only to Stop.' }
if ($EventsOnly -and $Command -ne 'M3Probe') { throw 'EventsOnly applies only to M3Probe.' }
if ($ResumeProjection -and $Command -ne 'AudioProbe') { throw 'ResumeProjection applies only to AudioProbe after explicit observation review.' }
if ($ResumeOutbound -and ($Command -notin @('M4Probe','M5Probe') -or $ResumeOutbound -notmatch '^[a-f0-9]{32}$')) { throw 'ResumeOutbound requires M4Probe/M5Probe and an exact known-unsent outbound ID.' }
if ($SocialBinding -and ($Command -notin @('M4Probe','M4Listen','M5Probe') -or $SocialBinding -notmatch '^[a-f0-9]{64}$')) { throw 'SocialBinding requires M4Probe/M4Listen/M5Probe and a verified binding ID.' }
if ($Command -in @('VoiceSynthesize','VoiceSend') -and -not $Session) { throw 'Voice conversion/send requires a new Session.' }
if ($Command -eq 'VoiceSend' -and -not $VoiceIntent) { throw 'VoiceSend requires a stable VoiceIntent; unknown sends are never replayed.' }
$v2Args = @('-u','-m','digitallife_v2',$Command.ToLowerInvariant())
if ($Command -in @('VoiceStatus','VoiceSynthesize','VoiceSend')) {
    $v2Args = @('-u','-m','digitallife_v2.voice_cli',@{VoiceStatus='status';VoiceSynthesize='synthesize';VoiceSend='send'}[$Command],'--profile',$Profile)
    if ($VoiceText) { $v2Args += @('--text',$VoiceText) }
    if ($VoiceRef) { $v2Args += @('--speech-ref',$VoiceRef) }
    if ($Command -eq 'VoiceSend') { $v2Args += @('--target',$VoiceTarget,'--target-kind',$VoiceTargetKind,'--intent',$VoiceIntent) }
}
if ($Command -eq 'VisionProbe') { $v2Args = @('-u','-m','acceptance.vision_probe') }
if ($Command -eq 'M3Probe') { $v2Args = @('-u','-m','acceptance.m3_content_events') }
if ($Command -eq 'ReadingProbe') { $v2Args = @('-u','-m','acceptance.reading_probe') }
if ($Command -eq 'SocialInspect') { $v2Args = @('-u','-m','acceptance.social_inspect') }
if ($Command -eq 'M4Probe') { $v2Args = @('-u','-m','acceptance.social_reply_probe') }
if ($Command -eq 'M4Listen') { $v2Args = @('-u','-m','acceptance.social_reply_listen') }
if ($Command -eq 'M5Probe') { $v2Args = @('-u','-m','acceptance.media_probe') }
if ($Command -eq 'M5Listen') { $v2Args = @('-u','-m','acceptance.media_reply_listen'); if (-not $RouteMarker) { throw 'M5Listen requires RouteMarker.' }; $v2Args += @('--marker',$RouteMarker) }
if ($Command -eq 'GroupProbe') { $v2Args = @('-u','-m','acceptance.group_probe') }
if ($Command -eq 'SocialReceive') { $v2Args = @('-u','-m','acceptance.social_receive') }
if ($Command -eq 'AudioProbe') { $v2Args = @('-u','-m','acceptance.audio_probe') }
if ($Command -eq 'SpeechProbe') { $v2Args = @('-u','-m','acceptance.speech_probe','--microphone-source',$MicrophoneSource) }
if ($Command -eq 'NativeVoiceProbe') { $v2Args = @('-u','-m','acceptance.native_voice_probe') }
if ($EventsOnly) { $v2Args += '--events-only' }
if ($ResumeProjection) { $v2Args += '--resume-projection-prompt' }
if ($Command -eq 'M4Probe') { $v2Args += @('--app',$SocialApp,'--kind',$SocialKind) }
if ($Command -eq 'M4Listen') { $v2Args += @('--app',$SocialApp); if ($RouteMarker) { $v2Args += @('--marker',$RouteMarker) } }
if ($Command -eq 'M5Probe') { $v2Args += @('--kind',$MediaKind); if ($SourceId) { $v2Args += @('--source-id',$SourceId) } }
if ($Command -eq 'M5Probe' -and $ThreadId) { $v2Args += @('--thread-id',$ThreadId) }
if ($Command -eq 'GroupProbe') { $v2Args += @('--app',$SocialApp); if ($GroupHint) { $v2Args += @('--route-hint',$GroupHint) }; if ($GroupReceipt) { $v2Args += @('--receipt',$GroupReceipt) }; if ($RequestedNow) { $v2Args += '--requested-now' } }
if ($Command -eq 'SocialInspect') { $v2Args += @('--app',$SocialApp); foreach ($v2Step in $InspectStep) { $v2Args += @('--step',$v2Step) }; if ($InspectCurrent) { $v2Args += '--current' } }
if ($Command -eq 'SocialInspect' -and $RouteMarker) { $v2Args += @('--read-marker',$RouteMarker) }
if ($ResumeOutbound) { $v2Args += @('--resume-outbound',$ResumeOutbound) }
if ($SocialBinding) { $v2Args += @('--binding-id',$SocialBinding) }
if ($Command -in @('Run','Doctor','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SpeechProbe','NativeVoiceProbe','SocialReceive','M4Probe','M4Listen','M5Probe','M5Listen','GroupProbe','SocialInspect')) { $v2Args += @('--profile',$Profile) }
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
    if ($Command -in @('Run','VisionProbe','M3Probe','ReadingProbe','AudioProbe','SocialReceive','M4Probe','M4Listen','M5Probe','M5Listen','GroupProbe','SocialInspect') -and -not $env:DIGITALLIFE_V2_API_KEY -and -not $env:PHONE_LIFE_API_KEY) {
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
