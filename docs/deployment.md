# 后端部署说明

> 2026-10-29 新增用户指定的香港服务器手机演示环境：`http://38.76.172.48:18000/api/v1/`，目录 `/srv/teacher-schedule`，Compose 项目 `teacher-schedule-phone`。当前部署与手机安装记录见[手机联网演示](phone-demo.md)。下文 `codex-app-preview`、局域网 IP 和早期未验收描述保留为历史部署说明，不表示当前公网环境仍在该电脑上运行。

本文记录后端本机部署与 App 联调预览服务。当前实际运行的是专用 Compose 项目 `codex-app-preview`（API + PostgreSQL），用于 Flutter 本机联调；它不是已经验收的生产发布，备份恢复章节也尚未演练。外部真实 SMTP 和推送通道仍待接入。

相关文件：

| 文件 | 作用 |
| --- | --- |
| `backend/Dockerfile` | 运行镜像：Python 3.12.16 + uv 锁定依赖 + Gunicorn |
| `backend/.dockerignore` | 排除 `.venv`、`.env`、Git、上传文件和缓存 |
| `infra/deploy/compose.yaml` | API + PostgreSQL 16.15 的部署栈（项目隔离卷） |
| `infra/deploy/.env.example` | 部署环境变量示例（无效值，必须替换） |

开发用 `infra/compose.yaml` 与本部署栈完全独立：它只有开发 PostgreSQL + Mailpit，使用 `pgdata` 卷；部署栈使用项目名 `teacher-schedule-deploy`（本机预览覆盖为 `codex-app-preview`），Compose 会生成项目前缀的 `postgres_data`、`media_data` 卷，不会覆盖开发数据。

## 1. 静态文件与私有上传（已落地）

`backend/config/settings.py` 已完成以下修改：

- 在 `SecurityMiddleware` 之后加入 `whitenoise.middleware.WhiteNoiseMiddleware`；
- 设置 `STATIC_ROOT = BASE_DIR / "staticfiles"`；
- 未配置 `MEDIA_URL`，也没有为上传文件增加 URL 路由。

容器入口每次单实例启动会执行：

```sh
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application ...
```

`collectstatic` 将 Admin 等静态文件写入 `/app/staticfiles`，由 WhiteNoise 提供；Admin 登录页的 CSS 不再依赖开发服务器的静态文件处理。`MEDIA_ROOT` 由 `DJANGO_MEDIA_ROOT` 指定并挂载 `media_data` 卷，保存导入原件和头像；这些文件只能通过现有认证接口读取，宿主机和反向代理都不应直接公开该目录。

## 2. 第一次在新机器启动

前提：Linux 主机已安装 Docker Engine 与 Docker Compose v2，仓库已检出到 `/srv/teacher-schedule`。

```bash
cd /srv/teacher-schedule
cp infra/deploy/.env.example infra/deploy/.env
```

编辑 `infra/deploy/.env`，至少替换：

- `DJANGO_SECRET_KEY`：随机长字符串，绝不能提交；
- `POSTGRES_PASSWORD`：强密码，建议只用字母数字，避免写进 `DATABASE_URL` 时需要百分号编码；
- `DJANGO_ALLOWED_HOSTS`：本机演练保持 `localhost,127.0.0.1`；有域名或反向代理时改成实际域名；
- `API_BIND_ADDRESS` / `API_PORT`：默认只监听 `127.0.0.1:18000`，适合本机演练；对外提供服务时再改为 `0.0.0.0` 并由反向代理终结 TLS。

检查配置（不会创建容器或卷）：

```bash
docker compose -f infra/deploy/compose.yaml config
```

构建并启动：

```bash
docker compose -f infra/deploy/compose.yaml up -d --build
docker compose -f infra/deploy/compose.yaml ps
```

镜像入口会按顺序执行 `manage.py migrate --noinput`、`manage.py collectstatic --noinput`，然后 `exec gunicorn config.wsgi:application`。因此：

- **只运行一个 API 实例**（`api` 服务不要 `--scale`）。迁移和静态收集在每次容器启动时执行一次；多副本会并发跑迁移，不属于本阶段的单实例模型。
- Gunicorn 是容器 PID 1，`docker stop` 的 SIGTERM 会触发优雅退出（`stop_grace_period: 30s`）。
- API 启动不依赖 SMTP 或个推账号：`EMAIL_HOST` 为空时应用照常启动，只有实际发信会失败。接入邮件后把 SMTP 变量写进 `infra/deploy/.env` 再 `docker compose -f infra/deploy/compose.yaml up -d` 即可；个推/APNs 的供应商参数不属于本轮交付，后续按现有 `notifications` 设备模型接入。

健康检查：

```bash
curl -fsS http://127.0.0.1:18000/api/v1/health/
# {"status":"ok","database":"ok"}
```

