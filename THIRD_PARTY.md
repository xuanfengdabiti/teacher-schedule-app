# 第三方复用记录

截至 2026-10-27，本仓库包含 Django 后端、Flutter 移动端、本地演示启动脚本与虚构模板。优先使用成熟包实现账号、网络、存储、路由、管理后台与表格读取；教师业务按本项目接口实现。未复制第三方 App 源码；候选及其许可证核查见[开源调研](docs/open-source-research.md)。

空教室查询复用既有 Django ORM、课次/教室占用/数据覆盖选择器和 Flutter Material 日期、时间控件，未新增第三方依赖或数据库表。

## 默认候选

Flutter、Riverpod、go_router、Dio、flutter_secure_storage 与 SharedPreferences 已实际使用并完成 Android APK 构建。Drift、calendar_view、flutter_local_notifications 与个推插件仍只是原方案候选，未引入当前本地演示版。真实 SMTP、远程推送与 iOS 构建不在本次验收范围。

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
| Gunicorn | https://github.com/benoitc/gunicorn | 26.2.0 | MIT | uv | 演示容器内运行 Django |
| WhiteNoise | https://github.com/evansd/whitenoise | 6.12.0 | MIT | uv | Django Admin 静态文件 |
| openpyxl | https://openpyxl.readthedocs.io/ | 3.1.5 | MIT | uv | B12a/B12b XLSX 只读解析（`read_only=True, data_only=False`） |
| Pillow | https://python-pillow.org/ | 13.0.0 | MIT-CMU（HPND） | uv | Django/DRF `ImageField` 的头像图片解码与格式校验；不做图像识别 |
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

### 移动端实际依赖

本机验收使用 Flutter 3.47.8 / Dart 3.13.7。以下版本以 `mobile/pubspec.lock` 为准；许可证核对本机下载包的 LICENSE，传递依赖由锁文件记录。

| 名称 | 来源 URL | 锁定版本 | 许可证 | 用途 |
| --- | --- | --- | --- | --- |
| Flutter | https://github.com/flutter/flutter | 3.47.8 | BSD-3-Clause | Android/iOS UI 框架 |
| flutter_riverpod | https://pub.dev/packages/flutter_riverpod | 3.4.3 | MIT | 页面状态与依赖管理 |
| go_router | https://pub.dev/packages/go_router | 18.1.1 | BSD-3-Clause | 页面路由 |
| dio | https://pub.dev/packages/dio | 5.11.1 | MIT | API 网络请求 |
| flutter_secure_storage | https://pub.dev/packages/flutter_secure_storage | 11.2.0 | BSD-3-Clause | 登录信息保存 |
| shared_preferences | https://pub.dev/packages/shared_preferences | 2.5.6 | BSD-3-Clause | 设置与小型本地草稿 |
| intl | https://pub.dev/packages/intl | 0.20.3 | BSD-3-Clause | 日期与本地化 |
| cupertino_icons | https://pub.dev/packages/cupertino_icons | 1.0.9 | MIT | 图标 |
| flutter_lints | https://pub.dev/packages/flutter_lints | 6.0.0 | BSD-3-Clause | 开发时静态检查规则 |

App 发布时提供“开源许可”页和相应第三方声明。当前项目本身的许可证尚未由作者选定，公开可读不等于已按 MIT 等协议授权。
