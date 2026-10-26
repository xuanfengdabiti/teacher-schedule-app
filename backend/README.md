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
- 教学备注：`GET /api/v1/sessions/{id}/notes/`、`PUT /api/v1/sessions/{id}/notes/mine/`、`POST /api/v1/sessions/{id}/notes/mine/publish/`（详见下文）
- 教学交接：`GET /api/v1/handoffs/prefill/?source_session_id=`、`POST/GET /api/v1/handoffs/`、`GET /api/v1/handoffs/{id}/`、`POST /api/v1/handoffs/{id}/read/`、`POST /api/v1/handoffs/{id}/acknowledge/`、`POST /api/v1/handoffs/{id}/withdraw/`（详见下文）
- 教学计划与进度：`GET/PUT /api/v1/offerings/{id}/plan/`
- 授课完成：`GET/PUT /api/v1/sessions/{id}/completion/`
- 调课候选：`POST /api/v1/sessions/{id}/reschedule-options/`（详见下文）
- 调课申请：`POST/GET /api/v1/reschedule-requests/`、`POST /api/v1/reschedule-requests/{id}/submit/`、`POST /api/v1/reschedule-requests/{id}/withdraw/`（详见下文）
- 离线同步：`GET /api/v1/sync/bootstrap/`、`GET /api/v1/sync/changes/?cursor=`
- 消息与设备：`GET /api/v1/notifications/`、`POST /api/v1/notifications/{id}/read/`、`PUT/DELETE /api/v1/devices/{installation_id}/`
- 数据导入：Admin 的“数据导入 → 导入批次 → 上传导入批次”（详见下文）
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
uv run --env-file .env python manage.py spectacular --file /tmp/openapi.yaml --validate --fail-on-warn
uv run --env-file .env pytest
```

OpenAPI 校验必须零警告零错误；CI 在 pytest 之前执行同一命令。

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
| `PLAN_BEHIND_THRESHOLD_HOURS` | 否 | 教学计划落后阈值，Decimal 字符串，默认 `2.0`；不能使用 float |
| `DJANGO_MEDIA_ROOT`    | 否   | 私有上传目录；默认 `backend/var/media`，不得由 Web 服务器直接暴露 |

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
│   ├── notifications/  # 消息、推送设备与投递记录骨架
│   │   ├── services.py  # notify：事务内去重并建 pending 投递记录
│   │   ├── views.py     # 消息游标分页、已读、设备绑定/注销
│   │   ├── admin.py     # 三个模型只读列表
│   │   ├── migrations/
│   │   └── tests/
│   ├── imports/         # B12a CSV/XLSX 解析、校验、预览、首次/重复导入与身份映射
│   │   ├── parsing.py   # 固定模板解析；类型校验和行级错误
│   │   ├── validation.py # 日期、区间、校历、规则、课次等批内业务校验
│   │   ├── services.py  # PreviewResult / CommitResult、锁内重预览、事务写入
│   │   ├── storage.py   # MEDIA_ROOT/imports/<batch_id>/ 私有文件
│   │   ├── forms.py     # Admin 多文件上传表单
│   │   ├── admin.py     # 上传 -> 自动预览 -> 提交；身份映射只读列表
│   │   ├── migrations/
│   │   └── tests/       # 样例、重复导入、XLSX、错误定位、过期预览、Admin
│   └── scheduling/      # 排课规则、课次、授课教师、人员不可用、同步状态/变更日志
│       ├── calendar.py  # 教学周与规则展开（纯函数，不查数据库）
│       ├── changelog.py # ChangeEntry / record_changes，必须由调用方开启事务
│       ├── services.py  # 课次生成事务服务（统一锁顺序，生成时写变更日志）
│       ├── selectors.py # B13 可见范围、预取与当前学期查询
│       ├── sync.py      # bootstrap 一致快照与 changes 增量接口
│       ├── sync_serializers.py # /sync/ OpenAPI 响应 schema
│       ├── reschedule_options.py # 共享目标评估、只读调课候选、冲突和覆盖检查
│       ├── changes.py   # 正式调课唯一写入口（锁、再检查、快照、日志、通知）
│       ├── requests.py  # 调课申请状态机与服务端批准/驳回
│       ├── serializers.py        # 课表读取、调课候选、调课申请 schema
│       ├── views.py     # 课表、调课候选、调课申请接口
│       ├── urls.py      # 课表、调课、同步路由
│       ├── admin.py
│       ├── migrations/
│       └── tests/       # 样例学期、查询数/接口/性能测试与共享造数 helper
│   └── teaching/        # B20 备注、B21 交接、B22 计划/完成、B23 落后提醒
│       ├── models.py    # 备注、交接、计划、专题、修订、完成记录、幂等记录
│       ├── selectors.py # 备注/计划/完成权限、上次课次与可读范围
│       ├── services.py  # 备注保存草稿/发布事务、版本冲突与幂等
│       ├── plans.py     # B22 计划保存、归档与完整修订快照
│       ├── completion.py # B22 完成/更正/撤销、分配上限与并发锁
│       ├── progress.py  # 唯一进度算法（常数查询）
│       ├── reminders.py # B23 每日落后提醒服务
│       ├── hours.py     # Decimal 学时解析与一位小数输出
│       ├── management/commands/send_plan_reminders.py
│       ├── plan_serializers.py / completion_serializers.py
│       ├── progress_views.py # /offerings/{id}/plan/ 与 /sessions/{id}/completion/
│       ├── handoffs.py  # B21 交接创建/已读/确认/撤回、预填与调课提示
│       ├── handoff_serializers.py # 交接 API 合同与 JSON payload
│       ├── handoff_views.py # /handoffs/ 预填/列表/详情/状态动作
│       ├── admin.py     # 交接只读列表（筛选状态、搜索课程/教师）
│       ├── urls.py
│       ├── migrations/
│       └── tests/       # A10/A11/A12/A13/A14/A18/A23/A24/A25、计划/提醒/并发/查询数
├── manage.py
├── pyproject.toml
├── uv.lock
└── .env.example
```

