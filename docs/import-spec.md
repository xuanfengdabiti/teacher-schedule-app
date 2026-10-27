# 课表数据导入规范 v1

> 2026-10-27。本文是导入合同；[CSV 模板](../templates/import/README.md)全部为虚构数据。B12a 已实现首次导入与相同内容的重复导入（解析、校验、预览、新建型提交）；B12b 已实现不影响课表的基础资料 merge 增量更新、任课范围版本递增、变更明细/警告和过期预览状态指纹；B12c 已实现会改变既有课次的 merge 更新：节次时间/时段组、校历例外新增与修改、规则周次/星期/节次/教室/教师/计划学时、sessions.csv 单次正式变化，以及课次的新增/不变/自动更新/自动取消/自动恢复/人工决定/已完成锁定分类和 Admin 决定项。snapshot 模式（B12d）仍留待后续；学期日期、首周周一、教学周数和时区属于结构性字段，增量导入仍报 `CHANGE_NOT_SUPPORTED_YET`，需新建学期或由教务人员手工处理。领域对象以[领域规则](domain-design.md)为准，本文规定外部文件如何映射到这些对象。

## 1. 输入、批次与基本类型

首版支持固定表头的 UTF-8 CSV（可带 BOM）和 XLSX。XLSX 每个工作表使用与 CSV 文件名相同的名称及字段，例如 `terms`、`calendar_exceptions`；本次仓库只提供 CSV，不提供已生成的 XLSX。通过 Python `csv` / openpyxl 读取；不开发任意表格自动识别器。

一个导入批次可以上传多个文件；没有变化的类型可以不上传，不要求每次重传完整集合。管理后台选择以下批次参数，不在每行重复：

| 参数 | 类型/必填 | 规则 |
| --- | --- | --- |
| school_code | 文本，必填 | 当前部署学校；服务端解析为 school_id，禁止按学校名称猜测 |
| source_system | 文本，必填 | 稳定来源标识，如 `school_export_v1`；同一来源后续不能随意改名，否则身份映射会断开 |
| mode | 枚举，必填 | `merge` 默认增量；`snapshot` 指定范围快照，范围和缺失处理必须显式确认 |
| scope | 结构化范围，snapshot 必填 | term_code、数据类型、指定 offering_codes/resource_codes；不能隐含“全校所有表” |
| source_updated_at | ISO 8601，必填 | 学校导出数据的时间；coverage 行有自己的来源时间时以行值为准 |

样例参数是 `school_code=DEMO-SCHOOL`、`source_system=demo_csv_v1`、`mode=merge`，只用于虚构环境。

### 1.1 通用类型

| 类型 | 约定 |
| --- | --- |
| code / ID | 非空文本，最长建议 64 字符；大小写敏感，去除首尾空格；始终保留前导零。姓名、课名不是去重键 |
| text | UTF-8 文本；按业务长度设正常字段上限；不在导入器中引入复杂内容审查 |
| date | `YYYY-MM-DD`；先按学校本地日期解释，不经过设备时区转换 |
| time | `HH:mm`，24 小时制；例 `08:00`；首版课程不跨午夜 |
| datetime | ISO 8601，必须含偏移，如 `2026-10-26T08:00:00+08:00`；存储为 aware datetime |
| integer | 十进制整数，不能用小数或科学计数法表示编号 |
| hours | Decimal(6,1) 的十进制文本，例如 `2.0`；不使用 float，不接受负值 |
| bool | 仅 `true` / `false`；不混用“是/否”、0/1 |
| codes | 一个 code 或 `|` 分隔的 code 集合；去重后按稳定顺序保存；不使用中文顿号或逗号 |
| optional | 空单元格；不要用 `null`、`-`、`无` 代替空值 |

CSV 逗号/换行按标准 CSV 引号规则处理，不添加注释行。XLSX 的编号列应设置文本；已丢失的前导零不能由程序自动猜回。拒绝公式单元格作为主键、日期、人数或学时的输入，要求导出静态值，避免依赖 Excel 缓存计算结果。

### 1.2 日期区间统一口径

- 学期 `start_date` / `end_date` **包含**两端；教学第 1 周周一可以早于开学日，但生成课次仍受实际学期起止日期限制。
- 选课、任课、覆盖用 `[effective_from,effective_until)` / `[from_date,until_date)`，上界日期**不含当天**。覆盖到 2027-01-24 时填写 `2027-01-25`。
- 时间占用用 `[start_at,end_at)`；09:40 结束与 09:40 开始不冲突。额外通勤缓冲另由学校参数配置。
- `completed` 必须有真实来源声明的完成量；日期已经过去不意味着完成。不能导入未来 completed 课次来增加进度。

## 2. 依赖顺序与文件清单

推荐将一套关联文件作为一个批次预览，先全部暂存、校验外键，再在一次事务内依次写入：

`terms → periods → teachers / classrooms → offerings → enrollments → calendar_exceptions → rules → sessions → room_blocks / resource_unavailability → coverage`

calendar_exceptions 与 rules 都提交后再展开具体课次；sessions 对展开结果补充稳定外部映射、单次安排或历史完成量。不要“展开规则一次、导入 sessions 再新增一次”。

