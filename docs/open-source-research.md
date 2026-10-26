# 开源复用调研与选型记录

调研日期：2026-10-26。范围：移动端教师课表 App，覆盖课表与调课、教学备注与交接、教学计划与进度、教室历史与失物协查。本文为实现方案的选型依据，尚未引入第三方源码，也未完成整仓编译验证。

## 1. 结论与复用原则

在本次检索范围内，没有找到可以直接部署并完整覆盖四个教师业务方向的开源 App。已经存在可复用的课表界面、移动端框架、账号系统、数据库访问和文件解析工具。建议使用 **Flutter + Django/DRF + PostgreSQL**，复用基础能力，只编写本项目的教师业务与适配逻辑。

“先找开源”按功能执行，不等同于一定要 fork 一整个学生课表应用。单机学生课表的课程模型通常没有实际选课关系、协同授课、课次级交接、正式调课记录；直接在其上加服务器可能比采用成熟框架新建业务模块改动更多。此处是根据候选项目公开说明作出的适配判断，尚未对候选完整源码做质量审计。

每个新增模块动工前完成：查现有依赖 → 查成熟开源 → 做最小适配验证 → 记录采用/放弃理由。已能满足需求的组件通过封装调用；只有组件缺失或适配成本明显更高的部分才自写。

## 2. 整体 App 候选