## 课次生成

B11 实现“按规则展开课次”；B12a 的导入预演与提交直接复用这里的展开服务和锁顺序，不复制展开算法。字段更新型导入归 B12b。正式调课已由 B31 的统一写入口实现，见下文。

- Admin 的“学期”列表提供动作 **按规则生成课次**，调用 `apps.scheduling.services.generate_sessions_for_term`；事务内先锁 `SchoolSyncState`，再锁 `Term`。
- 已存在的生成键（规则 + 原教学日期 + 原起始节）不会被改写，差异只计入 `mismatched` 供后续导入预览处理；新建课次时 `Term.schedule_version` 加 1。
- 同一次生成事务内为每个新建课次写一条 `session` ChangeLog，并在有新建课次时写一条 `term` ChangeLog（version 为新的 `schedule_version`）；序号由 `apps.scheduling.changelog.record_changes` 在 `SchoolSyncState` 行锁下连续分配。
- `ClassSession` 在 Admin 中仅可查看（不能新增、修改、删除）：完成量更正需要原因并走 B22 教学服务，正式课表变更走 B31 统一服务。
- 校历 `cancel`/`custom`/`replace` 目标日上的基础课次保留为 `cancelled`，不删除。

**写入口约定：** 所有修改课次、学期或校历的写入口（B12 导入、B31 正式调课、B22 完成确认、Admin 修改校历）都必须在**已有事务内**调用 `record_changes`，并与对象/版本更新同事务提交。B31 的 `apply_session_change` 是当前正式调课的唯一写入口。`record_changes` 不再单独开启事务；这不是可选的审计日志，遗漏会让 App 增量同步永久跳过该变更。

## 数据导入（B12a）

B12a 支持两次明确的提交结果：**全新对象首次导入**，以及**同一批文件完全相同内容的重复导入**（全部行 `unchanged`，不写业务表、不写 ChangeLog）。文件里出现数据库中已有且字段不同的对象时，预览返回稳定错误码 `CHANGE_NOT_SUPPORTED_YET`，整批不可提交；更新、snapshot 和人工决定项属于 B12b。

### 管理后台操作