若分批导入，外键可以引用此前同来源已存在对象，或本次暂存对象；引用不存在即整批报错。现有数据库其他来源对象不能仅凭同名静默合并；学校正式编号相同需要建立明确的来源映射。

以下表中“必填”指每行字段必须有值，所有规定表头均须保留；“可空”允许空值，但修改已有值的行为由第 5 节决定。

## 3. 字段定义

### 3.1 terms.csv：学期

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| term_code | code，必填 | 稳定外部学期编号；映射 Term.code，不用日期替代编号 |
| term_name | text，必填 | 展示名称 |
| start_date / end_date | date，必填 | start≤end；首版一个批次可含多个学期，但各行关系必须明确 |
| first_week_monday | date，必填 | 必须是周一；教学周按它计算，而非自然周号 |
| teaching_week_count | integer，必填 | >0，通常 16–24，实际以学校为准；超出周数的日期不生成规则课次 |
| timezone | text，必填 | IANA 时区；默认配置 `Asia/Shanghai`，不是随手机变化 |
| hour_minutes | integer，必填 | >0；每个学时分钟数的解释口径。Offering 默认继承，不据此自动判断完成 |

样例 2026-09-07 为第一周周一，2026-10-26 为第 8 周，2027-01-24 是第 20 周周日。

### 3.2 periods.csv：节次

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| term_code | FK→Term，必填 | 所属学期 |
| period_number | integer，必填 | >0；同学期唯一，按序递增 |
| start_time / end_time | time，必填 | start<end；同学期节次不能重叠 |
| block_code | code，必填 | 可连续上课的时段组，例如 morning/afternoon；不同组之间不生成调课候选 |

一个学期一套默认节次表；同组内课间休息计入展示的课程起止区间，但不额外增加学时。若真实学校同一学期有多套校区节次表，接入时再增加 period_scheme，而不是首版提前配置全部可能情况。现存导入长课可以跨组展示，调课候选不能跨午休等禁止间隔。

### 3.3 teachers.csv：教师名册

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| teacher_code | code，必填 | 学校正式工号；映射 TeacherProfile.teacher_no，`0001` 不得转成 `1` |
| formal_name | text，必填 | 正式姓名，不作主键 |
| department | text，可空 | 院系/教研室 |
| email | email，可空 | 用于注册后的名册匹配；去首尾空格并按既定邮箱归一化规则保存 |
| phone / wechat | text，可空 | 只能导入已有本人确认的自愿联系方式；样例为空 |
| phone_visibility / wechat_visibility | enum，必填 | `private / course_teachers / school_teachers`；默认 private |
| is_active | bool，必填 | 名册是否启用；停用不删除历史任课记录 |

名册导入只创建教师资料，不创建密码，不视为已验证邮箱，不自动将同名账号绑定。已关联账号的联系方式在 App 中由本人维护；后续名册导入即使给了 phone/wechat 或可见范围，也不覆盖这些字段，空值同样不会抹掉自愿填写的资料。修改正式工号需要明确迁移映射，不用“删旧教师新增教师”处理。

### 3.4 classrooms.csv：教室

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| classroom_code | code，必填 | 稳定教室编号 |
| campus / building / room_no | text，必填 | 三者组合在学校内唯一；room_no 为文本，允许 `A-301` |
| capacity | integer，必填 | ≥0；0 不表示无限容量 |
| equipment | codes，可空 | 设备代码集合，如 `projector|computer`；与 offerings.required_equipment 使用同一词表 |
| is_active | bool，必填 | false 不作为调课目标；历史排课仍保留 |

### 3.5 offerings.csv：课程、教学班与课程开设

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| term_code | FK→Term，必填 | 所属学期 |
| offering_code | code，必填 | 本学期一次开课的稳定编号；term 内唯一 |
| course_code / course_name | code / text，必填 | 目录 Course；code 在学校内稳定，名称可改 |
| class_code / class_name | code / text，必填 | TeachingClass；同学期 class_code 唯一；一个班可有关联多门 Offering |
| target_hours | hours，必填 | 课程目标学时，≥0；不是已完成量 |
| lead_teacher_code | FK→TeacherProfile，必填 | 课程负责人，必须包含在 teacher_codes |
| teacher_codes | codes→教师，必填 | 该 Offering 可参与协作的任课范围；不意味着每节课所有教师同时上课 |
| required_equipment | codes，可空 | 调课硬性设备要求；空表示无额外要求 |

同一 course_code/class_code 在多行出现时，其名称等共用字段必须一致；不允许以文件最后一行任意覆盖。同一 class_code 的两门不同 Offering 仍按实际 Enrollment 做学生冲突，不能把班内所有学生默认全部选上。

首版该模板将 teacher_codes 映射为整个学期有效的 TeachingAssignment，负责人 role=lead，其余 role=teacher。分周实际授课教师写在 rules/sessions。若名单在期中变更，需教务确认生效范围与保留的历史分工；不通过重导 offering 时静默删除过去的任课关联。B12b 对新增成员按学期开始日创建任课；对移除成员，若其仍有未来 scheduled 课次则整批拒绝，否则把有效任课结束于提交当日并保留历史。复杂分段任课可后续加独立 assignment 模板，本次不另引入一套重复维护入口。

