# 移动端协作功能（M03）

> 本文为模块交接记录。最终主路由已接线，当前界面使用“教室与失物 / 教室记录 / 失物消息”等名称；调课提交区固定在底部。实际操作与构建结果见 [最终验收](local-demo-acceptance.md)。

本目录实现调课、教室协查、失物协查和消息中心的可直接使用页面。页面只依赖现有 `ApiClient`，不依赖 Riverpod 全局 provider，不新增 pubspec 依赖，也未改 `main.dart`/router；主路由由 Codex 统一接线。

## 公共页面与 import 路径

```dart
import 'package:teacher_schedule/features/collaboration/reschedule_page.dart';
import 'package:teacher_schedule/features/collaboration/reschedule_requests_page.dart';
import 'package:teacher_schedule/features/collaboration/classroom_search_page.dart';
import 'package:teacher_schedule/features/collaboration/classroom_usage_page.dart';
import 'package:teacher_schedule/features/collaboration/inquiry_compose_page.dart';
import 'package:teacher_schedule/features/collaboration/inquiry_list_page.dart';
import 'package:teacher_schedule/features/collaboration/inquiry_detail_page.dart';
import 'package:teacher_schedule/features/collaboration/notification_list_page.dart';
import 'package:teacher_schedule/features/free_rooms/free_rooms_page.dart';
```

| 页面 | 构造函数 | 用途 |
| --- | --- | --- |
| `ClassroomInquiryPage` | `ClassroomInquiryPage({required ApiClient api})` | 教室记录与失物消息两个页签，底部常驻发起失物询问入口 |
| `ReschedulePage` | `ReschedulePage({required ApiClient api, required String sessionId})` | 查询候选时段、提交调课申请 |
| `RescheduleRequestsPage` | `RescheduleRequestsPage({required ApiClient api})` | 我的申请、草稿提交、撤回 |
| `ClassroomSearchPage` | `ClassroomSearchPage({required ApiClient api, ValueChanged<ClassroomItem>? onSelect, String title = '教室记录'})` | 默认进入使用记录；传入 `onSelect` 时复用为教室选择页 |
| `ClassroomUsagePage` | `ClassroomUsagePage({required ApiClient api, required String classroomId, DateTime? initialDate})` | 某日教室使用记录、教师联系方式、发起协查；`initialDate` 用于从空教室结果保留查询日期 |
| `FreeRoomsPage` | `FreeRoomsPage({required ApiClient api})` | 空教室查询：日期/时段/搜索条件、空闲与待核验结果、分页加载 |
| `InquiryComposePage` | `InquiryComposePage({required ApiClient api, String? classroomId, ClassroomItem? initialClassroom, DateTime? initialDate})` | 选择教室、时段与接收教师并发起失物询问；也支持从教室记录预填 |
| `InquiryListPage` | `InquiryListPage({required ApiClient api})` | 收到/我发起的协查列表 |
| `InquiryDetailPage` | `InquiryDetailPage({required ApiClient api, required String inquiryId})` | 标记已读、追加回复、发起人结案 |
| `NotificationListPage` | `NotificationListPage({required ApiClient api, void Function(String objectType, String objectId)? onOpenObject})` | 消息分页、已读、对象跳转回调 |

`ApiClient` 的 `baseUrl` 已包含 `/api/v1/`，页面中的 path 不再重复该前缀。

## 接口与交互

### 1. 调课

- 候选时段：`POST sessions/{sessionId}/reschedule-options/`，JSON body 为
  `date_from`、`date_until`、`allow_weekend=false`，可选 `classroom_ids`。
  **注意：后端实际是 POST（只读查询也走 POST），不是 GET；用 GET 会 405。** 初始范围为 14 天，页面可改日期范围和可选教室条件。
- 选项按 `available` / `unverified` / `conflict` 分组。只有可用和待核验选项可以被选中；已知冲突卡片不能提交。
- 可选项展示日期、节次、教室、学生冲突人数、`reasons`、`conflicts`、`missing_data`。
- 提交：`POST reschedule-requests/`，body 为
  `session_id`、`target_date`、`target_start_period`、`target_end_period`、`target_classroom_id`、`reason`、`submit: true`。
