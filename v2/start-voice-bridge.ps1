param(
    [Parameter(Mandatory=$true)][string]$QQPath,
    [Parameter(Mandatory=$true)][string]$QQPackagePath,
    [string]$Runtime = "$PSScriptRoot/data/voice-runtime/napcat-4.18.41",
    [ValidateRange(1024,65535)][int]$Port = 31071,
    [ValidateRange(1024,65535)][int]$WebPort = 31072
)
$ErrorActionPreference = 'Stop'
$npRuntime = (Resolve-Path -LiteralPath $Runtime).Path
$npQQ = (Resolve-Path -LiteralPath $QQPath).Path
$npPackage = Get-Content -LiteralPath $QQPackagePath -Raw | ConvertFrom-Json
$npPolicy = Get-Content -LiteralPath "$PSScriptRoot/data/social.private.json" -Raw | ConvertFrom-Json
$npAccount = $npPolicy.accounts.'com.tencent.mobileqq'
if ($npAccount -notmatch '^[1-9][0-9]{4,19}$') { throw 'Bound QQ account is required.' }
if (Get-NetTCPConnection -State Listen -LocalPort $Port,$WebPort -ErrorAction SilentlyContinue) { throw 'Voice bridge port is already occupied.' }
foreach ($npRequired in @('NapCatWinBootMain.exe','NapCatWinBootHook.dll','loadNapCat.js','napcat.mjs')) {
    if (-not (Test-Path -LiteralPath (Join-Path $npRuntime $npRequired))) { throw "Missing official NapCat runtime: $npRequired" }
}
# Only this separate runtime's package override is changed; installed QQ is untouched.
$npPackage.main = './loadNapCat.js'
$npPackage | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $npRuntime 'qqnt.json') -Encoding utf8
$npPrivate = "$PSScriptRoot/data/voice.private.json"
if (Test-Path -LiteralPath $npPrivate) {
    $npExisting = Get-Content -LiteralPath $npPrivate -Raw | ConvertFrom-Json
    if ($npExisting.serial -ne $npPolicy.serial -or $npExisting.provider -ne 'napcat') { throw 'Existing voice configuration differs; preserve it.' }
    $npToken = $npExisting.access_token
} else {
    $npToken = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
}
$npVoice = @{ version=1; serial=$npPolicy.serial; enabled=$true; provider='napcat'; endpoint="http://127.0.0.1:$Port"; access_token=$npToken }
$npVoice | ConvertTo-Json | Set-Content -LiteralPath $npPrivate -Encoding utf8
$npNetwork = @{ network=@{httpServers=@(@{name='digitallife-voice';enable=$true;host='127.0.0.1';port=$Port;token=$npToken;enableCors=$false;enableWebsocket=$false;messagePostFormat='array';debug=$false});httpClients=@();websocketServers=@();websocketClients=@()}; enableLocalFile2Url=$false; parseMultMsg=$false }
$npConfig = Join-Path $npRuntime 'config'
New-Item -ItemType Directory -Path $npConfig -Force | Out-Null
$npNetwork | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $npConfig "onebot11_$npAccount.json") -Encoding utf8
$npNetwork | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $npConfig 'onebot11.json') -Encoding utf8
$npWebFile = Join-Path $npConfig 'webui.json'
if (-not (Test-Path -LiteralPath $npWebFile)) {
    @{host='127.0.0.1';port=$WebPort;token=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant();loginRate=3} | ConvertTo-Json | Set-Content -LiteralPath $npWebFile -Encoding utf8
}
$npEnvNames = @('NAPCAT_PATCH_PACKAGE','NAPCAT_LOAD_PATH','NAPCAT_INJECT_PATH','NAPCAT_MAIN_PATH')
$npOriginal = @{}
foreach ($npName in $npEnvNames) { $npOriginal[$npName] = [Environment]::GetEnvironmentVariable($npName,'Process') }
try {
    $env:NAPCAT_PATCH_PACKAGE = Join-Path $npRuntime 'qqnt.json'
    $env:NAPCAT_LOAD_PATH = Join-Path $npRuntime 'loadNapCat.js'
    $env:NAPCAT_INJECT_PATH = Join-Path $npRuntime 'NapCatWinBootHook.dll'
    $env:NAPCAT_MAIN_PATH = Join-Path $npRuntime 'napcat.mjs'
    $npProcess = Start-Process -FilePath (Join-Path $npRuntime 'NapCatWinBootMain.exe') -ArgumentList @("`"$npQQ`"","`"$env:NAPCAT_INJECT_PATH`"",$npAccount) -WorkingDirectory $npRuntime -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $npRuntime 'digitallife-start.log') -RedirectStandardError (Join-Path $npRuntime 'digitallife-start.err.log')
    @{pid=$npProcess.Id;started=$npProcess.StartTime.ToUniversalTime().ToString('o');runtime=$npRuntime;qq_path=$npQQ;endpoint=$npVoice.endpoint} | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot/data/voice-runtime/process.private.json" -Encoding utf8
    Write-Output "Voice bridge started. API: $($npVoice.endpoint). Login UI: http://127.0.0.1:$WebPort/webui/"
} finally {
    foreach ($npName in $npEnvNames) { [Environment]::SetEnvironmentVariable($npName,$npOriginal[$npName],'Process') }
}