### 3.6 enrollments.csv：实际选课

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| enrollment_code | code，必填 | 稳定选课记录编号，映射 ImportIdentity；有效期修改不改编号 |
| term_code | FK→Term，必填 | 必须与 Offering 学期一致 |
| offering_code | FK→CourseOffering，必填 | 实际选的课 |
| student_code | code，必填 | 学校正式学号，按文本保留前导零；按学校学号创建/引用 Student，不要求学生账号或姓名 |
| effective_from | date，必填 | 含当天，通常为开学日；允许学校提供转入日 |
| effective_until | date，可空 | 不含当天；必须>from；空表示持续有效，实际查询仍限所属学期 |

相同 Offering+学生的有效区间不得重叠；到期再选可有新的 enrollment_code 与不重叠区间。退课更新原行的 effective_until，不删除历史选课。候选日在退课前/后可能有不同结果，不能只取当前名单。

### 3.7 calendar_exceptions.csv：校历例外

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| exception_code | code，必填 | 稳定例外编号 |
| term_code | FK→Term，必填 | 所属学期 |
| date | date，必填 | 实际生效日期，学期内；同学期同日最多一条例外 |
| kind | enum，必填 | `cancel / replace / custom` |
| source_teaching_date | date，replace 必填 | 执行哪个原教学日期的基础排课；其余为空，必须同学期 |
| reason | text，必填 | 学校校历说明 |

`cancel` 停该实际日期的基础排课；`replace` 在目标 date 执行 source_teaching_date 的基础排课，教学周与教师分工取**原教学日期**，不取目标日自然星期。被迁移课次保留原规则键和 UUID，原日期不再多保留一条占用；目标日期原基础课次被替换，保留取消/变化记录。先按基础规则取源日，再应用例外，避免源日 cancel 后“无课可搬”。

样例：10-05 cancel，10-10 replace source=10-05，表示第 5 周周一课在 10-10 上；同一课次的 origin_teaching_date 仍是 10-05。首版不允许替换链、循环或同一源日迁往多个目标日；不依据公共节假日接口自行猜校历。`custom` 抑制目标日基础排课，仅保留 sessions 明确指定的特殊课次。

B12c 允许在 merge 中新增例外，并修改已有例外的 `date`、`kind`、`source_teaching_date`、`reason`。merge 不删除行，因此“撤销停课”不能靠删除文件行表达：把该 `exception_code` 的 `date` 移到纠正后的日期，是当前合并模式下可见的撤销/改期方式；旧日期的课次恢复为 `scheduled`，新日期的课次按规则取消。只改 `kind=custom` 仍会抑制该日基础排课，不能用于恢复。需要完全删除例外且不产生新日期时，由教务人员在 Admin 手工处理。修改例外所属学期仍报 `CHANGE_NOT_SUPPORTED_YET`。

### 3.8 rules.csv：重复排课规则

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| rule_code | code，必填 | 稳定规则编号；修改星期/周次/教室不能悄悄换编号 |
| term_code / offering_code | FK，必填 | 学期与开课一致 |
| weekday | integer，必填 | 1=周一…7=周日 |
| start_period / end_period | integer→Period，必填 | 两端含，start≤end；每个中间节次必须存在 |
| weeks | integer 集合，必填 | 用 `1|3|5`；每项 1…teaching_week_count，去重排序；不用含义不明的“单双周”字符串 |
| teacher_codes | codes→教师，必填 | 该课次真正需要出席的教师，须为 Offering 任课范围子集 |
| classroom_code | FK→Classroom，必填 | 排定教室 |
| planned_hours | hours，必填 | 每次课的计划学时；一般等于节次数，但按学校明确口径输入 |

展开原日期：`first_week_monday + 7×(week−1) + weekday−1`，再限制学期起止日期，应用校历与正式单次变化。时间取目标日期 start_period 的开始及 end_period 的结束。不同规则如生成完全相同 Offering/日期/时段，预览提示疑似重复；不因两个规则编号不同就默默双计。

规则只负责生成。已有调课、完成量、已发布备注的课次不因后续改规则而静默改写；未调整且未产生业务记录的未来课次才可按新规则更新。规则改星期或起始节时，原已生成课次的 origin 日期/节次仍是首次生成值，不能按新规则重算身份键；预览按原生成映射配对后更新同一 UUID。无法明确配对的受影响课次要求提供原 external_session_id 或由教务明确配对，不直接取消旧行再生成新行。

