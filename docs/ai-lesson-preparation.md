# AI 课前备课助手

本轮新增一条本地演示闭环：教师在本人课次详情打开“AI备课”，填写本节主题，由本机 Django 调用部署配置的兼容 chat completions 服务生成四段可编辑内容，教师修改后保存为本人该课次的备课草稿。

App 不直接访问模型服务，也不在 APK 中保存任何密钥。链路为：

```
Flutter App → 本机 Django（JWT 认证、上下文组装、提示词）
            → 兼容 chat completions API（部署环境配置）
```

AI 只生成备课文字，**不参与排课、调课冲突、学时或正式课表计算**，也不替代教师确认。

## 接口合同

三个接口都需要登录，且只允许该课次的**实际授课教师**操作；沿用教学备注的 `require_active_teacher` / `get_session_for_notes` / `require_note_write_access`。

### `GET /api/v1/sessions/{id}/preparation/`

```json
{
  "context": {
    "course_name": "刑事证据审查实务（示例）",
    "date": "2026-10-28",
    "start_period": 1,
    "end_period": 2,
    "teaching_minutes": 80,
    "previous_note": "继续上次案例讨论，补充两道练习。",
    "plan_items": [{"title": "虚构专题：证据链审查", "planned_hours": "4.0"}]
  },
  "draft": null,
  "ai_available": true
}
```

- `teaching_minutes` 只按 `课次计划学时 × 开课每学时分钟数` 计算，**不含课间**（例如 2 × 40 = 80）。
- `previous_note` 复用课表卡片的备注摘要规则（`previous_note_previews`），本人私有草稿同样可读，不要求更早课次已完成。
- `plan_items` 取该开课当前未归档的教学计划专题，最多 5 条。
- `draft` 为 `null` 或已保存的草稿对象，字段见下。
- `ai_available` 在缺少 base URL、模型名或密钥时为 `false`，页面据此提示未配置；普通课表不受影响。

### `POST /api/v1/sessions/{id}/preparation/generate/`

请求 `{"topic": "证据链审查"}`，返回：

```json
{
  "topic": "证据链审查",
  "outline": "…",
  "case_study": "…",
  "discussion_questions": "…",
  "preparation": "…",
  "generated_model": "deepseek-flash",
  "generated_at": "2026-10-28T09:00:00+08:00"
}
```

- **生成不写数据库**：不创建草稿、不修改备注、学时、交接或课表。
- 上下文只含课程名称、日期节次、授课分钟、教学计划专题、上一备注和教师主题，不发送学生名单、班级或校园 UUID。
- 提示词要求四段中文内容、合计约 800 字以内；课堂案例必须明确是虚构教学案例，不编造真实判例或文献编号。

### `PUT /api/v1/sessions/{id}/preparation/`

请求字段：`base_version`、`topic`、`outline`、`case_study`、`discussion_questions`、`preparation`，可选 `generated_model`、`generated_at`。

成功直接返回完整草稿对象：

```json
{
  "id": "…",
  "topic": "证据链审查",
  "outline": "…",
  "case_study": "…",
  "discussion_questions": "…",
  "preparation": "…",
  "version": 1,
  "generated_model": "deepseek-flash",
  "generated_at": "2026-10-28T09:00:00+08:00",
  "updated_at": "2026-10-28T09:01:00+08:00"
}
```

- 没有草稿时 `base_version` 必须为 0；已有草稿时必须等于当前 `version`，否则返回 `409 PREPARATION_VERSION_CONFLICT` 并附 `current`，App 保留本地输入。
- 允许不提 AI、直接手写保存：`generated_model` 用空字符串，`generated_at` 用 `null`。
- 保存只写本人该课次的备课草稿，不改教学备注、下次备注、课次状态、实际学时、交接和正式课表。

### 错误形状

```json
{"code": "AI_UNAVAILABLE", "detail": "AI 服务暂时不可用，请稍后重试。", "field_errors": {}}
```

