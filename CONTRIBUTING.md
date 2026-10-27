# 开发规范

本文约束后续开发。后端和 Flutter 工程已建立；当前交付目标是本地演示作品，具体检查结果以实际验收记录为准。完整产品方案中的上线事项不作为本轮演示的前置条件。

## 1. 开发原则

- 先读[实现方案](docs/implementation-plan.md)和[领域规则](docs/domain-design.md)，再动功能代码；发现业务冲突在同一变更里修正文档。
- 先查现有包和开源项目，记录选型依据。优先组合成熟库；自写只覆盖教师业务差异和必要适配。
- 采用单仓库、模块化单体。一个功能先端到端完成再扩展；不为未来假设建立通用平台。
- 教师可见的标题、按钮、消息与失败提示遵循[界面措辞约定](docs/ui-wording.md)：使用教学日常用语，不展示内部版本号、对象编号、HTTP 或同步实现说明。
- 不引入两套状态管理、认证或本地数据库，不提前上微服务、事件总线、复杂审批引擎。
- 只在输入边界、外部服务和业务约束处做必要校验；内部已知类型不重复层层判断。禁止空 catch、吞错误和默认伪造成功。

## 2. 目录和职责

移动端按 feature 组织；页面不直接发请求或写数据库。Repository 统一网络/本地来源，Controller/Provider 管页面状态，Widget 负责渲染与交互。只有调课等复杂功能需要 domain 层，普通页面无需强制三层复制。

后端按 accounts、academics、scheduling、teaching、classrooms、inquiries、notifications、imports 分模块。

| 位置 | 职责 |
| --- | --- |
| Model | 字段、关系、唯一约束、简单业务不变量 |
| Serializer | 输入字段验证、序列化输出 |
| View/ViewSet | HTTP、账号和操作权限、调用业务服务 |
| service | 跨表写入、事务、正式调课与学时确认规则 |
| selector | 复杂查询、聚合、预取；简单查询可直接用 ORM |
| Admin | 维护入口；调用相同 service，不直接复制批准/导入逻辑 |

没有复杂逻辑的 CRUD 不必强制拆 service/selector；不要引入后端 Repository 再包装一遍 Django ORM。

## 3. 命名、格式与类型