该接口会执行 `SELECT 1`，数据库不可用时返回 503。Compose 的 `api` 健康检查也调用同一路径。

### 2.1 本机 App 预览服务（已启动）

当前实际运行的项目是 `codex-app-preview`，使用私有 env 文件 `C:/Users/Administrator/.dsh/tasks/app-preview.env`（不提交仓库）。启动/重建命令：

```powershell
docker compose -p codex-app-preview --env-file "C:/Users/Administrator/.dsh/tasks/app-preview.env" -f infra/deploy/compose.yaml up -d --build
```

当前参数：

- API 绑定 `0.0.0.0:18000`，本机访问 `http://127.0.0.1:18000`；同一局域网手机使用 `http://192.168.0.16:18000`（以主机实际 WLAN 地址为准，Windows 防火墙需允许该端口）。
- PostgreSQL 仅在同一 Compose 网络内，使用独立卷 `codex-app-preview_postgres_data`；媒体使用 `codex-app-preview_media_data`，不碰开发卷 `infra_pgdata`。
- 邮件后端为 `django.core.mail.backends.console.EmailBackend`，只把邮件打印到容器日志；真实 SMTP 和个推/APNs 尚未接入。`EMAIL_HOST` 等变量仍可通过私有 env 注入。
- `DJANGO_ALLOWED_HOSTS=*`、`DJANGO_DEBUG=false` 仅用于本机联调；不要直接当作公网生产配置。

Flutter 客户端把后端基址指到上述地址即可联调。API 健康检查已验证返回 `{"status":"ok","database":"ok"}`。

## 3. 创建管理员

```bash
docker compose -f infra/deploy/compose.yaml exec api python manage.py createsuperuser
```

按提示输入邮箱和密码（本项目用邮箱登录，没有 username）。创建后可访问 `http://127.0.0.1:18000/admin/`；对外部署时请通过反向代理暴露，不要直接把 18000 端口开到公网。

## 4. 导入仓库里的虚构模板

仓库 `templates/import/` 下的 CSV 是**虚构演示数据**，不是真实学校名册。导入前先确认数据库迁移已完成，然后：

1. 登录 Admin，进入 **数据导入 → 导入批次 → 上传导入批次**；
2. 选择学校（首次可先在 Admin 建一个虚构学校，代码例如 `DEMO-SCHOOL`）、填写稳定的 `source_system`（例如 `demo_csv_v1`）和 `source_updated_at`；
3. 一次选中 `templates/import/` 下同一批次的 CSV 文件（顺序与 `templates/import/README.md` 一致），上传后系统自动预览；
4. 预览无错误后提交；若有人工决定项，先在批次详情逐条处理再提交。

上传的原件写入 `DJANGO_MEDIA_ROOT/imports/<batch_uuid>/`，在本部署栈中就是 `media_data` 卷内的 `/app/var/media/imports/...`，不会通过 URL 公开。不要在公开仓库或部署目录放入真实学校数据；真实导入应通过同一 Admin 上传流程进行。

如果只需要在本地验证模板，不必进入容器：开发环境按 `backend/README.md` 启动后，用同样的 Admin 流程上传即可。

## 5. 日常更新与迁移

代码更新后（先确认并行工作已合并、`uv.lock` 已更新）：

```bash
cd /srv/teacher-schedule
git pull --ff-only
docker compose -f infra/deploy/compose.yaml build api
docker compose -f infra/deploy/compose.yaml up -d api
```

重新构建的镜像入口会自动执行迁移，并在 `STATIC_ROOT` 配置后自动收集静态文件。如果只想先看迁移计划：

```bash
docker compose -f infra/deploy/compose.yaml run --rm api python manage.py showmigrations
```

回滚镜像时用 `API_IMAGE` 指定上一个标签，例如在 `.env` 写 `API_IMAGE=teacher-schedule-api:2026-10-01`，再 `up -d api`；数据库迁移的回滚不在本文自动化范围内，涉及破坏性迁移前必须先做第 7 节备份。

查看日志：

```bash
docker compose -f infra/deploy/compose.yaml logs -f --tail=200 api
docker compose -f infra/deploy/compose.yaml logs -f --tail=200 db
```

Gunicorn 的访问日志和错误日志都输出到 stdout/stderr，由 Docker 日志驱动保存。生产环境建议给 Docker 配置日志轮转（例如 `daemon.json` 中 `max-size`/`max-file`），而不是把日志写进容器层。

## 6. 定时任务（不引入 Celery/Redis）

两个任务都用宿主机 cron 调 `docker compose exec -T`，不需要额外的 worker 框架。命令中的容器名假设 Compose 项目名是 `teacher-schedule-deploy`（即 `compose.yaml` 的 `name`），路径以 `/srv/teacher-schedule` 为例。