B12c 对规则 `weeks`（完整集合）、`weekday`、`start_period`/`end_period`、`teacher_codes`、`classroom_code`、`planned_hours` 做 merge 更新；`offering_code`/`rule_code` 变化仍报 `CHANGE_NOT_SUPPORTED_YET`。星期或起始节变化时，配对使用 origin 教学日期自身的 ISO 星期（即首次生成时的原规则星期）还原配置教学周，再与更新后展开结果按同一周配对；不使用数据库里已经改成新值的 `rule.weekday`，因此周一→周三后再次导入相同文件不会偏移一周。origin 三字段不重算。规则移除的周次不会删除旧课次，而是把目标设为 `cancelled`；规则新增的周次生成新 UUID。规则教师变化会替换未来未触碰课次的 `SessionTeacher`，并同时把被移出/新加入教师的 `assignment_scope_version` 递增一次（同一提交每人只加一次）。

### 3.9 sessions.csv：具体课次与历史完成记录

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| external_session_id | code，必填 | 来源系统稳定课次编号；映射到本地稳定 UUID，后续调课继续使用同编号 |
| term_code / offering_code | FK，必填 | 学期和开课一致 |
| origin_rule_code | FK→ScheduleRule，可空 | 来自规则的课次必须填；独立新增课次为空 |
| origin_teaching_date | date，规则课次必填 | **首次生成时的原教学日期**；调课后不变；首次绑定按原规则版本验证周次/星期，后续更新查已保存的生成映射 |
| origin_start_period | integer，规则课次必填 | 原规则起始节；同 origin_rule/date 构成稳定生成键 |
| session_date | date，必填 | 当前实际排定日期，学期内；可与 origin_teaching_date 不同 |
| start_period / end_period | integer→Period，必填 | 当前节次范围；普通调课保持节数与计划学时 |
| start_time / end_time | time，可空且成对 | 空时按节次派生；学校已有非标准课可显式覆盖，必须正时长；不会因此自动拆课或计算完成量 |
| classroom_code | FK→Classroom，必填 | 当前排定教室 |
| teacher_codes | codes→教师，必填 | 当前实际授课教师，须属于该 Offering 任课范围 |
| status | enum，必填 | `scheduled / completed / cancelled`；调课不是独立占用状态 |
| planned_hours | hours，必填 | 该课次计划量，≥0 |
| actual_hours | hours，completed 必填 | scheduled 必须为空；cancelled 填 0.0；completed≥0 且显式给值，不由日期/备注推断 |
| completed_by_teacher_code | FK→教师，completed 必填 | 来源记录确认教师；须属于该课次实际教师；其未绑定 App 账号时保留 TeacherProfile 及来源记录，不假造账号 |
| completed_at | datetime，completed 必填 | 来源完成确认时间，不早于课程结束，不晚于导入时刻；其他状态为空 |
| completion_ref | text，completed 必填 | 学校完成记录/确认依据；Importer 同时记录本批次操作人，不能混作来源确认人 |
| change_ref | text，条件必填 | 不同于规则/校历计算结果的单次正式时间/教室/教师变化，填写学校调整依据；与计算结果一致时可空 |

#### 匹配与防止重复计算

1. 先按 school+source_system+`external_session_id` 查 ImportIdentity。已存在则更新对应 UUID，不按新日期另建。
2. 首次出现且 origin 三字段齐全：用规则本地 ID+origin_teaching_date+origin_start_period 找已展开课次；找到后为**同一课次绑定外部编号**并更新允许字段。样例三行都走此路径，不新增第四十一条课。
3. 若三字段只填了一部分、找不到生成课次、指向不同 Offering、或外部编号与原生成键分别指向两个对象，整批报错并要求配对；不能自动“合并得差不多”。
4. origin 三字段均空才视为独立单次课；必须先预览确认是新增，避免来源遗漏规则引用造成双计。外部无稳定课次 ID 的学校先导出系统生成的映射表，后续文件带映射编号，不能临时用当前日期拼 ID。
5. 当前日期/节次/教室改变时，origin 与 external_session_id 都不改变。变更已有已批准调课、已完成课次或教学记录对象时，进入人工决定项而非自动覆盖。原记录不删除，保存 SessionChange/ImportBatch。
6. `sessions.csv` 的 `offering_code` 必须与 `origin_rule_code` 指向规则的 offering 一致；已有 `external_session_id` 的课次也不能改为另一 offering。不一致报 `SESSION_OFFERING_MISMATCH` 并定位到行，不能生成“B offering + A rule”的课次、占用 A 的原生成键，或把原课次误判为 unchanged。

B12c 的 sessions.csv 单次变化口径：

- 该行与规则/校历计算结果在日期、起止节、显式起止时间、教室或授课教师上不同，视为单次正式变化，必须填写 `change_ref`；缺 `change_ref` 报 `CHANGE_REF_REQUIRED`，整批不能提交。只改 `status`/完成量而不改安排时不需要 `change_ref`。
- 显式单次变化优先于规则展开，即使该课次已开始/结束；前提是课次仍为 `scheduled` 且未被教学记录、交接、调课申请、完成记录或非导入的正式调课触碰。
- 导入产生的 `SessionChange.source=import`，后续导入/校历恢复不把它当作人工触碰；B31 或 Admin 的正式调课为 `source=reschedule`，仍会锁定课次。
- completed 课次不因普通增量被改写：任何日期、节次、时间、教室、状态或完成量差异都生成 `completed_locked` 人工决定项，只提供 `keep_existing`。仅授课教师或 `planned_hours` 不同时保持原状并计入“已完成保持”，不生成决定项、不改历史记录。导入明确写入的历史 completed 课次仍按首次导入规则创建，但不会覆盖已有完成量。
- 由规则/校历/节次变化引起、涉及日期/节次/时间/教室且开始时间早于提交时刻的课次默认保持现状（计入“过去保持”），不自动移动或取消；如该课次另有教学记录/调课申请/完成记录，则进入人工决定项，由教务选择保留或采用。仅教师或 `planned_hours` 不同的未触碰课次仍可自动更新。显式 sessions.csv 单次变化不受“过去保持”限制。