1. 启动 PostgreSQL/Mailpit、执行迁移并创建 Admin 账号。
2. 进入 **数据导入 → 导入批次 → 上传导入批次**。
3. 选择学校、填写稳定的 `source_system`（例如 `demo_csv_v1`）和 `source_updated_at`，一次选择本批 CSV/XLSX 文件；来源标识后续不能随意改名。
4. 保存后自动解析并预览。详情页显示逐文件 `new/unchanged/error` 统计和错误表（文件、工作表、物理行号、字段、原值、code、中文说明）；预览消息同时给出本次预计新建的规则课次数。
5. 有错误时修正源文件后重新上传；上传后的批次可用“重新预览”按当前数据库重新计算，但已提交批次不能重预览。
6. 只有 `previewed` 且无错误时显示 **提交导入**。提交在单个事务内先锁 `SchoolSyncState`，再锁学期，重新预览；若文件内容、行统计、错误或 `Term.schedule_version` 已变化，批次回到 `uploaded` 并提示重新预览，不沿用旧判断。
7. 失败会整体回滚，批次标为 `failed` 并在详情页显示原因；已提交批次保留原文件、统计、提交人和提交时间。

CSV 文件名或 XLSX 工作表名必须是 `terms`、`periods`、`teachers`、`classrooms`、`offerings`、`enrollments`、`calendar_exceptions`、`rules`、`sessions`、`room_blocks`、`resource_unavailability`、`coverage`。CSV 使用 UTF-8（可带 BOM）；XLSX 中编号必须是文本，公式单元格会被拒绝。

### 私有文件位置

原始文件保存在：

```text
${DJANGO_MEDIA_ROOT:-backend/var/media}/imports/<batch_uuid>/
```

生产环境应把 `DJANGO_MEDIA_ROOT` 指向仅服务进程可读写的私有卷，并做好备份。项目没有配置 `MEDIA_URL`，也不把该目录挂到公开 URL；不要用 nginx 直接暴露。测试通过 `tmp_path` 重定向 `MEDIA_ROOT`，不会写入开发目录。

### 当前导入范围

- 12 类固定模板、CSV/XLSX、UTF-8/BOM、逐行类型与长度校验。
- 批内重复外部编号、外键/学期/节次/校历/任课范围/选课区间/完成量/覆盖声明等校验。
- 规则课次使用既有 `apps.scheduling.calendar.plan_term_sessions` 在内存中展开；预览与提交共用同一纯函数，不另写第二套周次/校历逻辑。
- `sessions.csv` 的规则课次必须按 `origin_rule_code + origin_teaching_date + origin_start_period` 精确匹配；三字段全空才是独立课次。具体日期/节次/教室/教师与规则展开不同时，B12a 报 `CHANGE_NOT_SUPPORTED_YET`，不做“差不多”的配对或单次改课。
- 新建对象逐类写入 `ImportIdentity`；重复导入通过 `(school, source_system, entity_type, external_id)` 识别，不按姓名/课程名/日期猜主键。
- 提交时调用 `generate_sessions_for_term` 生成规则课次并沿用其锁顺序与 ChangeLog；sessions 状态/完成量变化给课次 `version += 1` 并写 session ChangeLog。教师名册只建 `TeacherProfile`，不创建账号、不修改已关联账号的联系方式。

自动化测试直接读取 `templates/import/*.csv`，覆盖 12 文件样例（40 课次、10-05→10-10、4.0 完成学时）、重复导入、XLSX 12 工作表、编号数字/公式拒绝、错误行定位、`CHANGE_NOT_SUPPORTED_YET`、过期预览和 Admin 上传/预览/提交。

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

## 教学备注与离线草稿（B20）

