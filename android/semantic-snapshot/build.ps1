$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$taskBuild = Join-Path $PSScriptRoot 'build'
$taskJdk = 'C:\Program Files\Microsoft\jdk-11.0.17.8-hotspot\bin'
$taskAndroid = 'C:\ProgramData\Android'
New-Item -ItemType Directory -Force -Path (Join-Path $taskBuild 'classes'), (Join-Path $taskBuild 'dex') | Out-Null
& (Join-Path $taskJdk 'javac.exe') -encoding UTF-8 -source 8 -target 8 -classpath (Join-Path $taskAndroid 'platforms\android-35\android.jar') -d (Join-Path $taskBuild 'classes') (Join-Path $PSScriptRoot 'src\org\digitallife\ReadOnlySnapshot.java')
if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed' }
& (Join-Path $taskJdk 'jar.exe') cf (Join-Path $taskBuild 'classes.jar') -C (Join-Path $taskBuild 'classes') .
& (Join-Path $taskJdk 'java.exe') -cp (Join-Path $taskAndroid 'build-tools\35.0.0\lib\d8.jar') com.android.tools.r8.D8 --min-api 24 --lib (Join-Path $taskAndroid 'platforms\android-35\android.jar') --output (Join-Path $taskBuild 'dex') (Join-Path $taskBuild 'classes.jar')
if ($LASTEXITCODE -ne 0) { throw 'DEX compilation failed' }
$taskAssets = Join-Path $taskRoot 'src\phonelife\assets'
New-Item -ItemType Directory -Force -Path $taskAssets | Out-Null
& (Join-Path $taskJdk 'jar.exe') cf (Join-Path $taskAssets 'ui-snapshot.jar') -C (Join-Path $taskBuild 'dex') classes.dex
if ($LASTEXITCODE -ne 0) { throw 'JAR packaging failed' }
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $taskAssets 'ui-snapshot.jar')
