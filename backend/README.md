# 后端开发指南

## 前置条件

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.11 或更高（`uv run --env-file` 需要较新版本）
- Docker Desktop（或 Docker Engine + Docker Compose）

## 本地启动

以下命令均在 `backend/` 目录下执行，除非特别说明。

### 1. 启动 PostgreSQL 与 Mailpit

```bash
# 在仓库根目录执行
docker compose -f infra/compose.yaml up -d
```

默认凭据（可在 `infra/compose.yaml` 或 shell 环境变量中覆盖）：

| 变量                | 默认值         |
| ------------------- | -------------- |
| `POSTGRES_USER`     | `schedule_dev` |
| `POSTGRES_PASSWORD` | `schedule_dev` |
| `POSTGRES_DB`       | `schedule_dev` |
| `POSTGRES_PORT`     | `5432`         |
| `MAILPIT_SMTP_PORT` | `1025`         |
| `MAILPIT_HTTP_PORT` | `8025`         |

等待数据库变为 healthy：`docker ps --filter name=infra-postgres`。Mailpit 使用固定镜像
`axllent/mailpit:v1.32.0`，开发邮件不会真正外发：

- 邮件列表与阅读：<http://127.0.0.1:8025>
- HTTP API（脚本取信）：<http://127.0.0.1:8025/api/v1/messages>
- SMTP：`127.0.0.1:1025`，默认无需账号密码或 TLS。

若本机 `1025` 已被其他 SMTP 服务占用，可用
`MAILPIT_SMTP_PORT=1026 docker compose -f infra/compose.yaml up -d` 映射宿主机端口，
并把 `.env` 的 `EMAIL_PORT` 同步改为 `1026`；容器内 Mailpit 仍监听 1025。

升级 PostgreSQL 16 补丁版本并复用旧数据卷后，若迁移或测试报 `collation version mismatch`，是新镜像的系统排序规则库版本变化所致，执行一次即可：

```bash
docker compose -f infra/compose.yaml exec postgres psql -U schedule_dev -d postgres \
  -c "ALTER DATABASE template1 REFRESH COLLATION VERSION" \
  -c "ALTER DATABASE postgres REFRESH COLLATION VERSION" \
  -c "ALTER DATABASE schedule_dev REFRESH COLLATION VERSION"
```

### 2. 复制环境变量文件

```bash
cd backend
cp .env.example .env
```

`.env.example` 中的值只用于本地开发，**不要**写入真实凭据。

### 3. 安装依赖

```bash
uv sync --locked
```

### 4. 运行迁移

```bash
uv run --env-file .env python manage.py migrate
```

### 5. 启动开发服务器

```bash
uv run --env-file .env python manage.py runserver
```

- API：<http://127.0.0.1:8000/api/v1/health/>
- OpenAPI：<http://127.0.0.1:8000/api/v1/schema/>
- 账号接口：见下文“账号与认证”
- 学期与校历：`GET /api/v1/terms/`、`GET /api/v1/terms/{id}/calendar/`
- 课表：`GET /api/v1/sessions/`、`GET /api/v1/sessions/{id}/`
- 教学班开课：`GET /api/v1/offerings/`
- 调课候选：`POST /api/v1/sessions/{id}/reschedule-options/`（详见下文）
- Admin：<http://127.0.0.1:8000/admin/>

## 账号与认证（B01/B10 后端）

账号端点由 dj-rest-auth 7.2.0 + django-allauth 65.20.1 提供，JWT 由
SimpleJWT 5.5.1 签发；项目没有自写 JWT、密码哈希或邮件 token 算法。

| 方法与路径 | 说明 |
| --- | --- |
| `POST /api/v1/auth/registration/` | 字段 `email`、`password1`、`password2`、`formal_name`、`teacher_no`；只发送验证邮件，不返回 token |
| `POST /api/v1/auth/registration/verify-email/` | 提交邮件中的 `key` |
| `POST /api/v1/auth/registration/resend-email/` | 重发验证邮件 |
| `POST /api/v1/auth/login/` | `email` + `password`；邮箱未验证返回明确的 400 |
| `POST /api/v1/auth/logout/` | 需要 access token；请求体 `refresh` 加入 blacklist |
| `POST /api/v1/auth/token/refresh/` | 旋转 refresh；旧 refresh 随即失效 |
| `POST /api/v1/auth/password/reset/` | 发送重置邮件 |
| `POST /api/v1/auth/password/reset/confirm/` | `uid`、`token`、`new_password1`、`new_password2` |
| `POST /api/v1/auth/password/change/` | 已登录修改密码；成功后要求重新登录 |
| `GET/PATCH /api/v1/me/` | 本人资料、邮箱/教师状态、当前学期和联系方式可见范围 |
| `POST /api/v1/me/deactivation/` | 停用账号、撤销全部 refresh、清空联系方式，保留教学历史 |