以下接口均需 JWT，并先通过 `require_active_teacher`；未关联账号返回 403
`TEACHER_NOT_LINKED`，档案停用返回 403 `TEACHER_DISABLED`。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/sessions/{id}/notes/` | 返回 `mine`、`others`、`previous`；`mine` 含本人草稿，没有则为 null；`others` 只含其他教师已发布快照；`previous` 为上次已完成课次的共享记录。 |
| `PUT /api/v1/sessions/{id}/notes/mine/` | body 必填 `covered`、`unfinished`、`next_plan`、`base_version`、`client_mutation_id`；只更新 draft，不回写 published 或课次学时。 |
| `POST /api/v1/sessions/{id}/notes/mine/publish/` | body 必填 `base_version`、`client_mutation_id`；把当前 draft 复制到 published，`published_at=now`、`published_version`=新 version。 |

写备注只允许该课次实际 `SessionTeacher`，否则 403 `NOT_SESSION_TEACHER`。读取已发布内容允许该课次实际授课教师或该 offering 当前有效任课教师；同一教学班其他课程的教师即使能看课表，也不能读备注，返回 403 `NOT_OFFERING_TEACHER`。课次完全不在本人可见范围返回 404 `NOT_FOUND`。任何响应都不会返回他人的 draft 内容。

`GET` 的 `previous` 在同一 `CourseOffering` 中筛选，条件是课次 `status=completed`、
`start_at` 早于当前课次、且至少有一条 published 备注；按 `start_at` 倒序取最近一个课次，
再返回该课次所有教师已发布内容。当前、更晚、未 completed/cancelled 课次以及同名但不同
offering 的记录都不进入“上次记录”。排序按上课时间，不按备注发布时间，所以补录旧课不会顶掉
最近课次。测试中该 GET（含 JWT/教师查询和 previous 查询）固定 8 条查询，不随已发布作者数量增长。

写接口的 `base_version`：本人尚无该课次备注时必须为 0；已有备注时必须等于当前 `version`。
保存草稿或发布都令 `version+1`；保存草稿不改变 published 快照，发布才更新快照。没有本人备注，
或三段草稿去掉首尾空白后都为空时，发布返回 409 `NOTHING_TO_PUBLISH`，不创建备注、mutation 或
ChangeLog，也不能用空内容覆盖已有发布快照。冲突返回 409 `NOTE_VERSION_CONFLICT`，body 为
`{code, detail, field_errors, current}`，`current` 是服务器当前 `mine`（没有则 null），客户端据此
保留本地文本并展示两份内容。

两个写接口都要求 `client_mutation_id`（≤64）。服务在事务内先锁学校 `SchoolSyncState`，再按
`user+client_mutation_id` 唯一键查/建 `ClientMutation`：同 key 同请求摘要直接返回保存的响应，
不执行第二次写入；同 key 不同请求返回 409 `MUTATION_KEY_REUSED`；请求摘要覆盖操作类型、课次 ID、
正文和 `base_version`。保存草稿与发布各写一条 `note` ChangeLog，并与备注和
`ClientMutation` 同事务提交。自动重试只保存 draft，不会自动 publish，也不改变课次
`status/actual_hours/version`。

## 教学交接（B21 后端）

以下接口均需 JWT，并先通过 `require_active_teacher`；未关联账号返回 403
`TEACHER_NOT_LINKED`，档案停用返回 403 `TEACHER_DISABLED`。交接不写 ChangeLog，也不进入
离线同步；发送、已读、确认、撤回都以在线服务端结果为准。每条交接只对应一个接收人，选多位教师时
生成多条独立记录。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/handoffs/prefill/?source_session_id=` | 只读发送表单预填：`recipients`（该 offering 当前有效任课教师，排除本人，含 `is_suggested`）、`suggested_target_session`、`content`、`note_id`、`note_published_version`。不创建任何记录。 |
| `POST /api/v1/handoffs/` | body `offering_id`、`recipient_ids`（1–10 个）、`content`、`source_session_id`、`target_session_id`、`note_id`（后三者可空）。201 返回创建的交接列表；每个接收人一条 `pending`。 |
| `GET /api/v1/handoffs/?box=inbox\|sent&status=` | `inbox` 为本人收到、`sent` 为本人发出，默认 inbox；`status` 可选。created_at 倒序，DRF 标准分页。 |
| `GET /api/v1/handoffs/{id}/` | 仅发送人或接收人可看，其他人 404；GET 不写 `read_at`。 |
| `POST /api/v1/handoffs/{id}/read/` | 仅接收人；只写 `read_at`，status 仍为 pending。重复调用返回同一时间。 |
| `POST /api/v1/handoffs/{id}/acknowledge/` | 仅接收人；pending → acknowledged，写 `acknowledged_at`，`read_at` 为空时一并写入，并给发送人发 `handoff_acknowledged`。重复确认幂等且不重复通知；已撤回 409 `HANDOFF_WITHDRAWN`。 |
| `POST /api/v1/handoffs/{id}/withdraw/` | 仅发送人；只能撤回 pending。已确认 409 `HANDOFF_ALREADY_ACKNOWLEDGED`，重复撤回幂等；撤回不发消息。 |

发送规则：发送人必须是该 offering 当前有效任课教师，或来源课次的 `SessionTeacher`；接收人必须是
该 offering 当前有效任课教师（`effective_until` 为空或晚于今天）且不能是发送人，接收人无账号也可
发送。来源/目标课次必须属于该 offering。关联备注只能是发送人本人在来源课次上已发布的备注，保存当时
的 `published_version`；`content_snapshot` 以发送内容为准，之后修改或重新发布原备注不会改变交接
（A14）。任一接收人不合法时整体 400，不创建部分交接；接收人自动去重。

