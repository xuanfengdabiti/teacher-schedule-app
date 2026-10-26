# 第三方复用记录

截至 2026-10-27，本仓库包含项目文档、配置与虚构模板，以及 `backend/` 的后端工程：教务数据模型、课次生成/调课候选、B12a/B12b CSV/XLSX 导入与基础资料增量，以及账号/JWT、教师身份关联和 `me` 的后端切片。未复制第三方 App 源码；候选及其许可证核查见[开源调研](docs/open-source-research.md)。

## 默认候选

Flutter、Riverpod、go_router、Dio、Drift、flutter_secure_storage、calendar_view（待适配验证）、flutter_local_notifications 仍处于计划选型阶段；远程推送采用个推官方 Flutter 插件候选，服务条款和费用单独确认。后端已实际引入的包见下表，未运行 Flutter 构建、真实 SMTP 或推送链路验证。

这些候选选择不表示已完成双端兼容性构建、接入了真实学校数据或获有任何商用服务额度。

## 实际引入

以下为后端 `backend/` 已通过 uv 锁定版本的包。全部依赖的精确版本以 `backend/uv.lock` 为准。

### 运行时直接依赖

| 名称 | 来源 URL | 锁定版本 | 许可证 | 引入方式 | 用途 |
| --- | --- | --- | --- | --- | --- |
| Django | https://www.djangoproject.com/ | 5.2.18 | BSD-3-Clause | uv | Web 框架，ORM，Admin |
| djangorestframework | https://www.django-rest-framework.org/ | 3.18.3 | BSD-3-Clause | uv | REST API 视图、序列化、认证 |
| dj-rest-auth | https://github.com/iMerica/dj-rest-auth | 7.2.0 | MIT | uv | 注册、登录、退出、密码流程端点 |
| django-allauth | https://github.com/pennersr/django-allauth | 65.20.1 | MIT | uv，`socialaccount` extra | 邮箱账号、验证邮件、密码重置流程 |
| djangorestframework-simplejwt | https://github.com/jazzband/djangorestframework-simplejwt | 5.5.1 | MIT | uv | JWT access/refresh 与 blacklist |
| drf-spectacular | https://github.com/tfranzel/drf-spectacular | 0.30.0 | BSD-3-Clause | uv | OpenAPI schema 生成 |
| openpyxl | https://openpyxl.readthedocs.io/ | 3.1.5 | MIT | uv | B12a/B12b XLSX 只读解析（`read_only=True, data_only=False`） |
| psycopg | https://psycopg.org/ | 3.3.6 | LGPL-3.0-only | uv | PostgreSQL 数据库适配器 |
| psycopg-binary | https://psycopg.org/ | 3.3.6 | LGPL-3.0-only | uv | psycopg 的二进制分发（避免需要编译 libpq） |

### 开发依赖

| 名称 | 来源 URL | 锁定版本 | 许可证 | 用途 |
| --- | --- | --- | --- | --- |
| pytest | https://docs.pytest.org/ | 9.1.1 | MIT | 测试框架 |
| pytest-django | https://pytest-django.readthedocs.io/ | 4.14.0 | BSD-3-Clause | Django 测试集成 |
| ruff | https://docs.astral.sh/ruff | 0.17.2 | MIT | 格式化和静态检查 |

`django-allauth[socialaccount]` 的 extra 仅用于满足 dj-rest-auth 7.2.0 注册模块对
allauth SocialAccount 的导入依赖；项目未配置社交登录提供商，也未暴露社交登录端点。
`openpyxl` 的传递依赖 `et-xmlfile 2.0.0`（MIT）仅用于读取 XLSX 内部 XML。没有引入 pandas、tablib、django-import-export 或其他表格/导入框架。传递依赖不在此表重复记录，精确版本以 `backend/uv.lock` 为准。

普通包依赖锁版本；复制源码另记原文件路径、修改范围，保留版权和 NOTICE。无明确许可证的仓库不复制；GPL 候选当前仅作产品参考，若未来选其源码需明确项目许可证与分发方式。

App 发布时提供“开源许可”页和相应第三方声明。当前项目本身的许可证尚未由作者选定，公开可读不等于已按 MIT 等协议授权。
