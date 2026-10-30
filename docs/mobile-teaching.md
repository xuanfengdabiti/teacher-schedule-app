# Flutter 教学计划、授课完成与交接页面

> 本文为模块交接记录。最终主路由已接线，Android release 已实际验收计划保存、2.0 学时登记和交接确认；见 [最终验收](local-demo-acceptance.md)。

> 本轮只新增 `mobile/lib/features/teaching/` 与对应测试；未改 `pubspec.yaml`、`core`、`main.dart`、路由或其他 feature。页面使用现有 Material 主题、`ApiClient`、`shared/status_views.dart`，不新增依赖。

## 1. 路由接线入口

以下四个页面都只要求传入现有 `ApiClient`，不依赖全局 Riverpod provider，可由主工程直接创建路由或从已有课次/课程详情页 push。

| 页面 | 导入路径 | 构造参数 |
| --- | --- | --- |
| 教学计划 | `package:teacher_schedule/features/teaching/teaching_plan_page.dart` | `TeachingPlanPage(api: api, offeringId: offeringId)` |
| 授课完成 | `package:teacher_schedule/features/teaching/completion_page.dart` | `CompletionPage(api: api, sessionId: sessionId)` |
| 交接列表 | `package:teacher_schedule/features/teaching/handoff_list_page.dart` | `HandoffListPage(api: api)` |
| 发起交接 | `package:teacher_schedule/features/teaching/handoff_compose_page.dart` | `HandoffComposePage(api: api, sourceSessionId: sourceSessionId)` |

可选测试/复用入口（不需要路由）：

- `teaching_repository.dart` 的 `TeachingRepository` 实现 `TeachingApi` 抽象接口。
- `teaching_models.dart` 的 `PlanBundle`、`CompletionDetail`、`CompletionRequest`、`Handoff`、`HandoffPrefill` 等合同模型。
- `teaching_widgets.dart` 的 `TeachingMetric`、`TeachingStatusPill`、`TeachingNotice`。

接入示例（由主工程决定具体命名）：

```dart
Navigator.of(context).push(
  MaterialPageRoute(
    builder: (_) => TeachingPlanPage(api: api, offeringId: offeringId),
  ),
);
```

`HandoffComposePage` 的 `sourceSessionId` 必须来自当前课次详情；页面会额外请求 `sessions/{id}/` 取得 `offering_id`，因为 `handoffs/prefill/` 响应只给出接收人和备注预填，不包含 offering id。

## 2. API 合同与行为

### 2.1 教学计划

- GET `offerings/{id}/plan/` -> `{plan, progress, can_edit}`。
- PUT `offerings/{id}/plan/` -> `{base_version, items:[{id?, title, planned_hours, due_date?, due_session_id?}]}`。
- 页面只显示服务端 `progress`（完成/剩余/超出学时、剩余课次、过去待确认、未归入专题、`plan_status`、到期差额），不在手机端另算一套学时。
- `planned_hours` 始终按字符串提交，不转 double。
- `can_edit=true` 时可新增/编辑标题和学时/删除/选择截止日期；已有 `due_session_id` 的专题在未选日期时保留课次截止，选择日期后改为日期截止。
- `base_version` 取当前 `plan.version`；没有计划时为 0。
- 保存 409 时保留本地编辑并提示“计划版本已变化”，用户可保留输入或主动载入服务器版本，不会静默覆盖。
- `plan=null` 显示“未建立计划”和创建入口；`can_edit=false` 只读显示。

### 2.2 授课完成

- GET/PUT `sessions/{id}/completion/`。
- GET 展示服务端 `status`、`version`、`actual_hours`、完成人/时间、当前专题分配、可分配专题和历史。
- PUT body：`{action: complete|correct|revert, base_version, actual_hours, allocations:[{plan_item_id, actual_hours}], reason}`。
- `actual_hours` 和分配学时都以字符串发送；页面只做基本非负/一位小数校验，最终以服务端 Decimal 校验为准。
- `complete` 使用“确认完成”按钮；`completed` 后显示“保存更正”和“撤销完成”。更正、撤销都要求原因，原因在页面校验后才请求。
- 保存备注不会自动调用本页；页面不修改备注。
- 409 时保留输入，提示版本变化；用户可主动刷新服务端状态。
- 成功后用返回的 `CompletionDetail` 刷新页面（不另造本地进度算法）；由上层页面返回时重新拉取即可。