**每日计划提醒**（现有命令，已实现）：

```cron
# 每天 07:00（学校本地时区 Asia/Shanghai）发送教学计划落后提醒
0 7 * * * cd /srv/teacher-schedule && docker compose -f infra/deploy/compose.yaml exec -T api python manage.py send_plan_reminders >> /var/log/teacher-schedule-plan-reminders.log 2>&1
```

**每分钟投递通知**（命令后续定名 `dispatch_notifications`，当前尚未实现，不要提前写进 crontab）：

```cron
# 待 B40 实现 dispatch_notifications 后启用；先确认命令存在再取消注释
# * * * * * cd /srv/teacher-schedule && docker compose -f infra/deploy/compose.yaml exec -T api python manage.py dispatch_notifications >> /var/log/teacher-schedule-dispatch.log 2>&1
```

说明：

- 宿主机时区不是 `Asia/Shanghai` 时，cron 时间需要换算；应用内判断使用学校本地日期。
- `exec -T` 关闭 TTY，避免 cron 环境下分配伪终端失败。
- 每条命令都独立追加日志并按需自行轮转，本文不引入日志平台。
- 通知投递命令落地前，`notifications` 只会产生站内消息和 `pending` 投递记录，不会真正发送推送；接入个推/APNs 后再启用每分钟任务。

## 7. 备份与恢复（方案，尚未演练）

下面的备份/恢复步骤保留作后续参考，**本轮没有执行**，恢复流程也尚未完整验收。当前 App 预览服务不以备份恢复为前置条件。

备份目标是「数据库 + 私有 media 卷」。下面命令都在**部署主机**执行，目录 `/srv/teacher-schedule-backups` 需自行创建并做好权限控制；备份文件含真实数据，不能提交到仓库。

### 7.1 数据库备份

PowerShell 下不要把 `pg_dump` 的标准输出直接 `>` 重定向到文件（会产生 UTF-16 或损坏二进制）。统一在容器内生成，再 `docker cp` 出来：

```powershell
# 在部署主机（PowerShell）执行
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = 'D:\teacher-schedule-backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

# 1) 容器内生成自定义格式 dump
docker compose -f infra/deploy/compose.yaml exec -T db pg_dump -U schedule_app -d schedule_app -Fc -f /tmp/schedule-$stamp.dump

# 2) 复制到宿主机
docker compose -f infra/deploy/compose.yaml cp "db:/tmp/schedule-$stamp.dump" "$backupDir\schedule-$stamp.dump"

# 3) 清理容器内临时文件
docker compose -f infra/deploy/compose.yaml exec -T db rm -f /tmp/schedule-$stamp.dump
```

Linux 主机可直接用 `docker compose ... exec -T db pg_dump ... > backup.dump`，但仍建议先在容器内生成再 `docker cp`，避免 shell 编码/缓冲差异。`-Fc` 是自定义格式，恢复时用 `pg_restore`。

### 7.2 media 卷备份

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose -f infra/deploy/compose.yaml exec -T api tar -czf /tmp/media-$stamp.tar.gz -C /app/var/media .
docker compose -f infra/deploy/compose.yaml cp "api:/tmp/media-$stamp.tar.gz" "D:\teacher-schedule-backups\media-$stamp.tar.gz"
docker compose -f infra/deploy/compose.yaml exec -T api rm -f /tmp/media-$stamp.tar.gz
```

如果停机窗口允许，也可在 `api` 停止后对卷做一次性打包；在线打包可能包含正在写入的文件，恢复前应比对时间点。

### 7.3 恢复到独立的新库/新卷（不覆盖开发数据）

恢复演练必须在**新的数据库名 + 新的卷**里做，不能 `dropdb` 现有库，也不能把 dump 灌回 `teacher_schedule_deploy_postgres_data`。以下用一次性 Compose 覆盖文件 `infra/deploy/compose.restore.yaml`（需自行创建，不要提交真实备份）：

```yaml
# infra/deploy/compose.restore.yaml —— 仅恢复演练用，用完删除
services:
  db:
    volumes:
      - restore_pgdata:/var/lib/postgresql/data
  api:
    volumes:
      - restore_media:/app/var/media

volumes:
  restore_pgdata:
    name: teacher_schedule_restore_pgdata
  restore_media:
    name: teacher_schedule_restore_media
```

步骤（先设置恢复环境的库名，避免误连原库）：

```powershell
# 0) 恢复专用库名。shell 环境变量优先于 --env-file，因此只影响本次恢复栈。
$env:POSTGRES_DB = 'schedule_restore'

# 1) 用独立项目名和覆盖文件启动空库（新卷；POSTGRES_DB 会创建 schedule_restore）
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env up -d db