`GET handoffs/prefill/` 的 `content` 只取本人在来源课次上已发布备注的 `published_unfinished` 和
`published_next_plan`，按“未完成内容：”“下次计划：”两段拼接；没有发布备注或两段都为空时为空字符串，
但有发布备注时仍返回其 `note_id`/`note_published_version`。
`is_suggested`/`suggested_target_session` 取同一 offering 中来源课次之后第一个 `scheduled` 课次的
`SessionTeacher`（排除本人）；建议对象若已不是当前有效任课教师，只返回 `suggested_target_session`，
不会扩大 `recipients` 的合法范围。

状态变更都在事务内对交接行 `select_for_update`，因此并发确认/撤回只有一方成功，另一方得到
409。错误体统一 `{code, detail, field_errors}`：400 `VALIDATION_ERROR`/`INVALID_RECIPIENT`/
`INVALID_NOTE`/`INVALID_SESSION`，403 `NOT_OFFERING_TEACHER`/`NOT_HANDOFF_SENDER`/
`NOT_HANDOFF_RECIPIENT`，404 `NOT_FOUND`，409 `HANDOFF_WITHDRAWN`/
`HANDOFF_ALREADY_ACKNOWLEDGED`。

正式调课通过 `apps.scheduling.changes.apply_session_change` 生效后，会在同一事务内调用
`apps.teaching.handoffs.notify_pending_handoffs_for_moved_session`：对 `target_session` 为该课次
且仍 pending 的交接，向发送人发 `handoff_check` 消息，事件键 `handoff:{id}:check:{change.id}`，
说明目标课次新时间并请其核对接收人。接收人与交接状态都不变，同一 change 重放不重复消息。
`apps.scheduling.changes` 在函数内局部导入该服务，避免 `scheduling` 与 `teaching` 的模块级循环导入。

Admin 的“教学交接”列表只读，可按状态筛选并按课程/教师搜索，供教务查看确认状态；不提供直接修改。

## 教学计划与授课完成（B22 后端）