不设置“期初已完成学时”字段。学期中途接入时，只通过明确 `status=completed`、`actual_hours`、完成依据的历史课次导入实际完成量；全部 completed 课次求和即课程完成量，多教师只计一次。没有历史证据就显示过去待确认，不能把第 1–7 周全标完成。章节分配在 App 中补充，未归入专题的 actual_hours 仍计课程总量。

### 3.10 room_blocks.csv：非课程教室占用

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| block_code | code，必填 | 稳定占用编号 |
| term_code | FK→Term，必填 | 归属学期 |
| classroom_code | FK→Classroom，必填 | 占用教室 |
| start_at / end_at | datetime，必填 | 同学期范围，end>start；可以跨多节，不能用“全天”模糊字符串 |
| reason | text，必填 | 考试、维修等；课堂占用仍来自 ClassSession，不再重复写入此表 |

### 3.11 resource_unavailability.csv：人员不可用

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| block_code | code，必填 | 稳定人员占用编号 |
| term_code | FK→Term，必填 | 所属学期 |
| resource_type | enum，必填 | `teacher / student`，映射 PersonBlock 的两个 FK 之一 |
| resource_code | FK，必填 | teacher 时引用 teacher_code；student 时引用已存在 Student.student_no（本模板集由 enrollments 建立） |
| start_at / end_at | datetime，必填 | end>start，归属学期范围 |
| reason | text，必填 | 教师会议、学生考试等已知不能安排课程的时间 |

没有额外限制可不上传记录，但需要 coverage 明确说明该范围无额外占用且完整；空表本身不证明可调。学生既有课程又有 PersonBlock 时，冲突人数按学生 ID 去重。

### 3.12 coverage.csv：数据覆盖声明

| 字段 | 类型/必填 | 规则 |
| --- | --- | --- |
| coverage_code | code，必填 | 稳定声明编号，映射 DataCoverage |
| term_code | FK→Term，必填 | 所属学期 |
| kind | enum，必填 | 见下表八类；不能把“我的课表”声明成所有类别完整 |
| scope_key | text，必填 | `school` 或明确资源范围，见下文 |
| from_date / until_date | date，必填 | 半开区间，覆盖目标日才有效；until>from |
| completeness | enum，必填 | `complete / partial / missing` |
| source_updated_at | datetime，必填 | 来源更新时间，不是手机同步时间 |
| confirmation_ref | text，complete 必填 | 学校导出范围或教务确认依据；允许“确认本范围无额外占用”，不允许导入器自动补 complete |

| kind | 证明的内容 | 可用范围键 |
| --- | --- | --- |
| calendar | 校历/节次/日期例外 | school |
| teacher_assignments | 任课范围与分工 | school、offering:开课编号 |
| teacher_schedule | 教师在全校范围内的正式排课 | school、teacher:工号 |
| student_enrollments | 实际选课名单和有效期 | school、offering:开课编号 |
| student_schedule | 相关学生在全校的课程安排 | school、student:学号 |
| classroom_schedule | 教室课程占用 | school、classroom:教室编号 |
| room_blocks | 非课程教室占用 | school、classroom:教室编号 |
| person_blocks | 教师/学生额外不可用 | school、teacher:工号、student:学号 |

`school` 仅指当前 school+term 内所有相应资源，不能跨学校或学期。细粒度范围键中的编号必须真实存在；不允许任意文本作为“范围”。针对候选的教师、学生、教室、Offering，所有必要类别都完整才可返回 available。已知有冲突时即使另有数据缺口仍返回 conflict，并附缺口；缺数据但没查到冲突返回 unverified。

同 kind/scope_key 的声明如有效期重叠，不把它们简单拼成“看起来完整”；预览要求明确替换旧声明或裁开区间。修改已存在覆盖声明由稳定 coverage_code 更新；已有记录从学校明确的 partial/missing 改为 complete 时，预览显示 warning 供教务核对 confirmation_ref，不直接阻止提交，也不会静默改掉校历/排课数据。coverage 与所证明的数据提交同事务；未上传某类记录可以确认“已核对无额外占用”，但不能对尚未提供的整校课表凭空确认完整。

样例学校仅有模板中的教师/学生/课程，complete 仅用于联调，不对任何真实学校产生含义。

## 4. 去重、来源映射与更新

所有来源映射唯一键为 `(school_id, source_system, entity_type, external_id)`；entity_type 防止教师 `0001` 与学生 `0001` 被当作同一实体。批次文件中同类型同外部编号重复即报错，即使两行相同也要求去掉重复，避免不透明的“最后一行赢”。

