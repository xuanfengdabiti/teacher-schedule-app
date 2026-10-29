# 手机联网演示

本轮目标是 Android 手机安装后直接联网演示。沿用现有 Django/PostgreSQL 和虚构账号，不接学校官网、SSO 或教务接口；手机无需与电脑处于同一 Wi‑Fi。当前部署与验收进度见本文件末尾，不把待验证项目视为交付。

## 运行方式

- 后端位于用户指定的香港服务器，目录 `/srv/teacher-schedule`，独立 Compose 项目 `teacher-schedule-phone`。
- 服务地址为 `http://38.76.172.48:18000/api/v1/`，直接使用 IP，不配置域名。
- 数据库和媒体使用独立持久卷；原服务器已有网站、容器和端口不改。
- APK 用 `API_BASE_URL` 构建配置指定默认服务地址，仍保留手动连接设置。已有安装保存过其他服务地址时，以其手动设置为准。

手机需要能够访问服务器。普通课表、备注、学时、交接和失物数据保存在服务器；模型生成由后端调用已配置服务，手机端不保存模型 API 密钥。

## 手机安装

安卓手机下载 [教师课程表 1.1.0 安装包](http://38.76.172.48:18001/teacher-schedule.apk) 后安装。下载端口为独立的 `18001`，由 `teacher-schedule-download.service` 提供静态文件；APK 与服务器文件摘要一致，外网下载 HTTP 200、分段下载 206、长度与文件头校验通过。

首次安装已预设服务器地址，直接选择演示账号并登录。建议主讲演示使用“其他演示账号”里的教师甲（`t0001@example.invalid`）；李、张、王可用于交接与失物回复。统一密码 `TeacherDemo2026!`。首次进入会展示导入画面，同账号、地址、学期已有完成记录后跳过，也可勾选“演示首次导入”重放。

## 补齐的手机能力

本轮由 DeepSeek Harness 实现，Codex 负责审查与运行验收：

1. 注册账号、验证邮箱、重发验证、请求重置与确认重置的手机页面，复用已有认证接口。
2. 从系统相册选头像、预览、上传、鉴权读取及移除。
3. Android 上课本地提醒设置、提前时间、系统通知权限与测试提醒；按本人未来课程安排更新提醒。
4. 可预配置公网地址的安装包，避免新安装默认连接模拟器专用地址。

邮箱流程支持粘贴验证或重置链接。当前未提供真实 SMTP，演示服务器沿用 console 邮件后端；手机页面与接口闭环验收和真实邮箱送达是两件不同的事，不声称已接通真实发信服务。现有李、张、王和教师甲账号可直接登录。

## 新增依赖与复用依据

本轮先核查现有包，未重复实现相册选择或系统通知：

| 依赖 | 许可与来源 | 用途与适配 |
| --- | --- | --- |
| image_picker 1.2.4 | [Flutter 官方包](https://pub.dev/packages/image_picker)，BSD-3-Clause / Apache-2.0 | 复用系统相册和图片尺寸/质量选项；头像继续走已有 multipart 接口 |
| flutter_local_notifications 22.3.3 | [维护方文档](https://pub.dev/packages/flutter_local_notifications)，BSD-3-Clause | 复用 Android 通知与定时通知；不另写通知 SDK |
| timezone 0.11.2 | [Dart 官方发布者文档](https://pub.dev/packages/timezone)，BSD-3-Clause | 按课次实际时间和学校时区计算提醒时间 |

依赖锁定在 `mobile/pubspec.lock`；既有 Flutter/Riverpod/Dio/SharedPreferences 继续复用。提醒使用系统允许的非精确调度，手机系统可能延后通知；不申请精确闹钟或增加后台常驻服务。

## 构建与服务器操作

```powershell
cd mobile
flutter build apk --release --build-name=1.1.0 --build-number=2 --dart-define=API_BASE_URL=http://38.76.172.48:18000/api/v1/
```

本机 Windows 曾遇到 Kotlin 跨盘增量缓存错误。Flutter 步骤生成 SDK、版本等本地配置后，以下方式已实际构建成功（本机沿用现有 JDK 21、Android SDK 与临时目录配置）：

```powershell
cd android
$taskDefine = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('API_BASE_URL=http://38.76.172.48:18000/api/v1/'))
.\gradlew.bat assembleRelease '-Pkotlin.incremental=false' '-Ptarget-platform=android-arm64,android-x64' "-Pdart-defines=$taskDefine"
```

通知小图标通过 `android/app/src/main/res/raw/keep.xml` 保留单个 `@drawable/ic_notification`，避免 release 压缩后测试通知与课程提醒无法显示。

服务器复用 `infra/deploy/compose.yaml` 与 `backend/Dockerfile`。私有部署配置保存在服务器 `infra/deploy/.env`，不提交仓库。当前服务器 Docker Hub 直连超时，构建时使用相同 Python/PostgreSQL 镜像的镜像源，不更改项目依赖锁定。

```sh
cd /srv/teacher-schedule
docker compose -p teacher-schedule-phone --env-file infra/deploy/.env -f infra/deploy/compose.yaml ps
curl -fsS http://127.0.0.1:18000/api/v1/health/
```

本机 Docker 启动异常导致本轮暂时不能导出旧数据库。服务器通过已有种子建立同一套虚构课程、账号和活动数据；电脑上的原数据保留。现有人工操作与 AI 草稿不应被表述为已经完整迁移，后续旧库恢复可再导出比对。

## 实际验收进度

2026-10-29：服务器已启动 API 与 PostgreSQL，外网访问健康检查返回 `status=ok, database=ok`。虚构数据已初始化：37 门课程、30 间教室、1,440 个课次；教师甲及李、张、王均已通过外网登录和本人周课表、开课、教室、偏好读取验收。注册、验证、请求重置、实际链接确认重置、新密码登录通过后端测试，邮件由测试捕获而非真实送达。头像通过公网实际上传、鉴权读取、资料刷新、删除验收，结束后恢复教师甲原无头像状态。保留 2 份历史 AI 备课；公网真实生成接口另一次调用成功，用时 8.6 秒，四段共 440 字，未保存或修改课程数据。

最终 Flutter 全量 **117 项通过**，全项目 `flutter analyze --no-pub` 无问题，108 个 Dart 文件格式检查无变化。Android ARM64 / x64 release **1.1.0（2）** 构建成功并覆盖安装，最低 Android 7.0（API 24）；APK 为 **46,029,223 字节**，构建前后全部 Dart 源码摘要一致。没有连接实体手机，以上安装记录属于 Android 模拟器验收。

APK SHA-256：`6F71A97D51C4211EA3BFDACA9122C2116F5E3B445BD253A9A98B75B6DFFFACEE`。

### Android 安装后的实际操作

| 检查 | 已观察到的结果 |
| --- | --- |
| 公网登录与首次导入 | 李老师完成首次导入，7 门本人课程、220 个课次；本人今日课表和整周课表读取正常，简短备注直接显示 |
| 头像 | 系统相册选择测试图片→预览→上传成功→页面回显→移除成功；结束后恢复原姓名首字 |
| 提醒权限与系统显示 | Android 通知授权通过，点击“测试提醒”后系统通知栏实际出现对应标题与正文；release 保留小图标资源，避免压缩后丢失 |
| 课程提醒安排 | 李老师未来课程产生 11 个系统待触发提醒；08:10 上课的提醒设置为 07:55，改提前 10 分钟后为 08:00，随后恢复 15 分钟 |
| 关闭与退出 | 关闭提醒后系统待触发数变为 0；再次开启恢复 11 个，退出登录后系统待触发数再次从 11 变为 0 |
| 其他演示账号 | 教师甲完成 8 门本人课程、270 个课次的首次导入；冷启动恢复账号后系统安排 14 个未来课程提醒 |
| 最终包换账号 | 正常退出李老师并完成清理后登录教师甲，提醒页结束加载、控件可用、系统安排 14 个提醒；已移除控制器内重复监听账号造成的状态重置 |

以上系统结果来自 Android 36 模拟器实际安装的 release 包。尚未等待未来课次的真实触发时刻，非精确提醒的实际送达受手机系统调度影响。演示设备时区从 GMT 调整为 `Asia/Shanghai` 后重启 App，确认显示 08:10–11:30 / 14:00–17:10 的学校时段。

| 登录入口 | 整周课表 |
| --- | --- |
| ![手机联网登录](screenshots/phone-online-login.png) | ![手机联网周课表](screenshots/phone-online-week.png) |

![上课提醒设置](screenshots/phone-reminder-settings.png)