| 候选 | 已核实的能力与协议 | 与本项目的差距 | 处理决定 |
| --- | --- | --- | --- |
| [YIClass](https://github.com/wykwey/YIClass) | Flutter 跨平台课表，日/周等视图、导入导出；README 说明本地存储；[Apache-2.0 LICENSE](https://github.com/wykwey/YIClass/blob/main/LICENSE) | 未在公开说明中找到完整教师协同后端；README 功能段写支持多课并排，FAQ 又写只显示首个匹配课程，需运行验证，不能据此认定实际布局能力 | **优先参考候选**；开工先试运行并考察课程卡片/周视图能否独立复用，不能直接照搬课程和权限模型 |
| [SchedU](https://github.com/gnahz77/SchedU) | Flutter，JSON/AI 导入；[MIT LICENSE](https://github.com/gnahz77/SchedU/blob/master/LICENSE) | 公开定位以个人课程表为主，未声明完整教师协同业务；AI 导入不是本项目首版前提 | **第二候选**；参考导入格式、界面交互，按实际适配工作量决定是否提取模块 |
| [ClassPath](https://github.com/DaHedan/ClassPath) | Flutter，日/周/学期视图、调休补班、导入导出；[GPL-3.0 LICENSE](https://github.com/DaHedan/ClassPath/blob/main/LICENSE) | 本地优先学生工具，未覆盖四个教师业务；复制代码会把对应协议义务带入衍生发布流程 | **产品行为参考**；当前方案不复制其源码。公开 GitHub 仓库与主动选择 GPL 作为本项目许可证是两件事 |
| [WakeUpSchedule](https://github.com/YZune/WakeUpSchedule) | 原 Android 课表，单双周、特定学校课表导入；仓库根目录/API 未声明许可证 | 原仓推送年代较早，平台和学校绑定明显；不能把今天商业产品能力推断为该公开仓库能力 | **只作历史产品参考**；不复制无明确授权的代码 |

GitHub REST API 当次返回的仓库状态如下。`pushed_at` 只代表仓库发生推送，不代表已发布、默认分支最后提交或真实设备质量；星数不作为采用标准。

| 仓库 | archived | pushed_at（UTC，API 返回值） | API 协议识别 |
| --- | --- | --- | --- |
| wykwey/YIClass | false | 2025-12-17T04:30:09Z | Apache-2.0 |
| gnahz77/SchedU | false | 2026-04-11T16:23:29Z | MIT |
| DaHedan/ClassPath | false | 2026-09-17T12:15:24Z | GPL-3.0 |
| YZune/WakeUpSchedule | false | 2018-04-07T01:28:19Z | null |

数据来源为各仓库的 `https://api.github.com/repos/{owner}/{repo}`，例如 [YIClass API](https://api.github.com/repos/wykwey/YIClass)。这些是调研快照，不承诺后续持续维护。

## 3. 移动端组件：明确复用到什么程度

| 能力 | 默认方案/来源 | 协议 | 采用方式 |
| --- | --- | --- | --- |
| 跨平台 UI | [Flutter](https://docs.flutter.dev/resources/faq) | 框架 BSD-3-Clause；引擎另有第三方声明 | Android/iOS 共用一套主要代码；使用 Material 组件、系统字号、现成对话框/底部菜单，不自建 UI 框架 |
| 状态管理 | [flutter_riverpod](https://pub.dev/packages/flutter_riverpod) | MIT | 统一异步加载与页面状态；首版使用手写 Provider，避免为了基础页面叠加状态管理框架 |
| 页面导航 | [go_router](https://pub.dev/packages/go_router) | BSD-3-Clause | 登录跳转、课程详情、消息定位统一路由；不自写路由器 |
| HTTP | [Dio](https://pub.dev/packages/dio) | MIT | 公用 API 客户端和会话刷新处理；业务页不各建一个请求客户端 |
| 本地缓存 | [Drift](https://pub.dev/packages/drift) | MIT | 基于 SQLite 的类型化缓存与迁移；缓存课表和教学记录草稿，不自建数据库引擎 |
| 本地提醒 | [flutter_local_notifications](https://pub.dev/packages/flutter_local_notifications) | BSD-3-Clause | 对已同步课表安排本地上课提醒；按插件说明做 Android/iOS 平台配置 |
| 远程推送 | [个推官方 Flutter 插件](https://github.com/GetuiLaboratory/getui-flutter-plugin)（包名 `getuiflut`） | [插件 MIT LICENSE](https://github.com/GetuiLaboratory/getui-flutter-plugin/blob/master/LICENSE)；云端推送服务另有服务条款/计费 | 通过插件接入 Android/iOS，再由服务端调用 REST API；插件开源不代表推送基础服务开源或永久免费 |
| 会话持久化 | [flutter_secure_storage](https://pub.dev/packages/flutter_secure_storage) | BSD-3-Clause | 直接使用系统存储能力保存登录凭据，不额外开发加密系统 |
| 周课表展示 | [calendar_view](https://pub.dev/packages/calendar_view) | MIT | **先做验证再决定依赖**；只承担事件布局，教学周与节次语义由业务模型提供 |

周课表组件验证限定为一个小任务：用七天、十二节、跨两节课、午休空档、并发重叠课程以及大字号场景测试；确认中文表头、点击/长按事件、定位当前周可实现。通过后用 `TeacherWeekView` 适配封装。如果按实际节次排列需要大量改源码，仅自写“周课表布局组件”，继续复用 Flutter 的布局、滚动、手势与无障碍能力。今日卡片按原图自行组合标准组件，无须引入一整个开源 App。

[Syncfusion Flutter Calendar](https://pub.dev/packages/syncfusion_flutter_calendar) 明确属于商业组件，需要商业许可或符合其社区许可条件。因此不作为默认无额外许可前提的依赖；不能因源码可见就把它当作 MIT 组件。

## 4. 后端与导入组件

| 能力 | 默认复用 | 协议/依据 | 本项目仍需写的部分 |
| --- | --- | --- | --- |
| 业务框架、ORM、迁移、管理后台 | [Django](https://github.com/django/django) | BSD-3-Clause；[5.2 LTS 发布说明](https://docs.djangoproject.com/en/5.2/releases/5.2/) | 学期、课次、交接等模型与操作。直接复用 Django admin 维护学校数据，不另做教师网页版 |
| REST API、序列化、权限接口 | [Django REST framework](https://github.com/encode/django-rest-framework) | [源 LICENSE](https://github.com/encode/django-rest-framework/blob/main/LICENSE.md) 为 BSD 三条款；API 自动识别曾返回 NOASSERTION，应以文件为准 | 业务 API、筛选、批量读写；禁止绕开服务层另写一套相同业务 |
| 登录/登出、注册、密码重置等账号端点 | [dj-rest-auth](https://github.com/iMerica/dj-rest-auth) | MIT；[官方端点文档](https://dj-rest-auth.readthedocs.io/) | 教师身份绑定、学校范围、移动端表单、重置链接跳回 App |
| 邮箱验证与账号流程 | [django-allauth](https://github.com/pennersr/django-allauth) | MIT；[官方能力表](https://allauth.org/features/) | 学校名单/邀请规则、邮件文案及 App 链接。只选定一套对 App 暴露的认证 API，默认由 dj-rest-auth 提供，不同时另暴露 allauth headless 的第二套同类流程 |
| Access/Refresh Token | [SimpleJWT](https://github.com/jazzband/djangorestframework-simplejwt) | MIT | 会话配置与移动端集成；不手写 JWT 编解码和签名 |
| OpenAPI | [drf-spectacular](https://github.com/tfranzel/drf-spectacular) | BSD-3-Clause | 标注业务输入输出、导出版本化接口文档 |
| XLSX 导入 | [openpyxl](https://openpyxl.readthedocs.io/en/stable/) | MIT/Expat | 表头映射、学校编码、教学周展开、预览与错误行报告。首版限定 `.xlsx`/CSV；旧 `.xls` 先转存为 `.xlsx`，不因为一个格式上整套 pandas |
| CSV 导入 | Python 标准库 `csv` | 随 Python 标准库 | 学校字段映射和编码处理，无须第三方依赖 |
| 数据库 | [PostgreSQL](https://www.postgresql.org/about/licence/) | PostgreSQL License | 关系与查询索引，不自建数据库层或另一套通用 ORM |

dj-rest-auth 的旧 `jazzband/dj-rest-auth` 链接会重定向到 `iMerica/dj-rest-auth`；开发文档应使用当前规范仓库地址。不要混用停止维护的 `django-rest-auth` 与 `dj-rest-auth`，也不要把旧版本安装示例中的配置项直接抄到新版本。[DRF 官方认证文档](https://www.django-rest-framework.org/api-guide/authentication/)列有相关集成说明。

## 5. 调课与业务算法的边界

首版目标是“现有课表中，找出一节课可以调整到的空闲时段”，不是重新为全校自动排课。数据库查询时间区间，再检查教师、实际选课学生集合、教室占用与容量，并按业务偏好排序即可。这里自写的是本校规则的组合，继续复用 ORM、数据库集合查询及事务，不开发通用优化求解器。

如果之后扩展到多门课同时调换、整学期排课或大量软约束，再引入 [Google OR-Tools](https://github.com/google/or-tools) 的 [CP-SAT](https://developers.google.com/optimization/cp/cp_solver)（Apache-2.0）。已有成熟求解器时不自写遗传算法或另一个约束引擎。首版不引入 OR-Tools，避免将小规模候选筛选升级成求解平台。

备注、交接确认、课时完成确认和失物协查单属于本项目差异化业务；现成课表组件不会自动提供这些能力。应按主实现文档的课次和课程实体落库，而非在课表卡片里追加松散字符串。

## 6. 部署与国内使用

- Django/PostgreSQL 部署到学校可达的服务器；Flutter App 通过同一个 API 工作。不要默认运行时必须访问 GitHub、Google 登录或海外 BaaS。
- [Flutter 官方中国网络说明](https://docs.flutter.dev/community/china)提供镜像配置指引；这是开发工具下载路径，不是 App 的运行时依赖。构建环境需连同 Android Gradle 依赖一并验证。
- [Supabase 自托管](https://supabase.com/docs/guides/self-hosting/docker)确实能复用认证、数据库、存储等能力，但完整套件涉及多个服务。对于本项目仍要实现大量关系业务，默认 Django 单体更便于同一团队统一维护。这是工作量判断，不是对 Supabase 国内可达性的实测结论。
- 不采用把 Firebase Authentication/Firestore 作为唯一核心后端的旧课表模板。当前没有对学校网络做连通性测试；首版应使用自行部署的后端，避免额外引入一套海外账号/数据服务。
- 本地上课提醒与远程消息推送要区分：本地插件不会自动把另一位教师刚提交的交接推到离线设备。站内消息落库并在启动/前台刷新；关闭 App 后的交接/调课通知默认采用个推与厂商通道、iOS APNs。只核实了文档与插件来源，尚未注册生产应用、配置通道或验证真机投递。

### 6.1 远程推送采用条件

个推的 [官方仓库](https://github.com/GetuiLaboratory/getui-flutter-plugin) 和 [pubspec](https://github.com/GetuiLaboratory/getui-flutter-plugin/blob/master/pubspec.yaml)表明包名为 `getuiflut`；不要因名称相近误选社区的 `getui_flutter` 或不存在的 `getui_flutter_plugin`。插件为 MIT；其底层 SDK、推送平台与厂商服务分别遵守各自条款。云端消息服务的计费查阅[官方计费说明](https://docs.getui.com/getui/billing/rules/)，不将 MIT 插件解释为整套服务永久免费。

接入依据包括[快速接入指南](https://docs.getui.com/getui/start/accessGuide/)、[服务端 REST V2](https://docs.getui.com/getui/server/rest_v2/introduction/)、[Android 厂商应用开通](https://docs.getui.com/getui/mobile/vendor/vendor_open/)、[厂商 SDK 集成](https://docs.getui.com/getui/mobile/vendor/androidstudio/)以及[iOS APNs 证书配置](https://docs.getui.com/getui/mobile/ios/apns/)。只集成 Android 主包不等于已经具备所有厂商的进程关闭后投递；需对应厂商参数、签名指纹与通道开通。iOS 要配置推送能力、APNs 凭据及匹配的 App 标识。

M0 即用一台计划试点的 Android 真机和一台 iOS 真机验证推送最小链路，并确认内部 APK 分发是否具备目标厂商通道开通条件；部分厂商要求上架或完成额外申请，不能到 M4 才发现。M4 完成试点机型覆盖和通知点击跳转等验收。服务端区分平台“已受理”和设备“已送达/用户已读”，不能收到 API 成功响应就把业务消息标为教师已看见。

## 7. 版本、引入与后续维护要求

1. 本次确认的是能力、协议和候选状态，不声称这些依赖已经共同通过编译。除 Django 5.2 LTS 的大版本选择外，不把网页显示的“最新版本”写成未经验证的兼容组合。
2. 开工时建立一套兼容基线：Flutter stable 的具体版本、Dart、Android/iOS 工具链、Python 3.12、Django 5.2 的具体补丁、DRF/认证库组合、PostgreSQL 具体主版本。用一次安装、迁移、登录与刷新、Android debug 构建及 iOS 构建验证后写进开发环境文档和锁文件。
3. 移动端提交 `pubspec.lock`；后端提交锁文件，记录 Python 版本；生产镜像固定版本。补丁升级走小 PR，不在每次构建时自动取最新版本。
4. 复制候选 App 的文件前记录原仓 URL、commit SHA、原路径、协议与修改范围；保留原版权/NOTICE。普通包依赖在发布时生成第三方声明并提供 App 内“开源许可”入口。候选调研表不等于已复制清单。
5. 每个依赖须有一个具体用途；不并用 Riverpod/Bloc/Provider 多套状态管理，不引入第二个本地数据库，不同时上 Celery/Redis/WebSocket 来完成可由普通请求与站内消息满足的首版需求。
6. 首批复用验证完成：账号登录/重置邮件深链、周课表组件、XLSX 预览与课次展开、本地缓存/通知；远程推送最小链路在 M0 并行验证，避免厂商开通问题推迟到发布前。若失败，先调整适配方案或替换现成组件，最后才自写。

## 8. 官方资料索引

以上表格的项目、包和协议链接即事实来源。还可直接查阅：

- [Flutter 架构指南](https://docs.flutter.dev/app-architecture)：采用其中适合小团队的职责分离，不把全部示例层级照搬。
- [Django admin](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/)：学校数据与教务操作的基础维护入口。
- [DRF Authentication](https://www.django-rest-framework.org/api-guide/authentication/)：认证与权限分别负责的边界。
- [dj-rest-auth Documentation](https://dj-rest-auth.readthedocs.io/)：现成账号端点与配置入口。
- [openpyxl Documentation](https://openpyxl.readthedocs.io/en/stable/)：文件格式与解析能力；网页版本标识仅是该文档的版本，不等于本项目锁定版本。