| 类型 | 外部标识/业务约束 |
| --- | --- |
| Term | term_code；学校内 term_code 唯一 |
| Period | term_code+period_number；同学期序号唯一 |
| TeacherProfile / Student | teacher_code / student_code；学校内正式编号唯一，跨来源须显式关联 |
| Classroom | classroom_code；校区+楼宇+房间号也须唯一 |
| Course / TeachingClass / Offering | course_code / term_code+class_code / term_code+offering_code |
| Enrollment | enrollment_code；同时检查 Offering+Student+effective_from 唯一及有效区间不重叠 |
| CalendarException / Rule | exception_code / rule_code；同学期同日期例外另有唯一约束 |
| ClassSession | external_session_id；规则生成键另有唯一约束；两者配对到同一个 UUID |
| RoomBlock / PersonBlock / DataCoverage | 各自 block_code / coverage_code，按不同 entity_type 识别 |

同文件重导输出 `unchanged`，不重复课次、完成量或消息；文件哈希辅助识别重复，不取代行级身份映射。改日期不产生新身份；真正新增补课才使用新课次编号并在预览确认新增。

有教学记录、完成量、交接或已批准调课的对象禁止级联删除；缺失数据按状态取消/失效处理，保留历史引用。变更正式课表共用 scheduling service；Admin、CSV、未来教务 API 不各自写不同的批准逻辑。

B12c 的课次写入统一走 `apps.scheduling.import_changes` 的批量入口 `apply_import_session_batch`：整批只锁一次 State/Term/申请/课次，批量 `bulk_create`/`bulk_update` 课次、批量写 `SessionTeacher`、批量 `SessionChange`，一次 `record_changes` 记录全部 session/term/展示变更，每个受影响学期只递增一次 `Term.schedule_version`。写入仍复用同一套 `version+1`、`SessionChange` 前后快照、`ChangeLog`、教师 `schedule_changed` 汇总通知（每位受影响教师每批一条，`event_key=import:{batch_id}:{teacher_id}`，正文列出新增/调整/取消/恢复节数）和待确认交接核对提示（每条交接每批一条）。导入侧不直接改 `ClassSession` 字段。`sessions.csv` 的 `change_ref` 写入 `SessionChange.approval_ref` 与原因；人工“采用导入结果”以教务人员填写的依据说明作为 `approval_ref`，并标记 `forced_override=true`。同一批次的申请提示按申请去重，每条申请每批最多一条。

## 5. 预览、合并模式与提交

### 5.1 预览是必需步骤

解析暂存不写正式业务表。预览返回：总行数、new/changed/unchanged/missing/errors/manual_decision 计数、逐行问题、来源和范围摘要、预计课次数与学时变化、预览时 schedule_version。

B12c 预览额外返回课次目标统计（新增、不变、自动更新、自动取消、自动恢复、人工决定、其中冲突、已完成锁定、过去保持、已完成保持）、逐课次前后对比（日期、节次、时间、教室、教师、状态、计划/实际学时）和完整人工决定项列表。受影响学期的目标课表由更新后的 periods/calendar_exceptions/rules 叠加本批 sessions.csv 显式行，调用既有 `calendar.plan_term_sessions` 得到，不另写周次/校历展开逻辑。

冲突检查复用 B30 的批量机制：按受影响学期和时间窗口一次性预加载 `_load_evaluation_data`，再用同一个 `_TargetEvaluator` 在内存中评估所有目标（`policy="import"`），不按课次逐个查库。评估使用本批最终目标安排：把同批将移动/恢复的课次从旧占用中移除，并以目标日期、节次、教室和目标授课教师作为占用叠加；同批新增课次也作为占用可见，跨学期批次不会把其他学期的目标占用删掉。本批新增或修改的教室占用、教师/学生不可用同样进入占用叠加，修改前的旧区间不再算占用。因此两节课同批迁入同一时段/教室会互相看见，迁出的旧位置也不会误报。教师检查使用 `session_targets` 的目标 `teacher_ids`，不是数据库旧 `SessionTeacher`。它不套用调课候选的“目标必须晚于当前时间、非周末、非校历例外日、同一时段组、节数不变、教室启用/容量/设备、当前课次必须 scheduled”等策略条件；导入的是学校已确定的课表，这些不是数据错误。B30/B31 的默认策略和逐目标入口保持不变。发现已知冲突的课次即使未被触碰，也进入人工决定项（5.1 的“现有排课冲突不能悄悄接受”）。

每条问题至少包含 file/sheet、物理行号（表头为第 1 行）、字段、原值、稳定错误码、中文说明与修正建议。例如 `sessions.csv:4 origin_rule_code RULE_NOT_FOUND`。不能只说“导入失败”，也不能部分丢行后显示全部成功。

必要校验包括外键/类型/唯一性、节次日期、任课范围、选课有效期、规则与课次配对。现有排课冲突不能因导入而悄悄接受：列出冲突课程/资源，教务需要学校正式安排依据再明确接受例外；“已导入”不能等于“系统保证无冲突”。计划课时合计与课程目标不一致可作为需确认的业务提示，不能自改总学时。

