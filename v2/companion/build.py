"""Offline Android SDK build; creates a local development key, never installs."""
import os
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent
SDK=Path(os.getenv('ANDROID_SDK_ROOT',r'C:/ProgramData/Android'))
JDK=Path(os.getenv('DIGITALLIFE_JDK',r'C:/Program Files/Microsoft/jdk-17.0.5.8-hotspot'))
BUILD=ROOT/'build'; BUILD.mkdir(exist_ok=True)
TOOLS=SDK/'build-tools/36.0.0'; ANDROID=SDK/'platforms/android-35/android.jar'
env={**os.environ,'JAVA_HOME':str(JDK)}
def run(*args):subprocess.run([str(a) for a in args],env=env,check=True)
classes=BUILD/'classes'; classes.mkdir(exist_ok=True)
run(JDK/'bin/javac.exe','-encoding','UTF-8','-source','8','-target','8','-bootclasspath',str(ANDROID)+os.pathsep+str(TOOLS/'core-lambda-stubs.jar'),'-d',classes,*ROOT.glob('src/**/*.java'))
run(TOOLS/'d8.bat','--min-api','26','--lib',ANDROID,'--output',BUILD,*classes.glob('**/*.class'))
unsigned=BUILD/'unsigned.apk'
resources=BUILD/'resources.zip'
run(TOOLS/'aapt2.exe','compile','--dir',ROOT/'res','-o',resources)
run(TOOLS/'aapt2.exe','link','-I',ANDROID,'--manifest',ROOT/'AndroidManifest.xml','--min-sdk-version','26','--target-sdk-version','35','-o',unsigned,resources)
with zipfile.ZipFile(unsigned,'a') as archive:archive.write(BUILD/'classes.dex','classes.dex')
aligned=BUILD/'aligned.apk';run(TOOLS/'zipalign.exe','-f','4',unsigned,aligned)
key=BUILD/'development.keystore'
if not key.exists():run(JDK/'bin/keytool.exe','-genkeypair','-keystore',key,'-alias','digitallife','-storepass','android','-keypass','android','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=DigitalLife Local Development')
apk=BUILD/'digitallife-companion.apk'
run(TOOLS/'apksigner.bat','sign','--v4-signing-enabled','false','--ks',key,'--ks-key-alias','digitallife','--ks-pass','pass:android','--key-pass','pass:android','--out',apk,aligned)
run(TOOLS/'apksigner.bat','verify',apk)
print(apk)
