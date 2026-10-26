# 课表数据导入规范 v1

> 2026-10-26。本文是导入合同；[CSV 模板](../templates/import/README.md)全部为虚构数据。B12a 已实现首次导入与相同内容的重复导入（解析、校验、预览、新建型提交）；字段更新、snapshot、已调课/已完成/有教学记录对象的人工决定项、过期预览重算和换授课教师后的可见性处理在 B12b。领域对象以[领域规则](domain-design.md)为准，本文规定外部文件如何映射到这些对象。

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

名册导入只创建教师资料，不创建密码，不视为已验证邮箱，不自动将同名账号绑定。已关联账号的联系方式在 App 中由本人维护；后续名册导入的空值不会抹掉自愿填写的资料。修改正式工号需要明确迁移映射，不用“删旧教师新增教师”处理。

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

首版该模板将 teacher_codes 映射为整个学期有效的 TeachingAssignment，负责人 role=lead，其余 role=teacher。分周实际授课教师写在 rules/sessions。若名单在期中变更，需教务确认生效范围与保留的历史分工；不通过重导 offering 时静默删除过去的任课关联。复杂分段任课可后续加独立 assignment 模板，本次不另引入一套重复维护入口。

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

同 kind/scope_key 的声明如有效期重叠，不把它们简单拼成“看起来完整”；预览要求明确替换旧声明或裁开区间。修改已存在覆盖声明由稳定 coverage_code 更新，新增来源不能自动覆盖学校明确的 partial/missing。coverage 与所证明的数据提交同事务；未上传某类记录可以确认“已核对无额外占用”，但不能对尚未提供的整校课表凭空确认完整。

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

## 5. 预览、合并模式与提交

### 5.1 预览是必需步骤

解析暂存不写正式业务表。预览返回：总行数、new/changed/unchanged/missing/errors/manual_decision 计数、逐行问题、来源和范围摘要、预计课次数与学时变化、预览时 schedule_version。

每条问题至少包含 file/sheet、物理行号（表头为第 1 行）、字段、原值、稳定错误码、中文说明与修正建议。例如 `sessions.csv:4 origin_rule_code RULE_NOT_FOUND`。不能只说“导入失败”，也不能部分丢行后显示全部成功。

必要校验包括外键/类型/唯一性、节次日期、任课范围、选课有效期、规则与课次配对。现有排课冲突不能因导入而悄悄接受：列出冲突课程/资源，教务需要学校正式安排依据再明确接受例外；“已导入”不能等于“系统保证无冲突”。计划课时合计与课程目标不一致可作为需确认的业务提示，不能自改总学时。

### 5.2 merge：默认增量

- 只更新文件出现的稳定编号；文件中未出现的记录保持原样。
- 核心必填字段用本次显式值更新；可空的展示资料空值默认保留已有值，避免空名册覆盖教师本人联系方式。清空操作在 Admin 明确执行；v1 不在 CSV 发明第二套清空标记。
- scheduled/completed/cancelled 是显式状态，不按空 actual_hours 自动推断。已经 completed 的课次重导 scheduled，或明确实际学时不同，进入人工决定项，不能因为普通增量覆盖进度。
- rules.weeks 或 offerings.teacher_codes 属于完整集合字段：一行给出该对象当前的集合，不把新旧列表盲目合并。移除成员/周次影响已有记录时必须预览和确认；不会自动删除历史课次/任课。

### 5.3 snapshot：限定范围快照

仅对明确选定的来源、学期、类型、课程/资源范围识别 missing，不影响其他来源或范围。提交前列出每个缺失对象的拟动作，用户必须明确确认。

- 缺失未来且未产生教学业务的课次：拟取消，不硬删 UUID；缺失规则：停止后续生成，保留历史。
- 有备注/交接/完成量/正式调课的缺失课次：人工决定，不能自动取消并造成完成量变化。
- 缺失教师/教室/课程目录：只提停用建议，不跨历史级联；缺失选课记录需明确退课生效日期，不能直接按文件上传日猜。
- DataCoverage 不因“这是一份快照”自动 complete；声明范围必须与实际导出范围一致。

### 5.4 提交和过期预览

errors 或未解决的 manual_decision 存在时整批不提交；可修文件重传或在预览明确解决。提交事务先按统一锁顺序获取同步状态/学期写入锁，检查预览版本，若期间有人正式调课/修改相关数据，重新计算受影响预览，不沿用旧判断。

正式表更新、ImportIdentity、ImportBatch、SessionChange、coverage、对象版本/ChangeLog 及站内消息同事务提交。单次提交失败整体回滚，不产生“半门课导入”。大文件先按学期/范围拆批；没有实际需要时不增加异步平台。

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

B12a 自动化测试已覆盖第 1–3 条以及重复导入、CSV/XLSX 对照、错误定位和 Admin 流程；更新/快照/人工决定项（第 5–8 条中的更新与缺失处理）仍留待 B12b。样例的 40 课次、两节完成课和 10-05→10-10 替换由服务端测试验证，不以本文推算代替实现。