认证默认使用 SimpleJWT `JWTAuthentication`，同时保留 `SessionAuthentication`
供 Admin/可浏览 API。access 有效期 30 分钟，refresh 14 天；refresh 旋转并把旧值加入
blacklist。移动端把 token 保存在系统安全存储；接口不使用 httpOnly cookie，因此登录和
刷新响应会返回 token 本身。修改密码、重置密码确认和停用会通过
`revoke_all_refresh_tokens` 把该用户所有 `OutstandingToken` 加入 blacklist。访问 token
不存入 blacklist，仍可能在其 30 分钟有效期内可用；客户端收到“重新登录”后必须丢弃本地
token。

### 教师身份关联

注册时写入 `TeacherClaim(status=pending)`。邮箱验证成功（allauth
`email_confirmed` signal）后，只有同一学校、`teacher_no` 与归一化邮箱都匹配、档案
启用且尚未关联账号的**恰好一个** `TeacherProfile` 才会自动关联；此时档案状态置为
`active` 且 `assignment_scope_version` 加 1。只匹配工号、只匹配邮箱、同名或档案已被占用
都保持 pending，交教务处理；绝不按姓名自动关联。

Admin 的“教师身份申报”列表支持按状态筛选和两个动作：

- **批准并关联到所选教师档案**：要求已填写 `matched_profile`，且该档案未停用、未被占用。
- **驳回**。

两个动作与自动匹配共用 `apps.accounts.services` 中的事务服务。`GET /api/v1/me/` 的
`teacher_status` 为 `active / pending / disabled / unlinked`；未关联教师调用需要
`TeacherProfile` 的接口返回 403 `TEACHER_NOT_LINKED`，教师档案停用时返回 403
`TEACHER_DISABLED`。`PATCH /me/` 对未关联档案返回 400 `TEACHER_NOT_LINKED`，对停用档案
返回 403 `TEACHER_DISABLED`。

### 邮件与 App 深链

本地开发使用 Mailpit 捕获邮件：

1. `docker compose -f infra/compose.yaml up -d` 启动 Mailpit。
2. 注册或找回密码后打开 <http://127.0.0.1:8025> 阅读邮件。
3. 脚本可通过 <http://127.0.0.1:8025/api/v1/messages> 取信。

邮件模板为中文，验证邮件同时包含
`{APP_LINK_BASE}verify-email?key=...` 和可复制的原始 key；重置邮件同时包含
`{APP_LINK_BASE}reset-password?uid=...&token=...` 和可复制的 uid + token，便于在另一台
设备打开邮件后手工粘贴。`APP_LINK_BASE` 可设为自定义 scheme（示例
`teacherschedule://`）或 `https://<域名>/app`；项目不提供网页回跳页。

SMTP 参数全部来自环境变量。测试使用 Django locmem backend 和
`django.core.mail.outbox`，不连接外部 SMTP；真实学校 SMTP 投递尚未在本仓库验证，
本地 Mailpit 的 SMTP 收信已在手工流程中验证。

## 运行检查命令

```bash
uv run --env-file .env ruff format --check .
uv run --env-file .env ruff check .
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pytest
```

大规模性能测试默认不运行（`pyproject.toml` 中 `addopts` 排除了 `perf` marker）。单独运行：

```bash
uv run --env-file .env pytest -m perf
```

`ruff` 与 `pytest` 本身不读环境变量，但统一加 `--env-file` 可让命令形式一致；`manage.py`、`pytest`（经 pytest-django 加载 Django settings）必须有这些变量。

## 关于 `.env` 的说明

项目**不引入** `django-environ` 之类的配置库，`config/settings.py` 只读标准库 `os.environ`。
`.env` 由 `uv run --env-file .env ...` 在启动进程时注入，而不是由 Django 在运行时解析。
因此直接 `uv run python manage.py check` 而不带 `--env-file` 会报缺少环境变量——这是预期行为。

在部署环境（systemd、容器编排等）应通过真实环境变量注入，不需要 `.env` 文件。

## 环境变量说明

