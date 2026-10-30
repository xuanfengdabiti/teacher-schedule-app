# 教师课程表移动端

Flutter Android/iOS 工程，当前交付 Android 本地演示版。包含登录、课表与调课、教学备注与交接、教学计划与学时、教室历史与失物协查。完整启动与虚构账号见 [本地演示说明](../docs/local-demo.md)。

## 本地运行

先在仓库根目录执行 `pwsh scripts/start-demo.ps1`，再在本目录执行：

```powershell
flutter pub get
flutter run
```

Android 模拟器默认 API 为 `http://10.0.2.2:18000/api/v1/`，登录页可修改。真机改成同一局域网内电脑 IP。账号可用登录页“填入教师甲 / 乙”按钮。

## 验收与构建

本次使用 Flutter 3.47.8、Dart 3.13.7、JDK 21、Android SDK 36。依赖已通过 `pubspec.lock` 固定。

```powershell
flutter analyze
flutter test
flutter build apk --release
```

安装包输出：`build/app/outputs/flutter-apk/app-release.apk`，当前约 44 MB，含 ARM64 与 Android x64。当前使用演示签名，供本地安装测试。

此电脑构建时遇到 Windows 临时目录与跨盘 Kotlin 增量缓存问题，实际验证通过的构建方式（在 `mobile/android` 目录）：

```powershell
$env:JAVA_HOME='D:\tools\jdk-21.0.13.0.2+1'
$env:ANDROID_HOME='D:\tools\android-sdk'
$env:TEMP='D:\tools\javatmp'
$env:TMP='D:\tools\javatmp'
$env:JAVA_TOOL_OPTIONS='-Djava.io.tmpdir=D:\tools\javatmp -Djdk.net.unixdomain.tmpdir=D:\tools\javatmp'
$env:PATH='D:\tools\flutter\bin;'+$env:JAVA_HOME+'\bin;'+$env:PATH
.\gradlew.bat assembleRelease '-Pkotlin.incremental=false' '-Ptarget-platform=android-arm64,android-x64'
```

上述 `D:\tools` 是当前电脑安装位置；其他电脑按实际 SDK/JDK 位置调整即可。