### 5.2 merge：默认增量

- 只更新文件出现的稳定编号；文件中未出现的记录保持原样。
- 核心必填字段用本次显式值更新；可空的展示资料空值默认保留已有值，避免空名册覆盖教师本人联系方式。清空操作在 Admin 明确执行；v1 不在 CSV 发明第二套清空标记。
- scheduled/completed/cancelled 是显式状态，不按空 actual_hours 自动推断。已经 completed 的课次重导 scheduled，或明确实际学时不同，进入人工决定项，不能因为普通增量覆盖进度。
- rules.weeks 或 offerings.teacher_codes 属于完整集合字段：一行给出该对象当前的集合，不把新旧列表盲目合并。移除成员/周次影响已有记录时必须预览和确认；不会自动删除历史课次/任课。
- B12b 的 merge 白名单：terms 只改 term_name；teachers 改 formal_name/department/email/is_active，未关联账号时按名册更新 phone/wechat/可见范围，空值保留，已关联账号的联系方式一律以 App 为准；classrooms 改校区/楼宇/房间号/容量/设备/启用状态；offerings 改 Course/TeachingClass 名称、target_hours、负责人、必需设备和 teacher_codes；enrollments 只改 effective_until；room_blocks/resource_unavailability 改起止时间和原因；coverage 按 coverage_code 改日期范围、completeness、来源时间和确认依据。表外差异仍报 `CHANGE_NOT_SUPPORTED_YET`。
- 修改 Term 名称写 `term` ChangeLog；修改教师名称、教室展示信息、Course/TeachingClass 名称时，为所有出现在 SessionSummary 中且受影响的课次批量写 `session` ChangeLog，version 保持课次当前值，不为展示字段递增。
- offerings.teacher_codes 新增教师时创建 `TeachingAssignment`（学期开始日生效）；移除教师时若该教师在该 offering 仍有未来 scheduled 课次，B12c 会先看同一批次是否已把生成这些课次的规则教师改为他人：全部改走则允许结束任课，否则整批报 `CHANGE_NOT_SUPPORTED_YET`。允许时把有效任课结束于提交当日（学校本地日期），不删除历史任课。负责人变化同步 `lead_teacher`、任课 role 和教学计划 owner。任何新增、结束或角色变更都在同一事务递增该教师的 `assignment_scope_version`，让 App 清除失去权限的缓存并重新 bootstrap。
- 同一 coverage_code 从 partial/missing 改为 complete 时保留 warning；`confirmation_ref` 仍按 complete 必填校验，错误阻止提交。
- B12c 的 merge 白名单追加：periods 的 start_time/end_time/block_code；calendar_exceptions 的 date/kind/source_teaching_date/reason；rules 的 weeks/weekday/start_period/end_period/teacher_codes/classroom_code/planned_hours；sessions 已有行的 session_date/节次/显式时间/教室/教师/status/完成量差异（安排差异另需 change_ref）。学期结构性字段（start_date/end_date/first_week_monday/teaching_week_count/timezone）仍不支持，报 `CHANGE_NOT_SUPPORTED_YET`。
- 课次分类规则：现有课次与目标完全一致为 unchanged；不同且“未被触碰”时自动更新/取消/恢复；被触碰时生成 ImportDecision，默认不改。未被触碰指：状态为 scheduled、开始时间晚于提交时刻、无 `SessionChange`（`source=import` 的导入自身记录除外）、无 TeachingNote、无 Handoff（source/target）、无 draft/submitted RescheduleRequest、无 CompletionRecord。恢复路径允许当前为 cancelled（这正是恢复目标），但仍要求无上述业务记录；过去课次按 3.9 的“过去保持”口径处理。
- 教师变化：替换 SessionTeacher；被移出和新加入的教师在本次提交中各自只递增一次 `assignment_scope_version`。`planned_hours` 变化更新未触碰课次，已触碰的进入人工决定。已完成的课次按 3.9 的 completed_locked 处理。
- 待审批的 submitted 调课申请遇到课次被导入移动/取消/恢复时，申请状态不变，`check_summary` 写入 `import_update` 提示并给申请人发 `reschedule_result` 消息，事件键 `import:{batch_id}:request:{request_id}`。
- 导入结果的课次统计按新增、自动更新、自动取消、自动恢复、人工保留、人工采用分别返回。

### 5.3 snapshot：限定范围快照

> 实现状态（2026-10-27）：**未实现，已暂停**。Admin 不提供 snapshot 上传入口；即使直接构造 `mode=snapshot` 批次，预览也会报 `SNAPSHOT_NOT_IMPLEMENTED` 并拒绝提交，不会静默按 merge 执行。以下为保留的规格，等 App 主流程可用后再恢复实现。

仅对明确选定的来源、学期、类型、课程/资源范围识别 missing，不影响其他来源或范围。提交前列出每个缺失对象的拟动作，用户必须明确确认。

