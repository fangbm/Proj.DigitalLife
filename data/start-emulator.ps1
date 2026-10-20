$ErrorActionPreference = 'Stop'
$env:ANDROID_HOME = 'C:\ProgramData\Android'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:ANDROID_AVD_HOME = 'D:\android-avd\digitallife'
$taskCurrent = & 'C:\ProgramData\Android\platform-tools\adb.exe' -s emulator-5582 emu avd name 2>$null
if ($taskCurrent -contains 'digitallife-api35') {
    Write-Output 'digitallife-api35 is already running as emulator-5582.'
    exit 0
}
$taskProcess = Start-Process -FilePath 'C:\ProgramData\Android\emulator\emulator.exe' -ArgumentList @('-avd', 'digitallife-api35', '-port', '5582', '-memory', '4096', '-cores', '4', '-no-boot-anim', '-no-snapshot-load', '-no-snapshot-save', '-no-audio', '-camera-back', 'none', '-camera-front', 'none') -WindowStyle Normal -RedirectStandardOutput (Join-Path $PSScriptRoot 'emulator.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'emulator.stderr.log') -PassThru
$taskProcess.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'emulator.pid')
Write-Output ('Started digitallife-api35, launcher PID ' + $taskProcess.Id)
