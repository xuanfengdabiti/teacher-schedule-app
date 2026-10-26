# 后端开发指南

## 前置条件

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.11 或更高（`uv run --env-file` 需要较新版本）
- Docker Desktop（或 Docker Engine + Docker Compose）

## 本地启动

以下命令均在 `backend/` 目录下执行，除非特别说明。

### 1. 启动 PostgreSQL

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

等待容器变为 healthy：`docker ps --filter name=infra-postgres`

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
- Admin：<http://127.0.0.1:8000/admin/>

## 运行检查命令

```bash
uv run --env-file .env ruff format --check .
uv run --env-file .env ruff check .
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pytest
```

`ruff` 与 `pytest` 本身不读环境变量，但统一加 `--env-file` 可让命令形式一致；`manage.py`、`pytest`（经 pytest-django 加载 Django settings）必须有这些变量。

## 关于 `.env` 的说明

项目**不引入** `django-environ` 之类的配置库，`config/settings.py` 只读标准库 `os.environ`。
`.env` 由 `uv run --env-file .env ...` 在启动进程时注入，而不是由 Django 在运行时解析。
因此直接 `uv run python manage.py check` 而不带 `--env-file` 会报缺少环境变量——这是预期行为。

在部署环境（systemd、容器编排等）应通过真实环境变量注入，不需要 `.env` 文件。

## 环境变量说明

| 变量                  | 必需 | 说明                                                  |
| --------------------- | ---- | ----------------------------------------------------- |
| `DJANGO_SECRET_KEY`   | 是   | Django `SECRET_KEY`；缺失时启动抛出明确 `RuntimeError` |
| `DJANGO_DEBUG`        | 否   | `true`/`1`/`yes` 开启调试，默认关闭                    |
| `DJANGO_ALLOWED_HOSTS`| 否   | 逗号分隔；`DEBUG=false` 时必须非空                     |
| `DATABASE_URL`        | 是   | `postgres://user:pass@host:port/db`；缺失时明确报错    |

`DATABASE_URL` 缺失或为空时服务立即报错退出，**不回退到 SQLite**。

> Windows 提示：本机 `localhost` 曾导致 psycopg 连接挂起，`.env.example` 因此使用
> `127.0.0.1`。Linux/macOS 上两者等价。

## 目录结构

```text
backend/
├── config/              # 项目配置（settings、urls、wsgi/asgi、health 视图）
│   └── tests/
├── apps/
│   └── accounts/        # 自定义 User（UUID 主键、email 登录）
│       ├── forms.py     # Admin 新增/编辑表单
│       ├── migrations/
│       └── tests/
├── manage.py
├── pyproject.toml
├── uv.lock
└── .env.example
```

## 常用开发命令

```bash
# 创建超级用户（用邮箱）
uv run --env-file .env python manage.py createsuperuser

# 生成迁移
uv run --env-file .env python manage.py makemigrations

# 仅格式化
uv run ruff format .
```