以下接口均需 JWT，并先通过 `require_active_teacher`；未关联账号返回 403
`TEACHER_NOT_LINKED`，档案停用返回 403 `TEACHER_DISABLED`。计划和完成信息只对该 offering
当前有效任课教师及该 offering 课次的实际授课教师开放，不按教学班共享。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/offerings/{id}/plan/` | 返回 `{plan, progress, can_edit}`。`plan` 含当前版本、负责人和专题；没有计划时为 null。`progress` 由服务端唯一算法生成。 |
| `PUT /api/v1/offerings/{id}/plan/` | 仅 offering 的 `lead_teacher`。body 为 `items`、`base_version`；保存顺序即 position。首次 `base_version=0`，否则必须等于当前 version。 |
| `GET /api/v1/sessions/{id}/completion/` | 当前完成字段、当前专题分配、该 offering 未归档可分配专题、CompletionRecord 历史。 |
| `PUT /api/v1/sessions/{id}/completion/` | body 为 `action`（complete/correct/revert）、`actual_hours`、`allocations`、`reason`、`base_version`；仅该课次 SessionTeacher 可写。 |

计划编辑：带 `id` 更新原条目并保持 ID；不带 `id` 新建；`due_date` 与 `due_session_id`
至多一个，`due_session` 必须属于同一 offering。请求中没有出现、且无 `SessionProgress` 的条目
直接删除；有分配历史的条目改为 `archived=true`，保留历史分配、不再接受新分配，也不进入落后
判断。每次成功保存都令 plan version +1，并追加一条完整 `PlanRevision`，历史版本不覆盖。
非负责人返回 403 `NOT_PLAN_OWNER`；版本冲突返回 409 `PLAN_VERSION_CONFLICT` 并附 `current`。

`progress` 字段均为一位小数字符串（课次数为整数）：

- `target_hours`、`completed_hours`（仅 completed 课次 actual_hours，一课只计一次）、
  `remaining_hours=max(target−completed,0)`、`over_hours=max(completed−target,0)`（超额单列）。
- `remaining_sessions` 为全部 scheduled 课次（过去未确认不漏计）；`past_pending_sessions` 为
  scheduled 且 `end_at<now`；`future_sessions` 为 scheduled 且 `end_at>=now`；cancelled 不计。
- `unallocated_hours=completed_hours−全部 SessionProgress 之和`。
- `items` 每个专题含 `planned_hours/actual_hours/due/is_due/shortfall/archived`。没有计划时
  `plan_status=not_set`，不判定落后；有计划时 `behind_hours` 为未归档且 due 已到的专题差额
  之和，仅当差额为正且达到 settings `PLAN_BEHIND_THRESHOLD_HOURS` 为 `behind`，否则
  `on_track`；阈值为 0 时零缺口仍为 `on_track`。
- 算法固定 3 条查询（课次聚合、plan、专题聚合一页），不随课次数增长；测试直接统计
  `CaptureQueriesContext` 的 3 条查询。

完成写入在一个事务内按 **SchoolSyncState → Term → ClassSession** 加锁。课次当前 version 必须
等于 `base_version`，否则 409 `SESSION_VERSION_CONFLICT` 并附当前完成信息。两个授课教师同时
确认同一课次时，后到事务在课次行锁释放后读到新版本，返回 409，因此恰好一个成功。

- complete 只允许 scheduled 且 `start_at<=now`，cancelled 和未来课次返回 409
  `INVALID_COMPLETION_TRANSITION`；结束时间已过不会自动完成。
- correct/revert 只允许 completed，且 reason 去掉首尾空白后不能为空。
- complete/correct 的 actual_hours 必填、非负、最多一位小数。allocations 整体替换本课次分配；
  专题必须属于该 offering 的计划且未归档、不能重复，合计不能超过本课次 actual_hours。超过返回
  400 `ALLOCATION_EXCEEDS_SESSION_HOURS`，响应附 `allocation_total` 与 `session_actual_hours`；
  合计小于实际学时是合法的，差额进入 `unallocated_hours`。
- revert 将课次恢复 scheduled，清空 actual_hours/完成教师/完成时间/完成依据，删除全部
  SessionProgress。每次 complete/correct/revert 都写 CompletionRecord，课次 version +1，并在
  同一事务写 session ChangeLog。

保存备注只更新备注及 note ChangeLog，不调用完成服务，不改变课次 `status/actual_hours/version`
（A11/A12）。

## 计划落后提醒（B23 后端）

每日命令：

```bash
uv run --env-file .env python manage.py send_plan_reminders
# 指定学校本地判期日期，便于补跑或验收：
uv run --env-file .env python manage.py send_plan_reminders --date 2026-10-26
```

命令逐个处理有教学计划的 offering，每个 offering 一个事务。`plan_status=behind` 时给该
offering 的每位当前有效任课教师创建一条 `plan_behind` 站内消息，事件键为
`plan_behind:{offering_id}:{date}`；同一教师同一天重复运行不会重复创建。`on_track`、`not_set`
不发；没有计划的 offering 不处理；同一教学班的不同课程分别计算并分别提醒。正文包含最早到期
专题日期、该专题差额和合计差额。输出处理、落后、创建、同日跳过和无任课教师的统计。

本切片只创建站内消息和既有 `pending` DeliveryAttempt 骨架，不调用真实推送通道（归 B40）。
部署环境应使用系统定时任务每天运行一次，例如在学校时区的清晨执行，并保留命令输出：

```cron
0 7 * * * cd /srv/teacher-schedule/backend && uv run --env-file .env python manage.py send_plan_reminders >> /var/log/teacher-schedule-plan-reminders.log 2>&1
```

上面的路径只是部署示例，实际以部署目录和进程账号为准；生产环境也可直接注入环境变量而不使用
`.env`。

## 离线同步（B14）

两个接口均需 JWT 且账号关联启用中的教师档案。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/sync/bootstrap/` | 当前学期的一致快照：`cursor`、`assignment_scope_version`、`term`（与 `terms/{id}/calendar/` 同结构）、`offerings`、`sessions`（`SessionSummary`，含同教学班可见课次及 `is_mine`）、`notes`（本人全部备注含草稿，加本人可读的已发布备注）、`generated_at`。没有当前学期时 `term=null`、列表为空。不分页。 |
| `GET /api/v1/sync/changes/?cursor=` | 返回 cursor 之后最多 500 条日志对应的当前状态；`sessions` 只含当前可见课次，不可见课次不泄露存在性；`terms` 只含本校学期；`notes` 对作者返回完整本人备注，对其他教师只返回可读的发布快照。响应含 `next_cursor`、`has_more`、`assignment_scope_version`、`reset_required`。 |

