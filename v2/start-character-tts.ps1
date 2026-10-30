#requires -Version 7.0
param(
    [Parameter(Mandatory=$true)][string]$GPTWeights,
    [Parameter(Mandatory=$true)][string]$SoVITSWeights,
    [Parameter(Mandatory=$true)][string]$ReferenceAudio,
    [Parameter(Mandatory=$true)][string]$ReferenceText,
    [ValidateSet('zh','en','ja')][string]$ReferenceLanguage='ja',
    [ValidateSet('zh','en','ja','auto','auto_zh')][string]$TextLanguage='zh',
    [string]$VoiceId='winefox-character',
    [string]$VoiceSource='https://www.bilibili.com/video/BV1ozLE6pE3k/',
    [ValidateSet('v1','v2','v2Pro','v2ProPlus')][string]$ModelVersion='v2Pro',
    [ValidateSet('cpu','cuda')][string]$Device='cuda',
    [string]$Runtime='',
    [string]$Python='',
    [string]$FFmpegBin='',
    [ValidateRange(1024,65535)][int]$Port=31073
)
$ErrorActionPreference='Stop'
if (-not $Runtime) {$Runtime=Join-Path $PSScriptRoot 'data/voice-runtime/gpt-sovits'}
if (-not $Python) {$Python=Join-Path $PSScriptRoot 'data/tts-env/Scripts/python.exe'}
if (-not $FFmpegBin) {$FFmpegBin=Join-Path $PSScriptRoot 'data/voice-runtime/ffmpeg-8.1.2/ffmpeg-8.1.2-full_build-shared/bin'}
$ctRuntime=(Resolve-Path -LiteralPath $Runtime).Path
$ctPython=(Resolve-Path -LiteralPath $Python).Path
$ctGPT=(Resolve-Path -LiteralPath $GPTWeights).Path
$ctSoVITS=(Resolve-Path -LiteralPath $SoVITSWeights).Path
$ctReference=(Resolve-Path -LiteralPath $ReferenceAudio).Path
$ctFFmpeg=(Resolve-Path -LiteralPath $FFmpegBin).Path
if (-not (Test-Path -LiteralPath (Join-Path $ctFFmpeg 'ffmpeg.exe')) -or -not (Get-ChildItem -LiteralPath $ctFFmpeg -Filter 'avcodec-*.dll')) {throw 'A full-shared FFmpeg build is required by TorchCodec.'}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {throw 'TTS port already occupied.'}
if (-not (Test-Path -LiteralPath (Join-Path $ctRuntime 'api_v2.py'))) {throw 'Official GPT-SoVITS api_v2.py is required.'}
$ctBase=Join-Path $ctRuntime 'GPT_SoVITS/pretrained_models'
foreach ($ctRequired in @('chinese-roberta-wwm-ext-large/pytorch_model.bin','chinese-hubert-base/pytorch_model.bin')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ctBase $ctRequired))) {throw "Missing local feature model: $ctRequired"}
}
if (-not (Test-Path -LiteralPath (Join-Path $ctBase 'fast_langdetect/lid.176.bin')) -or -not (Test-Path -LiteralPath (Join-Path $ctRuntime 'GPT_SoVITS/text/G2PWModel/g2pW.onnx'))) {throw 'Local language detection and Chinese pronunciation models are required.'}
$ctNltk=Join-Path $ctRuntime 'runtime/nltk_data'
if (-not (Test-Path -LiteralPath (Join-Path $ctNltk 'corpora/cmudict/cmudict')) -or -not (Test-Path -LiteralPath (Join-Path $ctNltk 'taggers/averaged_perceptron_tagger_eng/averaged_perceptron_tagger_eng.weights.json'))) {throw 'Offline English CMU dictionary and JSON POS tagger are required.'}
if ($ModelVersion -match 'Pro' -and -not (Test-Path -LiteralPath (Join-Path $ctBase 'sv/pretrained_eres2netv2w24s4ep4.ckpt'))) {throw 'Missing official GPT-SoVITS speaker embedding model.'}
$ctInfer=@{custom=@{version=$ModelVersion;device=$Device;is_half=($Device -eq 'cuda');t2s_weights_path=$ctGPT;vits_weights_path=$ctSoVITS;bert_base_path=(Join-Path $ctBase 'chinese-roberta-wwm-ext-large');cnhuhbert_base_path=(Join-Path $ctBase 'chinese-hubert-base')}}
$ctInferFile=Join-Path $ctRuntime 'digitallife-infer.private.yaml'
# JSON is a YAML subset; no shell-built YAML or text interpolation of model paths.
$ctInfer | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ctInferFile -Encoding utf8
$ctPolicyFile="$PSScriptRoot/data/speech.private.json"
$ctPolicy=Get-Content -LiteralPath $ctPolicyFile -Raw -Encoding utf8 | ConvertFrom-Json
$ctBackup="$PSScriptRoot/data/speech-before-character-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssffff')).private.json"
Copy-Item -LiteralPath $ctPolicyFile -Destination $ctBackup
$ctPolicy.tts=@{enabled=$true;provider='gpt_sovits_local';voice_id=$VoiceId;voice_source=$VoiceSource;endpoint="http://127.0.0.1:$Port";language='zh-CN';text_language=$TextLanguage;prompt_language=$ReferenceLanguage;prompt_text=$ReferenceText;reference_audio=$ctReference;text_split_method='cut0';parallel_infer=$false;split_bucket=$false;seed=1234;gpt_sha256=(Get-FileHash -LiteralPath $ctGPT -Algorithm SHA256).Hash.ToLowerInvariant();sovits_sha256=(Get-FileHash -LiteralPath $ctSoVITS -Algorithm SHA256).Hash.ToLowerInvariant()}
$ctOldUtf8=$env:PYTHONUTF8;$ctOldHF=$env:HF_HUB_OFFLINE;$ctOldTF=$env:TRANSFORMERS_OFFLINE;$ctOldPath=$env:PATH
try {
    $env:PYTHONUTF8='1';$env:HF_HUB_OFFLINE='1';$env:TRANSFORMERS_OFFLINE='1'
    $env:PATH=$ctFFmpeg+[IO.Path]::PathSeparator+$ctOldPath
    & $ctPython -c 'import sys,torchaudio,resampy,opencc,pyopenjtalk; audio,rate=torchaudio.load(sys.argv[1]); assert audio.shape[-1]>0 and rate>0' $ctReference
    if ($LASTEXITCODE -ne 0) {throw 'Local reference audio decoder preflight failed; check TorchCodec and shared FFmpeg.'}
    $ctProcess=Start-Process -FilePath $ctPython -ArgumentList @('-u',"`"$(Join-Path $ctRuntime 'api_v2.py')`"",'-a','127.0.0.1','-p',"$Port",'-c',"`"$ctInferFile`"") -WorkingDirectory $ctRuntime -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $ctRuntime 'digitallife-tts.log') -RedirectStandardError (Join-Path $ctRuntime 'digitallife-tts.err.log')
    $ctPolicy | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ctPolicyFile -Encoding utf8
    @{pid=$ctProcess.Id;started=$ctProcess.StartTime.ToUniversalTime().ToString('o');python=$ctPython;runtime=$ctRuntime;port=$Port;policy_backup=$ctBackup;voice_id=$VoiceId;ffmpeg_bin=$ctFFmpeg} | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot/data/voice-runtime/tts-process.private.json" -Encoding utf8
    Write-Output "Character TTS selected at http://127.0.0.1:$Port. Run VoiceSynthesize to validate actual audio; startup alone is not acceptance."
} finally {$env:PYTHONUTF8=$ctOldUtf8;$env:HF_HUB_OFFLINE=$ctOldHF;$env:TRANSFORMERS_OFFLINE=$ctOldTF;$env:PATH=$ctOldPath}
