# 后端个人设置与头像交付记录

> 本轮范围：仅 B10b（个人设置、首选学期、头像）。B40a 远程投递与 B42a 部署交付仍由后续任务负责；本文不把站内消息、真实推送或 Flutter 页面写成已完成。

## 1. 实际交付

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 偏好持久化 | 已实现 | `accounts.UserPreferences` 一对一账号；pending/unlinked 账号也可读写 |
| 偏好 API | 已实现 | `GET/PATCH /api/v1/me/preferences/`，PATCH 为部分更新 |
| 首选学期 | 已实现 | 只接受本校已有学期；`preferred_term_id: null` 清除 |
| 头像 | 已实现 | `POST/GET/DELETE /api/v1/me/avatar/`；`POST` 再次调用即替换 |
| `me` 头像引用 | 已实现 | `GET /api/v1/me/` 增加 `avatar_url` 绝对 URL，无头像为 `null` |
| 迁移 | 已生成 | `backend/apps/accounts/migrations/0003_user_preferences_avatar.py` |
| OpenAPI | 已通过 | `manage.py spectacular --validate --fail-on-warn` 无警告 |
| 第三方记录 | 已更新 | Pillow 13.0.0，用途仅为头像图片解码与格式校验 |

未实现：Flutter 设置页、头像裁剪/压缩、图像识别、远端对象存储、真实 SMTP/推送账号验证。

## 2. 偏好字段与默认值

`GET /api/v1/me/preferences/` 首次访问会按默认值创建 `UserPreferences` 行。

| 字段 | 类型 | 默认值 | PATCH 规则 |
| --- | --- | --- | --- |
| `font_size` | `system` / `large` / `extra_large` | `system` | 三值枚举 |
| `local_reminder_enabled` | boolean | `true` | 本地提醒开关 |
| `local_reminder_minutes` | integer | `15` | 0–180，表示提前分钟数 |
| `preferred_term` | object/null（只读输出） | `null` | 输出 `{id, code, name}`；`null` 表示跟随当前学期 |
| `preferred_term_id` | UUID/null（仅 PATCH） | — | 必须是本校已有学期；`null` 清除 |
| `push_enabled` | boolean | `true` | 远程推送总开关 |
| `push_schedule_changed` | boolean | `true` | 课表变更、调课结果类别 |
| `push_handoff` | boolean | `true` | 交接收到/确认/核对类别 |
| `push_inquiry` | boolean | `true` | 协查请求/回复类别 |
| `push_plan` | boolean | `true` | 教学计划落后类别 |
| `updated_at` | datetime | 自动 | 只读 |

约定：

- PATCH 只写请求中出现的字段；未提交的偏好不会被重置。请求中出现未知字段返回 400。
- `preferred_term_id` 必须属于账号教师档案的学校；pending 账号在单校部署中按唯一的 `School` 校验。多校且无档案时返回 400，不猜测学校。
- 联系方式仍走原 `PATCH /api/v1/me/`，只有 active 教师档案可改；本轮没有改变该合同。
- `GET/PATCH /api/v1/me/preferences/` 只要求 `IsAuthenticated`，因此 pending 账号可以设置本人偏好。已停用档案返回 403 `TEACHER_DISABLED`。

## 3. 头像合同

### 上传/替换

`POST /api/v1/me/avatar/`，`multipart/form-data`，字段名 `avatar`。

- 支持 JPEG、PNG、WebP；最大 2 MiB。
- 复用 Django `ImageField` + DRF `ImageField`，由 Pillow 校验图片内容，不做扫描或图像识别。
- 文件存到 `MEDIA_ROOT/avatars/<user_id>/<random>.<ext>`；重复上传会删除旧文件。
- 成功返回 `{"avatar_url": "..."}`，再次上传即替换。

### 读取

`GET /api/v1/me/avatar/`，需要 JWT。

- 返回当前用户自己的 `FileResponse`；无头像返回 404。
- 不配置、不路由公开的 `MEDIA_ROOT`；导入原件和头像共用私有目录，但只有本接口能按登录用户读取头像。
- 响应带 `Cache-Control: private, max-age=86400`；文件名每次替换都变化，`me` 返回的 URL 带 `?v=` 便于客户端刷新缓存。

### 删除

`DELETE /api/v1/me/avatar/`，幂等返回 204；没有头像时同样 204，并清理已存在文件。

## 4. 供后续推送模块调用的入口

B40a 的投递 worker 必须在真正发送前调用：

```python
from apps.notifications.preferences import allows_remote_push

if allows_remote_push(notification.recipient.user, notification.type):
    ...
```

映射与规则在 `apps/notifications/preferences.py`：

| 通知类型 | 偏好类别字段 |
| --- | --- |
| `schedule_changed`、`reschedule_result` | `push_schedule_changed` |
| `handoff_received`、`handoff_acknowledged`、`handoff_check` | `push_handoff` |
| `inquiry_received`、`inquiry_replied` | `push_inquiry` |
| `plan_behind` | `push_plan` |

`push_enabled` 与对应类别字段必须同时为真；未登记的类型默认不做远程推送。站内 `Notification` 的创建不依赖该函数，因此关闭远推不会丢站内消息。处理时再调用一次即可实现“入队后修改开关仍生效”；本轮不修改现有 `notify()` 的 pending 创建语义。

## 5. 代码位置

| 位置 | 内容 |
| --- | --- |
| `backend/apps/accounts/models.py` | `User.avatar`、`UserPreferences` |
| `backend/apps/accounts/preferences.py` | 默认行读取、学校解析 |
| `backend/apps/accounts/serializers.py` | 偏好/头像序列化与校验 |
| `backend/apps/accounts/views.py` | 偏好与头像 API |
| `backend/apps/notifications/preferences.py` | 类别映射与发送前读取入口 |
| `backend/apps/accounts/tests/test_preferences_and_avatar.py` | 偏好与头像真实测试 |

## 6. 实际验证记录

在独立测试库（环境 `DATABASE_URL` 指向 15432 的 `schedule_accounts`）上执行：

```text
uv run pytest apps/accounts/tests
→ 77 passed

uv run python manage.py check
→ no issues

uv run python manage.py makemigrations --check --dry-run
→ No changes detected

uv run python manage.py spectacular --file <tmp>/openapi.yaml --validate --fail-on-warn
→ 通过，无输出警告

uv run ruff check apps/accounts apps/notifications/preferences.py config
→ All checks passed

uv run ruff format --check <本轮修改文件>
→ 通过
```

覆盖用例：偏好默认值、部分 PATCH 保留未提交值、非法字号/分钟数/未知字段、pending 账号读写、本校学期设置与清除、跨校学期拒绝、停用档案 403、推送类别映射、无头像、上传/读取/替换/删除、非图片、超大文件、不支持格式、账号隔离、直接 `/media/` 路径不可访问。

## 7. 未验证与边界

- 没有 Flutter 设置页或图片选择器，本接口不能证明手机端已可操作。
- 没有接入真实对象存储或 CDN；当前仅使用部署环境的私有 `MEDIA_ROOT` 本地存储。
- 头像上限 2 MiB、JPEG/PNG/WebP 是当前产品边界；未做裁剪、旋转、EXIF 清理或压缩。
- 远程推送仍是后续 B40a 的职责；本轮只提供偏好字段与发送前读取入口，不发送任何远程通知。
- 本轮未演练 Docker 构建、SMTP、推送账号或真机，不将代码完成等同于这些外部集成完成。