`notes` 的同步口径：作者本人任何保存草稿/发布日志都返回当前完整备注（含 draft）；其他教师只有在其可读且该备注已发布时返回 published 快照，`draft=null`。纯草稿保存日志对他人直接略过且不泄露存在性，但 `next_cursor` 仍推进。若同一页包含某备注的发布日志和后续草稿日志，只返回一次当前发布快照；只有后续草稿日志时，读者已拥有旧发布快照，不再下发重复项。

`cursor` 是字符串形式、客户端不得解析的学校 `last_seq`。bootstrap 在
`REPEATABLE READ READ ONLY` 事务中读取，cursor 和数据来自同一数据库快照；`generated_at`
也由该请求生成。changes 的 `next_cursor` 取本页最后一条**原始日志**的 `change_seq`，即使该对象
被可见性过滤掉也会推进，避免下一条不可见日志永久挡住后续变更。

`reset_required=true` 时 HTTP 仍为 200、`sessions`/`terms`/`notes` 为空：cursor 非法、超过当前
`last_seq`，或早于该校仍保留的最小日志序号（`min_seq > cursor + 1`）。客户端应重新 bootstrap，
并保留本地未同步草稿。同一实体在一页内出现多次时只返回数据库中的最新状态，不回放历史值。

`pytest` 中的查询数测试覆盖 demo 学期与增加规则后的学期：bootstrap 固定为 16 条查询（B20 增加
一次 notes 预取），无 tombstone/note 日志的 changes 页固定为 10 条查询（均含 JWT/教师档案与事务
控制），不随课次数量增长；含 tombstone 时额外一次 offering 可见性校验。含 note 日志时额外固定
两条（备注当前状态与可读范围），仍不随备注数量增长。

