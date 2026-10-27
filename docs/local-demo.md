# 本地演示启动包

用于本机演示的虚构环境：Django API + PostgreSQL，加一份 12 个 CSV 的示例课表（40 个课次）、2 个教师账号、1 个教务账号和少量备注/计划/交接/协查样例。不是生产部署，也不含真实学校数据。

## 一键启动

先启动 Docker Desktop，并安装 PowerShell 7（`pwsh`）。在仓库根目录执行：

```powershell
pwsh scripts/start-demo.ps1
```

脚本会：

1. 用 `infra/deploy/.env.demo` 的虚构参数构建并启动独立 Compose 项目 `teacher-schedule-demo`。
2. 轮询健康检查端点 `http://127.0.0.1:18000/api/v1/health/`，直到返回 200。
3. 把 `templates/import/*.csv` 和 `scripts/seed_demo.py` 复制进 api 容器。
4. 通过现有导入服务首次导入示例课表；重复启动不会重复导入，也不会覆盖演示者已保存的备注、计划、交接或协查。

停止：

```powershell
docker compose -p teacher-schedule-demo --env-file infra/deploy/.env.demo -f infra/deploy/compose.yaml down
```

如需清空演示数据重新开始（会删除本项目自己的数据卷，不影响其他项目）：

```powershell
docker compose -p teacher-schedule-demo --env-file infra/deploy/.env.demo -f infra/deploy/compose.yaml down -v
```

## 虚构账号

| 角色 | 邮箱 | 密码 |
| --- | --- | --- |
| 教师甲 | `t0001@example.invalid` | `TeacherDemo2026!` |
| 教师乙 | `t0002@example.invalid` | `TeacherDemo2026!` |
| 教务 | `academic-staff@example.invalid` | `PreviewOffice2026!` |

教务账号可登录 `http://127.0.0.1:18000/admin/`。

## 手机连接

- Android 模拟器：`http://10.0.2.2:18000/api/v1/`
- 真机：手机与电脑同一局域网，把地址改成电脑局域网 IP，例如 `http://192.168.1.10:18000/api/v1/`。

APK：`mobile/build/app/outputs/flutter-apk/app-release.apk`（当前约 44 MB）。

## 四核心演示路径

演示数据学期为 `2026-autumn`。当前日期不是演示日时，请选 **2026-10-26 所在周** 或 **2026-11-02 所在周**；2026-10-27（周二）本身没有课。

1. **课表与调课**：教师甲在 2026-10-26 周只见周一第 3–4 节大数据课（A302）；教师乙见第 1–2 节刑侦课（A301）。教师甲切到 2026-11-02 周可同时看到刑侦（周一 1–2 节，A301）与大数据（3–4 节，A302）。可对课次发起调课，教务在 Admin 登记审批结果。
2. **备注与交接**：教师甲打开 2026-10-19 已完成的刑侦课次，能看到种子写入并已发布的三段中文备注，可继续编辑并发布。交接列表里已有甲发给乙的虚构交接：乙可查看、确认，甲可撤回。
3. **教学计划与学时**：教师甲打开刑侦 offering 的教学计划，能看到两个 20.0 学时的虚构专题（截止 2026-11-02、2026-12-21）；从课次详情进入完成登记，可确认/更正完成学时并分配专题。种子不重算业务学时。
4. **教室与协查**：教师甲在协查列表能看到 A301、2026-10-26 08:00–09:40 的“蓝色笔记本”协查；教师乙收到后可回复，教师甲可结案。

## 说明

- 全部账号、课程、教室、备注、交接和协查均为虚构，可随时删除演示数据卷重来。
- `.env.demo` 的密钥和密码只用于本地演示，不要用于任何真实环境。
- 当前为本地演示版：没有真实远程推送、云部署或 iOS 实机验收。