| code | HTTP | 含义 |
| --- | --- | --- |
| `AI_NOT_CONFIGURED` | 503 | 缺少 base URL、模型名或密钥，未发起请求 |
| `AI_TIMEOUT` | 504 | 单次模型调用超过配置超时 |
| `AI_REQUEST_FAILED` | 502 | 服务返回非 2xx |
| `AI_UNAVAILABLE` | 502 | 网络错误或连接失败 |
| `AI_INVALID_RESPONSE` | 502 | 响应不是 JSON、缺段或空段 |
| `PREPARATION_VERSION_CONFLICT` | 409 | 草稿已被其他设备更新 |
| `NOT_SESSION_TEACHER` / `NOT_FOUND` | 403 / 404 | 不是本人课次或不可见 |

任何失败都不会写入草稿，也不会返回固定样例冒充生成结果。

## 部署配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TEACHING_AI_API_BASE_URL` | `https://api.deepseek.com` | 兼容服务的 base（可含 `/v1`），代码会拼接 `/chat/completions` |
| `TEACHING_AI_MODEL` | `deepseek-flash` | 服务端模型名 |
| `TEACHING_AI_API_KEY` | 空 | 留空即功能未配置 |
| `TEACHING_AI_TIMEOUT_SECONDS` | `45` | 单次模型调用超时；低于 Gunicorn 的 60 秒 |

请求使用标准 `model` / `messages` / `stream: false` / `max_tokens: 2200` / `response_format: {"type": "json_object"}`。仅当 base 主机属于 `deepseek.com` 时才追加官方文档中的 `thinking: {"type": "disabled"}`，其他兼容服务不发送供应商扩展字段。请求同时发送真实应用标识 `User-Agent: TeacherScheduleDemo/1.0`（不伪装浏览器），以适配会拒绝默认 urllib 标识的兼容网关。

真实密钥只放在本机、已被忽略的配置中：

1. 复制 `infra/deploy/.env.example` 为 `infra/deploy/.env`（演示环境用已有的 `.env.demo`）。
2. 另建 `infra/deploy/.env.ai.local`（已被 `.gitignore` 忽略），写入三项配置：

   ```dotenv
   TEACHING_AI_API_BASE_URL=https://<兼容服务主机>/v1
   TEACHING_AI_MODEL=<可用模型名>
   TEACHING_AI_API_KEY=<真实密钥>
   ```

3. 启动演示时，`scripts/start-demo.ps1` 若发现 `.env.ai.local`，会在 `.env.demo` 之后再叠加该文件（后者覆盖同名变量）。文件缺失时照常启动，只是 AI 备课返回“未配置”。

Compose 已把上述变量传入 `api` 容器。不要把密钥写入 APK、公开 `.env.demo`、文档或日志；脚本失败信息也不打印 env 内容。

## 无模型时的状态

- `ai_available=false`，页面显示“AI 备课暂未配置”，不显示内部地址或密钥状态。
- 普通课表、备注、教学计划、登记学时、调课和交接完全不受影响。
- 手动编写并保存备课仍然可用（`generated_*` 留空）。

## 本地验证

后端自动化覆盖：真实上下文（80 分钟 + 上一私有短备注 + 计划标题真正进入请求体）、mock 模型有效 JSON、缺配置/超时/HTTP/坏 JSON 不假成功、保存重读、版本冲突、跨账号私有、以及生成与保存不改变备注/学时/课表。

```powershell
cd backend
uv run pytest apps/teaching/tests/test_lesson_preparation.py -q
uv run ruff check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --file /tmp/openapi.yaml --validate --fail-on-warn
```

2026-10-28 已在本地演示服务接通兼容 API，使用配置中的 `deepseek-v4.1-flash` 完成真实生成。教师甲“网络安全（示例）”生成四段共 452 字，用时约 9.4 秒；提纲时间合计 80 分钟，并承接上一备注中的案例与练习。确认生成前后草稿不变，再修改课前准备、保存并重新读取，四段内容与保存值一致。真实凭据仅保存在被忽略的本机配置。

后端 teaching 回归 67 项通过；兼容请求标识修复后，备课 8 项再次通过。Ruff、Django 系统检查、迁移差异和 OpenAPI 零警告校验通过。Flutter 全量 60 项通过，最终格式检查和 analyze 通过；Android release 已覆盖安装，实际完成另一门“电子数据取证”的生成、编辑、保存和返回重开。截图与详细结果见[本地演示验收](local-demo-acceptance.md)追加记录。