- 成功提示明确写“正式课表尚未改变，审批通过后才会生效”；待核验申请会提示需教务核验。409 冲突不会创建申请。
- 申请列表：`GET reschedule-requests/?scope=mine`。
  `draft` 可 `POST reschedule-requests/{id}/submit/`；`can_withdraw` 为真时可 `POST reschedule-requests/{id}/withdraw/`。操作成功后用接口返回的申请对象替换列表项。

### 2. 教室

- 功能页“常用工具”提供 **空教室查询** 和统一的 **教室与失物** 入口。后者包含“教室记录 / 失物消息”两个页签，底部均可直接点击 **发起失物询问**。
- 搜索：`GET classrooms/?q=...`，输入 300ms 防抖；成功后保留当前旧列表直至新数据返回。
- 使用记录：`GET classrooms/{id}/usage/?date=YYYY-MM-DD`，日期前后切换或日期选择，展示 `session`、`cancelled_session`、`moved_out`、`room_block` 条目。
- 页面固定展示“排定课程记录，不代表实际到场或物品去向。”；不把排课记录解释为实际到场。
- 教师联系方式：仅在 `has_visible_contact` 为真时请求 `GET teachers/{id}/contact/`。只展示接口返回且 `*_status=visible` 的字段；微信/电话均用 Flutter `Clipboard` 复制，电话标注“仅复制，不拨号”，没有新增拨号依赖。
- 教室详情中的“发起失物询问”进入 `InquiryComposePage` 并保留该教室与所选日期；无需查使用记录时，可从统一页底部直接进入填写。

#### 空教室查询

- 入口：功能页“常用工具”的 **空教室查询**，对应 `FreeRoomsPage(api: api)`；不新增 router 依赖。
- 请求：`GET classrooms/availability/`，查询参数为 `date`、`start_time`、`end_time`，可选 `q`、`campus`、`building`、`min_capacity`，以及 DRF 的 `page`。默认今天 `08:10–09:40`，首次进入自动按默认条件查询；也允许选择历史日期。
- 页面区分 **空闲** 与 **待核验**，计数直接使用接口的 `available_count`、`unverified_count`；待核验不写成“空教室”或已确认空闲。
- 结果按接口分页，点“加载更多”请求下一页，并按教室 id 去重；卡片显示教室代码、位置、容量、空闲/待核验状态。
- 修改日期、时间、搜索词、校区、楼宇或最小容量后，旧结果立即清空并提示重新查询，避免把旧结果伪装成新条件的结果。
- 查询按钮固定在页面底部，键盘弹出时仍保持可见；表单和结果区域独立滚动。
- 点结果进入 `ClassroomUsagePage(..., initialDate: 查询日期)`，因此打开的是所查询日期的教室历史，不是固定今天。
- 页面只读：不预约、不审批、不修改课表，也不显示接口内部参数名或版本字段。

### 3. 失物协查

- 从统一页或独立失物消息页的底部按钮直接发起时，先选择教室，复用现有教室目录与搜索；默认今天 `08:10–17:10`。未选教室时不查询相关教师。切换教室后清除旧的候选和勾选，保留物品描述与时间。
- 发送成功回到“失物消息 → 我发起的”并刷新列表；原有从教室记录发起的预填路径继续保留。
- 候选：`GET inquiries/candidates/?classroom_id=&from_at=&to_at=`。时间以带本地时区偏移的 ISO 发送；页面限制结束晚于开始且跨度不超过 7 天。
- 发起：`POST inquiries/`，body 为 `classroom_id`、`from_at`、`to_at`、`item_description`、`recipient_ids`。
  **只提交勾选的 `recipient_ids`**，不会默认全选，也不会把未勾选教师发给后端。