- 缺失未来且未产生教学业务的课次：拟取消，不硬删 UUID；缺失规则：停止后续生成，保留历史。
- 有备注/交接/完成量/正式调课的缺失课次：人工决定，不能自动取消并造成完成量变化。
- 缺失教师/教室/课程目录：只提停用建议，不跨历史级联；缺失选课记录需明确退课生效日期，不能直接按文件上传日猜。
- DataCoverage 不因“这是一份快照”自动 complete；声明范围必须与实际导出范围一致。

### 5.4 提交和过期预览

errors 或未解决的 manual_decision 存在时整批不提交；可修文件重传或在预览明确解决。提交事务先按统一锁顺序获取同步状态/学期写入锁，检查预览版本，锁内重新计算受影响预览；若文件、行统计、问题、字段旧值、任课关系、教学计划负责人或 `Term.schedule_version` 与预览时不同，整批回到待预览，不沿用旧判断。预览状态指纹保存在 ImportBatch，只用于批次过期判断，不改变课程业务数据。

B12c 人工决定项（ImportDecision）：预览生成，重新预览整体重建；若 proposed/current/kind/options 未变则保留此前选择，否则清空并要求重新选择。Admin 预览详情页逐条选择，或批量“全部保留现状”；选择 `apply_import` 必须填写依据说明。存在未决定项时不能提交。提交时锁内重新预览的决定项必须与数据库中的决定项一致，否则按过期预览拒绝；提交只对选择 `apply_import` 的课次执行写入，`keep_existing` 只记录在批次结果中。决定后正式写入前，系统按“实际采用 + 保留现状”的最终组合再次批量检查资源冲突；若保留的课次使自动采用的目标出现新冲突，提交报 `IMPORT_FINAL_CONFLICT` 并回滚，不能按预览时已被放弃的目标方案落库。

正式表更新、ImportIdentity、ImportBatch、SessionChange、coverage、对象版本/ChangeLog 及站内消息同事务提交。单次提交失败整体回滚，不产生“半门课导入”。大文件先按学期/范围拆批；没有实际需要时不增加异步平台。课次写入按整批批量处理：一次 `bulk_create`/`bulk_update`、一次 `SessionChange.bulk_create`、一次 `record_changes`、每个受影响学期一次版本递增，查询数不随课次数量线性增长；教师消息按批次汇总到每人一条。

提交结果提供批次编号、changed/unchanged 数量、生成/匹配/取消课次量、冲突例外摘要及可下载错误/身份映射报告。暂存预览文件属于部署数据，不放公开 Git。v1 不做任意数据库历史快照一键回退；错误数据修正后通过新批次更新。

## 6. 样例联调验收

1. 上传模板集预览：12 个文件均可解析，外键完整；两名教师、三名学生、三间教室、两个 Offering。
2. 规则展开 40 次；10-05 的两次课移动到 10-10，不增加数量。sessions 三行匹配同一批规则课次，不额外新增。
3. 两次显式 completed 合计 4.0 学时，另 38 次 scheduled；S-DATA-W07 虽然已在过去仍待确认。重复同批导入保持不变。
4. 2026-10-26 显示第 8 周，刑事侦查 1–2 节由 0002 授课，数据分析 3–4 节由 0001 授课；改周不串教师。
5. 学生 000001 跨两门 Offering；将刑事侦查移至同日 3–4 节时发现学生冲突。10-28 的维修/会议/考试分别作为硬占用，不被课表空白覆盖。
6. 删除一条 enrollment 后用 merge 导入，不自动退课；改为 snapshot 时必须指定范围和退课日期，先展示缺失处理。
7. 在已完成/已调课课次上更改当前日期，不改变 external_session_id 或 origin；预览要求人工确认，确认后 UUID 和原教学记录保持。
8. 移除 student_schedule 或 person_blocks 的覆盖声明后，相关候选返回 unverified；其他类别 complete 不能替代。

导入自动化测试覆盖第 1–3 条以及重复导入、CSV/XLSX 对照、错误定位和 Admin 流程；B12b 追加覆盖缺行不误删、教师/教室/学期名称、选课退课与 B30 目标日有效名单、任课新增/结束/负责人变化、coverage warning、课表相关变更拒绝、并发预览过期和 session/term ChangeLog。B12c 追加覆盖：周次删减自动取消且重复导入不变、改星期/起始节再次导入保持 ID/origin、停课例外新增与改期恢复、节次时间调整（含 completed_locked）、sessions 单次变化与 change_ref、offering/规则/既有课次归属不一致拒绝、同批同目标/迁出迁入 overlay、同批新增课次作为占用、跨学期占用不丢失、新增/修改教室及人员占用、冲突决定项（保留/采用）、实际保留/采用组合提交拒绝、已批准调课后规则变化、未决定不可提交与锁内过期拒绝、换教师（仅教师和时间）与可见性、待审批申请提示、学期结构性字段拒绝；并用 40/200 课次实测“首次导入提交查询数不随课次线性增长”“节次调整预览查询数不随课次线性增长”和每位教师每批一条汇总消息。B30/B31 原有测试保持原样通过。snapshot、缺失对象处理和真实学校数据仍不在当前实现范围。