- Dart：官方风格，文件 `snake_case.dart`，类 `UpperCamelCase`，变量/函数 `lowerCamelCase`；使用 `dart format` 与 `flutter_lints`。
- Python：PEP 8 风格，文件/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`；Ruff 统一格式与静态检查。公共 service、selector 和外部适配器标注输入输出类型，不给每个局部变量堆注解。
- API JSON 和数据库字段用 `snake_case`；接口状态枚举使用英文稳定值，中文只是显示文案。
- 单个文件按职责拆分；超过约 400 行应评估，允许合理例外，不为满足行数机械分割。
- 变量表达业务含义，避免 `data1`、`temp2`；`Course`、`CourseOffering`、`ClassSession` 三层不能混叫 `course`。
- 日期、时区转换集中处理；学时用 Decimal/十进制字符串。不要用 float 或设备当前自然周推算教学周。
- 注释解释业务原因与外部限制，不复述代码。复杂调课规则附对应验收用例编号。

## 4. 必须守住的业务边界

1. ClassSession ID 稳定；调课不删课重建，不丢备注，不多计学时。
2. 冲突按目标日期实际选课名单检查，名单缺失返回 `unverified`；有已知冲突返回 `conflict`。
3. 申请不是正式安排；批准、导入和人工修改共用排课写入口，事务内再次校验。
4. 备注保存、交接已读、交接确认、授课完成是四种不同动作。
5. 多教师同一课次只累计一次课程学时；取消课不计。
6. 后端计算最终进度和冲突，移动端仅作展示；不得出现两套不同算法。
7. 教室记录是排定记录；联系方式按本人设置展示。

## 5. API 和数据迁移

- 先写 OpenAPI schema 和最小示例，再联调；业务失败有稳定 code 和可读 detail。按实现方案使用标准 HTTP 状态，不将所有错误返回 200。
- GET 不产生正式业务变更；可重试写入使用幂等资源路径或 client_mutation_id。
- 关键编辑带 base_version，冲突保留本地输入；禁止静默覆盖其他教师内容。
- 新字段优先可空/有合理默认，先兼容再收紧。数据库变更必须带 migration；不手工改生产表冒充迁移。
- 自定义 User 模型在第一次迁移之前确定。引用账号用 AUTH_USER_MODEL，不写死框架默认表。
- 导入以来源稳定编号关联，不能用姓名匹配主键；别把具体日期当永不改变的课次身份。
- 历史正式教学记录不级联删除；删除/取消语义由领域服务明确处理。

## 6. 依赖、复用和配置

- 引入新包前，确认用途、许可证、维护情况、双端兼容性，并更新[第三方记录](THIRD_PARTY.md)。已经解决同一问题的包优先复用。
- 提交 Flutter `pubspec.lock` 和后端 `uv.lock`，锁定 Flutter/Python/数据库版本。升级是独立可回退变更，不运行时追踪 latest。
- JSON 模型先用 `json_serializable` 生成，确实需要不可变模型复杂能力时再引入 Freezed；不要求所有简单状态都生成代码。
- 生成代码若提交，要标明来源并在 CI 核对与输入一致；禁止手改生成文件。OpenAPI 导出是合同，客户端生成器先小范围验证再定。
- `.env.example` 仅列配置键与无效示例；开发配置和真实凭据分开。公开 App ID 与服务端 MasterSecret 区分清楚。
- 使用结构化日志记录操作 ID、错误 code 与耗时；不输出密码、完整 token 或整张真实学生名单。无需另建日志平台才能开始开发。

## 7. 最小开发与 CI 检查

M0 创建后按锁定工具链运行；路径以实际脚手架为准：

| 对象 | 基础检查 | 何时追加 |
| --- | --- | --- |
| Flutter | `dart format --output=none --set-exit-if-changed .`、`flutter analyze`、`flutter test` | 修改原生配置时构建 Android/iOS；关键流程跑 integration_test |
| Python | `uv run ruff format --check .`、`uv run ruff check .`、`uv run pytest` | 事务/锁/聚合使用 PostgreSQL 集成测试 |
| Django | `uv run python manage.py check`、`uv run python manage.py makemigrations --check --dry-run` | 新环境迁移和发布前检查 |
| 合同 | `uv run python manage.py spectacular --file /tmp/openapi.yaml --validate --fail-on-warn`；CI 用 `--fail-on-warn` 校验 OpenAPI，需零警告零错误 | 接口字段或路由有变化 |
| 文档 | 相对链接有效、样例字段一致、无过期运行说明 | 变更需求/导入格式时同步 |

CI 分路径触发；纯文档不必构建完整双端。PR 上至少执行受影响检查，主分支构建 Android；iOS 从 M0 起在 Mac runner 验证，发布包由正式签名流程构建。

测试应能发现真实业务错误：学生跨班冲突、同时审批抢同教室、多人重复学时、导入覆盖已调课、离线草稿冲突等。不要测试框架自身，也不要为低风险文字修改新建测试套件。已通过的检查在无新变化时不反复跑。

## 8. Git 与评审

- 主分支 `main`；分支形如 `feat/teacher-login`、`fix/session-hours`、`docs/import-spec`。一个提交只做一项连贯改变。
- 提交格式建议 `feat(schedule): 增加教学周筛选`、`fix(teaching): 避免多人授课重复累计学时`。中文正文可用，作用域稳定。
- PR 说明先写用户问题与最终行为，再写验证结果、必要的迁移/配置。不要粘贴全部开发日志。
- 提交前检查 diff，排除临时导出文件、凭据、构建产物。无需为此建设复杂审核流程。
- 接口、规则、依赖或部署变化，在同一个 PR 更新对应文档；发现新问题先记录，不把不相关大重构混入当前功能。

## 9. 完成标准

功能完成需同时满足：用户可操作的闭环、后端持久化、真实错误/空状态、必要离线行为、权限范围、相应测试与使用说明。消息/邮件/推送必须分别说明哪些已实测；模拟成功不算外部集成完成。

本期四核心、账号与基础闭环以[任务清单](docs/delivery-backlog.md)和[业务验收](docs/domain-design.md)为准。仅 UI 完成不能关闭对应整项功能。