- 列表：`GET inquiries/?box=received` 或 `?box=sent`。切换 tab 优先使用缓存，下拉刷新保留旧列表直至新数据返回。
- 详情：进入后 `GET inquiries/{id}/`；非发起人再 `POST inquiries/{id}/read/` 记录首次查看。已读失败会显示提示，但仍展示可读取的详情。
- 回复：未结案且当前用户不是发起人时显示回复表单，`POST inquiries/{id}/replies/`，body 为 `result`（`not_seen`/`in_custody`/`other`）和可选 `body`；`other` 必填文字。成功后用返回对象追加显示，不覆盖历史。
- 结案：发起人且未结案时可 `POST inquiries/{id}/close/`，body 为可选 `close_note`；结案后隐藏回复表单并保留全部历史。
- 页面没有即时聊天、WebSocket 或轮询。

### 4. 消息

- 首屏：`GET notifications/?limit=20`；加载更多：带 `cursor=<next_cursor>` 再请求。按 `id` 去重，`next_cursor` 为空或未变化时不再显示加载更多，避免重复请求同一游标。
- 点击未读消息先 `POST notifications/{id}/read/`，成功后只在本地替换该消息；失败不伪装成已读。
- `onOpenObject` 存在且 `objectId` 非空时，把 `objectType`/`objectId` 交给主路由跳转；回调为空时打开消息正文详情，**不会假跳转**。
- 页头显示 `unread_count`，下拉刷新重置列表。

## 主路由接线建议

```dart
NotificationListPage(
  api: api,
  onOpenObject: (objectType, objectId) {
    switch (objectType) {
      case 'session':
        // push 现有课次/课表详情
      case 'reschedule_request':
        // push RescheduleRequestsPage 或对应申请详情
      case 'inquiry':
        // push InquiryDetailPage(api: api, inquiryId: objectId)
      default:
        // 保持不跳转；页面自身会显示消息详情
    }
  },
)
```

从课次详情进入调课：

```dart
Navigator.of(context).push(
  MaterialPageRoute<void>(
    builder: (_) => ReschedulePage(api: api, sessionId: sessionId),
  ),
);
```

从“教室/协查”入口：

```dart
Navigator.of(context).push(
  MaterialPageRoute<void>(
    builder: (_) => ClassroomSearchPage(api: api),
  ),
);
```

`ClassroomUsagePage` 内部已经会 push `InquiryComposePage`；从 `InquiryComposePage` 成功发起后返回 `true`，调用方只需提示或刷新。

## 体验与性能

- 所有请求在 `initState` 或用户动作中发起，`build` 不发请求。
- 搜索 300ms 防抖；`ClassroomSearchPage` 和列表刷新保留旧内容并显示顶部进度条；刷新失败用 SnackBar 提示。
- 空教室筛选条件一旦变化就清空旧结果，避免旧数据对应新条件；查询按钮固定在底部，表单在键盘弹出时仍可滚动。
- 列表使用稳定 `ValueKey`；消息分页按 id 去重；没有新增依赖。
- 主题沿用 `buildAppTheme()` 的浅蓝绿 Material 3 风格；页面在 `MaterialApp` 下直接使用。
- 日期展示为本地时间；提交给后端的日期时间为 `YYYY-MM-DD` 或带 `+08:00` 等本地偏移的 ISO 字符串。

## 本轮验证

在 `D:/class schedule/mobile` 执行：

```powershell
D:\tools\flutter\bin\flutter.bat analyze lib/features/collaboration test/features/collaboration
D:\tools\flutter\bin\flutter.bat test test/features/collaboration
```

结果：analyze 无问题；3 个协作合同测试通过：

1. 已知冲突时段不能选中提交（选中可用时段后按钮才启用）；
2. 协查只提交勾选的 `recipient_ids`；
3. 消息加载更多不会重复请求同一游标。

空教室新增 3 个测试：

```powershell
D:\tools\flutter\bin\flutter.bat analyze lib/features/free_rooms test/features/free_rooms lib/features/offerings/features_page.dart lib/features/collaboration/classroom_usage_page.dart
D:\tools\flutter\bin\flutter.bat test test/features/free_rooms
```

覆盖查询日期/时间/容量/分页参数、空闲/待核验渲染、点击结果按查询日期打开教室历史。

未做：整 App 构建、真机/Android 模拟器手测、与真实后端账号的端到端联调。主工程其他并行页面未改动。
