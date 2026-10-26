# 第三方复用记录

截至 2026-10-26，本仓库包含项目文档、配置与虚构模板，以及 `backend/` 的后端工程骨架；未复制第三方 App 源码。候选及其许可证核查见[开源调研](docs/open-source-research.md)。

## 默认候选

Flutter、Riverpod、go_router、Dio、Drift、flutter_secure_storage、calendar_view（待适配验证）、flutter_local_notifications；Django、DRF、dj-rest-auth、django-allauth、SimpleJWT、drf-spectacular、openpyxl、PostgreSQL。远程推送采用个推官方 Flutter 插件候选，服务条款和费用单独确认。

这些是计划选用，并不表示已完成兼容性构建、引入了源码或获有任何商用服务额度。

## 实际引入

以下为后端 `backend/` 已通过 uv 锁定版本的包。全部依赖的精确版本以 `backend/uv.lock` 为准。

### 运行时直接依赖

| 名称 | 来源 URL | 锁定版本 | 许可证 | 引入方式 | 用途 |
| --- | --- | --- | --- | --- | --- |
| Django | https://www.djangoproject.com/ | 5.2.18 | BSD-3-Clause | uv | Web 框架，ORM，Admin |
| djangorestframework | https://www.django-rest-framework.org/ | 3.18.3 | BSD-3-Clause | uv | REST API 视图、序列化、认证 |
| drf-spectacular | https://github.com/tfranzel/drf-spectacular | 0.30.0 | BSD-3-Clause | uv | OpenAPI schema 生成 |
| psycopg | https://psycopg.org/ | 3.3.6 | LGPL-3.0-only | uv | PostgreSQL 数据库适配器 |
| psycopg-binary | https://psycopg.org/ | 3.3.6 | LGPL-3.0-only | uv | psycopg 的二进制分发（避免需要编译 libpq） |

### 开发依赖

| 名称 | 来源 URL | 锁定版本 | 许可证 | 用途 |
| --- | --- | --- | --- | --- |
| pytest | https://docs.pytest.org/ | 9.1.1 | MIT | 测试框架 |
| pytest-django | https://pytest-django.readthedocs.io/ | 4.14.0 | BSD-3-Clause | Django 测试集成 |
| ruff | https://docs.astral.sh/ruff | 0.17.2 | MIT | 格式化和静态检查 |

传递依赖不在此表重复记录，精确版本以 `backend/uv.lock` 为准。

普通包依赖锁版本；复制源码另记原文件路径、修改范围，保留版权和 NOTICE。无明确许可证的仓库不复制；GPL 候选当前仅作产品参考，若未来选其源码需明确项目许可证与分发方式。

App 发布时提供“开源许可”页和相应第三方声明。当前项目本身的许可证尚未由作者选定，公开可读不等于已按 MIT 等协议授权。