| 变量                   | 必需 | 说明                                                              |
| ---------------------- | ---- | ----------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`    | 是   | Django `SECRET_KEY`；缺失时启动抛出明确 `RuntimeError`             |
| `DJANGO_DEBUG`         | 否   | `true`/`1`/`yes` 开启调试，默认关闭                                |
| `DJANGO_ALLOWED_HOSTS` | 否   | 逗号分隔；`DEBUG=false` 时必须非空                                 |
| `DATABASE_URL`         | 是   | `postgres://user:pass@host:port/db`；缺失时明确报错                |
| `DJANGO_EMAIL_BACKEND` | 否   | 默认 SMTP；测试/离线可改为 locmem 或 console backend               |
| `EMAIL_HOST`           | 否   | SMTP 主机，开发默认 `127.0.0.1`                                    |
| `EMAIL_PORT`           | 否   | SMTP 端口，开发默认 `1025`（Mailpit）                              |
| `EMAIL_HOST_USER`      | 否   | SMTP 用户名；Mailpit 本地默认留空                                  |
| `EMAIL_HOST_PASSWORD`  | 否   | SMTP 密码；不得提交真实值                                          |
| `EMAIL_USE_TLS`        | 否   | `true`/`false`，默认关闭                                           |
| `DEFAULT_FROM_EMAIL`   | 否   | 发件人，示例为虚构地址                                             |
| `APP_LINK_BASE`        | 否   | 邮件 App 深链前缀，默认 `teacherschedule://`；不要带查询参数       |

`DATABASE_URL` 缺失或为空时服务立即报错退出，**不回退到 SQLite**。

> Windows 提示：本机 `localhost` 曾导致 psycopg 连接挂起，`.env.example` 因此使用
> `127.0.0.1`。Linux/macOS 上两者等价。

## 目录结构

```text
backend/
├── config/              # 项目配置（settings、urls、wsgi/asgi、health 视图）
│   └── tests/
├── templates/
│   └── account/email/   # 中文验证/重置邮件（App 深链 + 可复制参数）
├── apps/
│   ├── common/          # UUID 基类、统一教师状态异常
│   ├── accounts/        # 自定义 User、TeacherClaim、JWT 账号接口
│   │   ├── adapters.py  # 中文邮件上下文、App 深链、无网页回跳
│   │   ├── serializers.py # 注册、登录、撤销钩子、me
│   │   ├── services.py  # 教师关联、全部 refresh 撤销、停用
│   │   ├── signals.py   # email_confirmed 后尝试自动关联
│   │   ├── views.py     # me、停用、退出、改密响应
│   │   ├── urls.py      # 账号与 me 路由
│   │   ├── forms.py     # Admin 新增/编辑表单
│   │   ├── migrations/
│   │   └── tests/
│   ├── academics/       # 学校/学期/节次/校历、教师/学生、课程/开课/选课/覆盖
│   │   ├── selectors.py # 按日期有效的实际选课查询
│   │   ├── admin.py
│   │   └── migrations/
│   ├── classrooms/      # 教室目录与教室非课程占用
│   │   ├── admin.py
│   │   └── migrations/
│   └── scheduling/      # 排课规则、课次、授课教师、人员不可用、同步状态
│       ├── calendar.py  # 教学周与规则展开（纯函数，不查数据库）
│       ├── services.py  # 课次生成事务服务（统一锁顺序）
│       ├── selectors.py # B13 可见范围、预取与当前学期查询
│       ├── reschedule_options.py # 只读调课候选、冲突和覆盖检查
│       ├── serializers.py        # 课表读取、调课候选请求/响应/错误 schema
│       ├── views.py     # 课表读取、调课候选查询接口
│       ├── urls.py      # 课表读取与调课候选路由
│       ├── admin.py
│       ├── migrations/
│       └── tests/       # 样例学期、查询数/接口/性能测试与共享造数 helper
├── manage.py
├── pyproject.toml
├── uv.lock
└── .env.example
```

## 课次生成

B11 只实现“按规则展开课次”，不包含导入器（B12）和正式调课（B31）。

- Admin 的“学期”列表提供动作 **按规则生成课次**，调用 `apps.scheduling.services.generate_sessions_for_term`；事务内先锁 `SchoolSyncState`，再锁 `Term`。
- 已存在的生成键（规则 + 原教学日期 + 原起始节）不会被改写，差异只计入 `mismatched` 供后续导入预览处理；新建课次时 `Term.schedule_version` 加 1。
- `ClassSession` 在 Admin 中仅可查看（不能新增、修改、删除）：完成量更正需要原因并走 B22 教学服务，正式课表变更走 B31 统一服务。
- 校历 `cancel`/`custom`/`replace` 目标日上的基础课次保留为 `cancelled`，不删除。

