param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$taskJdk = 'C:\Program Files\Microsoft\jdk-17.0.5.8-hotspot\bin'
$taskAndroid = 'C:\ProgramData\Android'
$taskBuild = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path (Join-Path $taskBuild 'classes'), (Join-Path $taskBuild 'dex'), $OutputDirectory | Out-Null
$taskLibrary = Join-Path $taskAndroid 'platforms\android-36\android.jar'
& (Join-Path $taskJdk 'javac.exe') -encoding UTF-8 -source 8 -target 8 -classpath $taskLibrary -d (Join-Path $taskBuild 'classes') (Join-Path $PSScriptRoot 'src\org\digitallife\ReadOnlyTarget.java')
if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed' }
& (Join-Path $taskJdk 'jar.exe') cf (Join-Path $taskBuild 'classes.jar') -C (Join-Path $taskBuild 'classes') .
if ($LASTEXITCODE -ne 0) { throw 'Class packaging failed' }
& (Join-Path $taskJdk 'java.exe') -cp (Join-Path $taskAndroid 'build-tools\36.0.0\lib\d8.jar') com.android.tools.r8.D8 --min-api 36 --lib $taskLibrary --output (Join-Path $taskBuild 'dex') (Join-Path $taskBuild 'classes.jar')
if ($LASTEXITCODE -ne 0) { throw 'DEX compilation failed' }
$taskOutput = Join-Path $OutputDirectory 'find-x6-target-observer.jar'
if (Test-Path -LiteralPath $taskOutput) { throw 'Refusing to overwrite observer artifact' }
& (Join-Path $taskJdk 'jar.exe') --create --file $taskOutput --date=2026-01-01T00:00:00Z -C (Join-Path $taskBuild 'dex') classes.dex
if ($LASTEXITCODE -ne 0) { throw 'DEX packaging failed' }
Get-FileHash -Algorithm SHA256 -LiteralPath $taskOutput
