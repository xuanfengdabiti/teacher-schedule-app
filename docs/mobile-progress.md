# 移动端进度（本地演示）

> 最终状态见 [2026-10-27 本地演示验收](local-demo-acceptance.md)：路由已接线，18 项测试通过、analyze 无问题、release APK 已安装并完成实际操作。下方保留分阶段记录，其中临时目录故障和“尚未构建”已解决。

更新：2026-10-27
范围：`mobile/` 下的 Android/iOS Flutter App。目标是本机演示四核心可用，不做云部署、商店发布或生产签名。

## 已完成

### 基础与登录

- 标准 Flutter 工程（Android + iOS，`teacher_schedule`），依赖锁定：Riverpod、Dio、go_router、flutter_secure_storage、shared_preferences、intl。
- 登录页可修改 API 地址；Android 模拟器默认 `http://10.0.2.2:18000/api/v1/`。
- 登录页已明示“本地演示 · 数据均为虚构”，并提供“填入教师甲/乙”：
  - 教师甲 `t0001@example.invalid`
  - 教师乙 `t0002@example.invalid`
  - 密码 `TeacherDemo2026!`
- JWT 存 flutter_secure_storage；启动恢复 `me/`；401 单次 refresh 并保存旋转后的 refresh；并发 refresh 去重。
- refresh 网络错误或 5xx 时保留本地令牌，只显示错误；仅 refresh 明确 400/401 时清除令牌并回登录。
- pending / 邮箱未验证 / disabled / unlinked 有独立账号状态说明页。
- 底部四标签：今天、课表、功能、我的。

### 课表

- 今天页显示日期、教学周、本人课程卡片；点击进详情。
- 周课表一次请求七天（`sessions/?from&to&scope=mine`），七日期切换、上周/下周/回今天、日期选择器。
- 同周缓存复用；今天与课表共享同一周缓存，切页不重复请求。
- 今天空状态含“查看本周课表”入口，便于演示 2026-10-27 周二无课时切到 10-26 或下一周。
- 空课、学期外、加载失败、可能未导入均有明确文案。

### 详情与教学备注

- 详情读取真实 API 字段：教师、班级、教室、日期/节次/时间、计划/实际学时、目标学时、选课人数、状态、版本。
- 详情可直接进入：教学备注、教学计划、完成学时、发交接、查询调课。
- 备注读取 mine / others / previous，三段 covered / unfinished / next_plan。
- PUT 保存草稿、POST 显式发布；保存与发布分离；同正文重试复用 `client_mutation_id`。
- 409 展示服务器版本与三段内容，保留本地稿；可“保留本地基于服务器版本重试”或“载入服务器版本（本地另存备份）”，也可恢复备份。
- 草稿和备份键改为 `账号 ID + 课次 ID`，共同授课教师换账号不会串稿。
- 恢复 LocalDraft 时保留原 `baseVersion`。服务器已 v2 时，v1 本地稿仍以 v1 提交，得到 409，不会直接覆盖 v2。
- 有本地稿时先立即显示并允许编辑，再后台尝试拉服务器；断网失败只增加“离线草稿可继续编辑”提示，不把已有稿替换成错误页。
- 输入只在本地 500ms 防抖保存，不发网络请求；保存失败保留文字。

### 功能入口与消息

- 功能页：按 offering 显示真实开课列表，并可从每门课进入“教学计划 / 进度”；常用工具包含交接收发、调课记录、教室查询、协查收发。
- 首页铃铛复用 M03 的 `NotificationListPage`（`features/collaboration/notification_list_page.dart`），读取真实消息：`GET notifications/`、游标加载更多、`POST notifications/{id}/read/`。
- 消息点击按 `object_type/object_id`：
  - session → 当前课次详情
  - handoff → 交接列表
  - inquiry → 协查详情（无 ID 时列表）
  - reschedule → 调课记录
  - classroom → 教室使用（无 ID 时教室查询）
  - 其他/缺少对象 → 只显示该消息详情，不做假跳转。
- 集成 M02/M03 页面时只导入其公开构造函数，未修改 `features/teaching/`、`features/collaboration/`。

### 平台

- `AndroidManifest.xml` 主清单已有 `INTERNET` 权限、`usesCleartextTraffic="true"`，应用名为“教师课表”。
- iOS 工程保留；本地 HTTP 的 ATS 放行配置保留。
- 未做注册/找回全套 UI、头像 UI、远程推送、云部署、生产签名——均非本轮本地演示阻塞项。

## 本轮验证状态

上一轮（本轮修改前）本切片测试曾通过 8 项；本轮新增的回归覆盖：

- `test/api_client_test.dart`：401 旋转 refresh；refresh 503 保留令牌；refresh 401 才退出。
- `test/notes_conflict_test.dart`：双账号同课次不串稿；v1 本地稿面对 v2 使用 v1 提交并 409；断网本机稿进入可编辑页；mutation id 复用。
- `test/schedule_controller_test.dart`：同周只请求一次。
- `test/widget_test.dart`：登录 → 今天/周缓存 → 详情 → 保存备注的真实 JSON 合同；已有令牌恢复登录。

本轮 `format / analyze / test` 未能执行：DSH 的 PowerShell 子进程在启动前失败，错误为
`mkdtemp 'D:\class schedule\D:\tools<TAB>javatmp\dsh-subprocess-XXXXXX'`。这是工具执行器的临时目录环境问题，不是 Dart 编译或测试断言输出；所有 `pwsh`、`dart`、`flutter test` 都受影响。需要在正常 TEMP/TMP 的会话中重跑。

建议复跑命令（Windows，`TEMP/TMP` 必须是存在的路径）：

```powershell
cd "D:\class schedule\mobile"
$env:TEMP = "D:\tools\javatmp"; $env:TMP = "D:\tools\javatmp"
D:\tools\flutter\bin\dart.bat format lib test
$dirs = @('lib/app','lib/core','lib/shared','lib/features/auth','lib/features/schedule','lib/features/notes','lib/features/offerings','lib/features/profile','lib/features/shell')
foreach ($dir in $dirs) { D:\tools\flutter\bin\flutter.bat analyze $dir }
D:\tools\flutter\bin\flutter.bat analyze test
D:\tools\flutter\bin\flutter.bat test test/api_client_test.dart test/notes_conflict_test.dart test/schedule_controller_test.dart test/widget_test.dart
```

说明：全目录 `flutter analyze` / `flutter test` 还会包含并行 worker 正在收尾的 M02/M03 文件；上一轮曾出现 `inquiry_detail_page.dart`、`classroom_usage_page.dart`、`teaching_widgets.dart` 的临时未完成错误，以及 2 个协作测试失败。不要用这些错误判断本切片，等 M02/M03 收尾后再做全 App 检查。

## 构建与安装

- 本轮按分工未重复运行 Gradle/NDK；Codex 正在本机首次构建 `app-first-build.log`。
- 本轮未安装模拟器 APK，也未验证真机。
- 构建时可使用（每个进程）：
  - `TEMP=TMP=D:/tools/javatmp`
  - `JAVA_TOOL_OPTIONS='-Djava.io.tmpdir=D:\tools\javatmp -Djdk.net.unixdomain.tmpdir=D:\tools\javatmp'`

## 仍未做

- 注册、邮箱验证重发、密码找回的完整 UI。
- 头像设置 UI（`avatar_url` 读取字段保留）。
- 推送设备注册、真实推送通道、后台提醒。
- 交接单条详情独立路由；目前消息中心跳交接列表，列表内可进详情。
- 生产签名、商店打包、云部署、数据备份。