### 2.3 交接列表与详情

- GET `handoffs/?box=inbox|sent` 使用 DRF 分页，当前页面读取第一页并显示 total；收件/发件用分段按钮切换。
- 打开收件条目：先 GET 详情，再按需 POST `handoffs/{id}/read/`；**不自动 POST acknowledge**。
- 已发出的条目打开时不调用 read 接口。
- 只有 `can_acknowledge=true` 显示“确认已接收”；只有 `can_withdraw=true` 显示“撤回交接”；点击后必须二次确认才 POST 对应动作。
- 详情只读展示正文、发送人、接收人、来源/目标课次、备注发布版本、状态、已读/确认/撤回时间。
- 操作成功会更新详情，并同时更新底层列表中的同一条目；返回列表不会伪造状态。
- 列表刷新保留旧内容；首次加载显示 loading，失败显示 retry；空收件/发件有明确空状态。

### 2.4 发起交接

- GET `handoffs/prefill/?source_session_id=...`，并并行 GET `sessions/{source_session_id}/` 取得 `offering_id`。
- 展示同 offering 候选接收人，**默认不勾选任何人**；`is_suggested` 仅作提示，不会自动选人。
- 下节课卡片独立展示日期、节次和“下节授课”名单，姓名来自 `suggested_target_teachers`，包含下节仍由本人授课的情况；多人授课全部显示。可选接收人中的下节授课教师另有标签，与是否勾选区分。
- 预填已发布备注的 `content`；没有发布备注时为空。
- 建议目标课次显示为可关闭的开关；关闭时不提交 `target_session_id`。
- POST `handoffs/` body：`offering_id, recipient_ids, content, source_session_id, target_session_id?, note_id?`。
- 发送按钮在未选接收人时禁用；请求失败保留输入并显示服务端错误；成功弹窗确认后才返回 `true`。
- 页面不做离线队列或本地伪造成功；发送必须在线成功。

## 3. 文件与测试

新增源码：

```text
mobile/lib/features/teaching/
  teaching_models.dart
  teaching_repository.dart
  teaching_controllers.dart
  teaching_widgets.dart
  teaching_plan_page.dart
  completion_page.dart
  handoff_list_page.dart
  handoff_compose_page.dart
```

新增测试：

```text
mobile/test/features/teaching/teaching_actions_test.dart
```

覆盖的关键合同：

- 收件箱详情加载只调用 `read`，不调用 `acknowledge`；显式点击后才确认。
- 已发出交接不调用 `read`。
- 更正请求的 `actual_hours`/分配学时保持十进制字符串，原因必填。
- 原因为空时控制器在本地拒绝，不消耗服务端请求。

## 4. 已执行检查

```text
dart format lib/features/teaching test/features/teaching
→ 已格式化，无额外变化

flutter analyze lib/features/teaching test/features/teaching
→ No issues found

flutter test test/features/teaching/teaching_actions_test.dart
→ 4 tests passed
```

另用本机预览服务手工核对过实际响应字段：`offerings/{id}/plan/` 的 `plan/progress/can_edit`、`sessions/{id}/completion/` 的状态/版本/可用专题、`handoffs/prefill/` 的接收人/建议课次/备注字段、`sessions/{id}/` 的 `offering_id` 均与模型一致。测试账号与真实数据未写入代码或测试。

## 5. 边界

- 未改主路由、导航、登录、全局 provider；页面需要主工程接入后才对教师可见。
- 未实现交接列表的无限分页加载；当前显示第一页（DRF 默认 50 条）的 `count` 与列表。数据量增大后应补下一页。
- 未做本地缓存/离线提交；计划、完成和交接均以在线服务端结果为准。
- 未做真机视觉验收；`flutter analyze` 与页面测试不能代替 Android/iOS 实机字号、长文本和触控检查。
- 交接详情读取是“打开后标记已读”，与“确认接收”严格分开；该行为已在测试中锁定。