# 2) 等待 db healthy 后恢复 dump（目标库已由 POSTGRES_DB 创建，不要再 createdb）
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env cp "D:\teacher-schedule-backups\schedule-YYYYmmdd-HHmmss.dump" db:/tmp/restore.dump
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env exec -T db pg_restore -U schedule_app -d schedule_restore --no-owner --no-privileges /tmp/restore.dump

# 3) 恢复 media 到独立新卷，再启动 api（入口迁移只补 dump 中缺失的迁移）
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env up -d api
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env cp "D:\teacher-schedule-backups\media-YYYYmmdd-HHmmss.tar.gz" api:/tmp/media-restore.tar.gz
docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml --env-file infra/deploy/.env exec -T api tar -xzf /tmp/media-restore.tar.gz -C /app/var/media

# 4) 校验：健康检查、Admin 登录、抽查导入批次和头像文件
curl.exe -fsS http://127.0.0.1:18000/api/v1/health/
```

注意：

- 上面的 `POSTGRES_DB` 只用于恢复演练的 `db`/`api` 容器；原 `.env` 与生产容器保持不动。恢复实例的 `api` 会按入口脚本对 `schedule_restore` 执行 `migrate`：dump 已包含 `django_migrations`，因此只补缺失迁移，不会重复建表。**先恢复 dump 再启动 `api`**，否则入口会先建迁移表，`pg_restore` 因对象已存在而失败。
- `POSTGRES_DB=schedule_restore` 会让恢复栈的 `db` 在初始化时创建该库；`POSTGRES_USER` 仍为 `schedule_app`，与备份中的角色一致。`pg_restore` 的目标库必须是空库；`--no-owner --no-privileges` 避免恢复角色与生产角色不一致。
- 如果 dump 来自更旧的代码，恢复后先看 `docker compose ... logs api`，让入口迁移补齐；如果 dump 来自更新版本，先用同一版本的镜像启动。
- 恢复完成后，确认新库数据正常再决定是否切换；原开发/生产库和卷全程不被读写。
- 演练结束用 `docker compose -p teacher-schedule-restore -f infra/deploy/compose.yaml -f infra/deploy/compose.restore.yaml down -v` 删除**恢复专用**卷；不要对 `teacher-schedule-deploy` 项目执行 `down -v`，那会删除生产数据卷。

## 8. 版本与许可证

| 组件 | 版本 | 许可证 | 用途/来源 |
| --- | --- | --- | --- |
| Python | 3.12.16（`python:3.12.16-slim-bookworm`，digest `sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753`） | PSF-2.0 | 基础镜像 |
| uv | 0.11.7（`ghcr.io/astral-sh/uv:0.11.7`） | Apache-2.0 / MIT | 构建期安装锁定依赖 |
| Django | 5.2.18（`uv.lock` 锁定，`django>=5.2,<5.3`） | BSD-3-Clause | Web 框架 |
| Gunicorn | 26.2.0（`uv.lock` 锁定） | MIT | WSGI 服务器；参考 [Gunicorn Docker 指南](https://gunicorn.org/guides/docker/) |
| WhiteNoise | 6.12.0（`uv.lock` 锁定） | MIT | 静态文件服务；参考 [WhiteNoise Django 文档](https://whitenoise.readthedocs.io/en/stable/django.html) |
| PostgreSQL | 16.15（`postgres:16.15`） | PostgreSQL License | 数据库 |
| psycopg | 3.3.6（`uv.lock` 锁定） | LGPL-3.0-only | PostgreSQL 驱动 |
| DRF | 3.18.3（`uv.lock` 锁定） | BSD-3-Clause | REST API |

部署相关做法参考 Django 5.2 [部署清单](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/) 与 [静态文件部署](https://docs.djangoproject.com/en/5.2/howto/static-files/deployment/)。本表只列本轮新增/直接相关的组件；完整第三方清单由另一个 worker 统一更新到 `THIRD_PARTY.md`，本轮没有改该文件。

## 9. 本轮验证范围

已实际执行并通过：

- `docker compose -p codex-app-preview ... up -d --build` 构建并启动成功；
- `api`、`db` 容器均为 healthy；PostgreSQL 迁移全部应用；
- 入口 `collectstatic` 收集 156 个静态文件，Gunicorn 以 3 个 worker 启动；
- `GET http://127.0.0.1:18000/api/v1/health/` 返回 200 和 `{"status":"ok","database":"ok"}`；
- `GET http://127.0.0.1:18000/admin/login/` 返回 200；
- Admin 登录页引用的 `/static/admin/css/base.css` 返回 200，`text/css`，22,120 bytes。

本轮**未执行**：完整测试套件、创建管理员、数据导入、真实 SMTP/推送投递、备份与恢复演练。以上未执行项不能写成已通过；外部邮件和推送接入后再补相应验证。