## 课表查询（B13）

以下接口均需 JWT，且账号必须关联启用中的教师档案。可见课次由
`apps.scheduling.selectors.visible_sessions_for_teacher` 统一决定：本人是该课次的
`SessionTeacher`、本人是该 offering 的有效 `TeachingAssignment` 教师，或本人有效任课于该课次所属
教学班的任一 offering 时可见。有效任课关系指 `effective_until` 为空或晚于学校时区下的今天；
`effective_from` 不限制，即将开始的任课也可提前查看。`is_mine` 只表示第一种情况；任课关系结束后，
本人作为 `SessionTeacher` 上过的课次仍可见。不在可见范围的课次详情返回 404，不用 403 暴露对象
存在性。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/terms/` | 本校全部学期，按开始日期倒序；`is_current` 按该学期学校时区判断 |
| `GET /api/v1/terms/{id}/calendar/` | 学期字段、`periods`、`exceptions` 与 `weeks`；一个学期一套节次，不再提供 `period-schemes/` |
| `GET /api/v1/sessions/` | 日期范围内可见课次；见下方参数说明，不分页 |
| `GET /api/v1/sessions/{id}/` | 课次摘要加计划/实际学时、完成时间、有效选课人数、课程目标学时与负责人 |
| `GET /api/v1/offerings/` | 本人任课及同教学班其他 offering；DRF 标准分页，page_size 50 |

`sessions/` 的查询参数：

- `from`、`to`：学校本地日期，半开区间 `[from, to)`，必填，跨度 1–42 天；缺失、倒置或超限返回
  400 `INVALID_DATE_RANGE`。
- `scope`：`mine`（默认，只返回 `is_mine` 课次）、`offering`（需 `offering_id`）、
  `teaching_class`（需 `teaching_class_id`）。指定对象不可见时返回 404。
- `include_cancelled`：默认 true，周课表据此显示“已取消”；传 false 时排除已取消课次。

列表响应的 `term` 取 `from` 所在学期；`from` 不在任何学期内时返回 `term=null` 和空
`results`。课次按 `start_at`、课程代码稳定排序。`date`、`weekday`、`teaching_week` 均按课次
**实际**学校本地日期计算；因此 2026-10-05 的停课/调休安排实际在 10-10 上课时，返回
`date=2026-10-10`、`weekday=6`、`teaching_week=5`（该日期确实是第 5 周周六），
`origin_teaching_date=2026-10-05`、`is_moved=true`。`is_moved` 在存在原始教学日期且实际日期或
开始节次与原始值不同时为 true。

`offerings/` 的 `term_id` 可选；默认当前学期，没有当前学期则返回空页。`is_assigned` 表示本人是否
在该 offering 任课，同教学班其他课程为 false。旧计划中的
`GET /offerings/{id}/sessions/` 由 `GET /sessions/?scope=offering&offering_id=` 替代。

读接口使用 `select_related` / `prefetch_related` 固定查询数：`scope=teaching_class` 的周课表在
一周或六周范围均为 6 条查询（含 JWT 用户与教师档案查询），不随课次数增长。`pytest -m perf`
用 20,000 课次规模验证 `scope=teaching_class` 一周请求。

## 调课候选查询（B30）

`POST /api/v1/sessions/{id}/reschedule-options/` 只读返回候选时段、候选教室、教师/学生/教室冲突、缺失数据类别和稳定排序，不创建调课申请、不修改课次。仅该课次的 `SessionTeacher` 或该 offering 的有效 `TeachingAssignment` 教师可查询；未登录返回 401，非任课教师返回 403，未关联教师档案返回 403 `TEACHER_NOT_LINKED`，教师档案停用返回 403 `TEACHER_DISABLED`，课次不存在返回 404。

请求字段：`date_from`、`date_until`（半开区间；默认今天至 `date_from + 14 天`）、`allowed_block_codes`、`classroom_ids`、`allow_weekend`。源课次非 `scheduled` 返回 409 `SESSION_NOT_RESCHEDULABLE`；日期区间无效返回 400 `INVALID_DATE_RANGE`，两者都使用 `{"code", "detail", "field_errors"}`。OpenAPI 合同见 `/api/v1/schema/`。

## 常用开发命令

```bash
# 创建超级用户（用邮箱）
uv run --env-file .env python manage.py createsuperuser

# 生成迁移
uv run --env-file .env python manage.py makemigrations

# 仅格式化
uv run ruff format .
```