## 消息与推送设备骨架（B14）

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/notifications/?cursor=&limit=` | 本人消息，按 `created_at`、`id` 倒序；服务端不透明游标，默认 20、最多 50。返回 `results`、`next_cursor`、`unread_count`；每项含 `id`、`type`、`object_type`、`object_id`、`title`、`body`、`created_at`、`read_at`、`is_read`。 |
| `POST /api/v1/notifications/{id}/read/` | 标记已读；重复调用返回同一 `read_at`，他人消息 404。 |
| `PUT /api/v1/devices/{installation_id}/` | 新建或更新本机设备；字段 `provider`、`provider_client_id`、`platform`、`enabled`（默认 true），同时更新 `last_seen_at`。同一 installation ID 属于其他账号时转移给当前账号。 |
| `DELETE /api/v1/devices/{installation_id}/` | 只要求登录，不要求启用中的教师档案（设备属于账号；pending 或 TEACHER_DISABLED 时退出登录仍可解绑）。幂等注销本机设备：保留行、置 `enabled=false` 并清空 `provider_client_id`，返回 204；不存在或非本人同样 204。保留行是因为历史 `DeliveryAttempt` 使用 `PROTECT`，需保留投递审计。 |

交接相关消息使用 `handoff_received`（收到交接）、`handoff_acknowledged`（接收人确认）和
`handoff_check`（目标课次调课后请发送人核对接收人）；事件键分别包含交接 ID，调课核对还包含
change ID，因此在同一业务事务重试或重放时不会重复建消息。

`apps.notifications.services.notify` 必须在业务事务内调用：recipient + event_key 已存在时返回
`None`，不重复创建；新消息会为该教师账号下每个 enabled 且账号 `is_active=true` 的设备创建一条
`pending` `DeliveryAttempt`，`next_attempt_at=now`。账号停用时 `deactivate_user` 在同一事务内调用
`disable_device_bindings`，把该账号全部设备置为 `enabled=false` 并清空 `provider_client_id`；即使
存在竞态遗留的 enabled 设备，`notify` 对停用账号也只建站内消息，不建投递。本切片**没有**真实推送通道、投递命令和重试：不会发送、
不会把任何记录标成 `sent`，也没有“模拟发送成功”的路径。Admin 只读展示 Notification、
DeviceRegistration、DeliveryAttempt，便于教务排查失败记录。设备可跨账号转移；B40 的实际发送
实现必须在发送时复核设备仍属于该消息接收人，不能仅凭历史 `pending` 记录向新账号手机发送旧消息。

## 调课候选查询（B30）

`POST /api/v1/sessions/{id}/reschedule-options/` 只读返回候选时段、候选教室、教师/学生/教室冲突、缺失数据类别和稳定排序，不创建调课申请、不修改课次。仅该课次的 `SessionTeacher` 或该 offering 的有效 `TeachingAssignment` 教师可查询；未登录返回 401，非任课教师返回 403，未关联教师档案返回 403 `TEACHER_NOT_LINKED`，教师档案停用返回 403 `TEACHER_DISABLED`，课次不存在返回 404。

请求字段：`date_from`、`date_until`（半开区间；默认今天至 `date_from + 14 天`）、`allowed_block_codes`、`classroom_ids`、`allow_weekend`。源课次非 `scheduled` 返回 409 `SESSION_NOT_RESCHEDULABLE`；日期区间无效返回 400 `INVALID_DATE_RANGE`，两者都使用 `{"code", "detail", "field_errors"}`。OpenAPI 合同见 `/api/v1/schema/`。

目标检查只有一个算法：B30 候选循环和 B31 的提交/批准都调用 `apps.scheduling.reschedule_options._TargetEvaluator`；公共单目标入口是 `evaluate_target(session, *, start_period, end_period, day, classroom, now)`。B30 先一次预载数据，再在内存中逐候选调用同一 evaluator，不在循环中发查询；单目标调用固定 8 条查询。`find_reschedule_options` 的 14/60 天查询数仍为常数。

## 调课申请、审批与正式生效（B31）

| 方法与路径 | 说明 |
| --- | --- |
| `POST /api/v1/reschedule-requests/` | 字段 `session_id`、`target_date`、`target_start_period`、`target_end_period`、`target_classroom_id`、`reason`、`submit`（默认 true）。201 返回申请；409 附 `evaluation`。 |
| `GET /api/v1/reschedule-requests/?scope=mine\|related&status=` | 本人申请或本人可见课次的关联申请；created_at 倒序，DRF 分页。返回源课次简版、目标、原因、状态、`needs_verification`、`check_summary`、审批说明/时间、`forced_override`、申请人和 `can_withdraw`。 |
| `POST /api/v1/reschedule-requests/{id}/submit/` | 仅申请人把 draft 提交；重新检查失败返回 409 且保持 draft。 |
| `POST /api/v1/reschedule-requests/{id}/withdraw/` | 仅申请人撤回 draft/submitted；approved/rejected 返回 409 `REQUEST_NOT_WITHDRAWABLE`；不改课表。 |

提交规则：`available` 直接提交；`unverified` 提交并置 `needs_verification=true`；`conflict` 返回 409 `RESCHEDULE_CONFLICT` 且不创建记录，`submit=false` 才保存草稿。硬条件失败返回 409 `TARGET_NOT_ALLOWED`（跨午休、过去、校历例外、停用/容量/设备不满足、节次数不一致等），不能被强制安排绕过。同一课次同一申请人已有 submitted 申请时返回 409 `REQUEST_ALREADY_PENDING`，草稿不受限制。

审批不开放教师 API，只通过 Admin 的三个 action 调用 `apps.scheduling.requests`：普通“批准并生效”、需要说明的“驳回”、必填学校审批编号的“登记学校强制安排”。普通批准在事务内重新检查，只有 `available` 生效；`unverified`/`conflict` 不能普通生效（统一写入口返回 `DATA_UNVERIFIED`/`SCHEDULE_CONFLICT` 并附评估结果），只能强制登记，并要求 `forced_override=true` 与风险说明。所有生效都经过 `apps.scheduling.changes.apply_session_change`：批准路径锁顺序为 SchoolSyncState → Term → RescheduleRequest → ClassSession，更新同一课次 ID、递增课次与学期版本，写 SessionChange 前后快照和 session+term ChangeLog，并为每位 SessionTeacher 与申请人创建幂等站内消息。驳回和撤回都不改课表。

## 常用开发命令

```bash
# 创建超级用户（用邮箱）
uv run --env-file .env python manage.py createsuperuser

# 生成迁移
uv run --env-file .env python manage.py makemigrations

# 仅格式化
uv run ruff format .
```
