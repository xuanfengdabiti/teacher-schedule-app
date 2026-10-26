# 领域规则与数据设计

> 根据原始开发概要和 `ui.jpg` 整理，与[详细实现方案](implementation-plan.md)共同作为设计基线。以下为建议的产品与业务规则，不表示已经取得学校数据或已接入教务系统。首版交付手机 APP，覆盖四个核心方向及账户、数据导入、通知、设置等完整使用闭环；教师网页版暂不开发。

## 1. 范围与角色

### 1.1 首版必须能完成的事

- 教师完成邮箱密码注册、邮箱验证、教师名册关联、登录、忘记密码、修改密码、退出登录与停用/注销入口，重启后保留登录状态；填写个人信息及自愿公开的联系方式。邮箱与名册工号均匹配才自动关联，其他情况进入 pending 等待教务确认。
- 管理员通过现成管理后台导入学期、时间节次、教师、课程开设、实际选课名单、教师任课、教室、排课与调停课数据，预览错误后确认导入；不再额外开发一套运营网站。
- 教师进入 APP 看当天课表；切换周课表、教学周、教学班；打开某一课次，处理备注、交接、计划、调课、教室查询。
- 四个方向都具备可操作的首版流程：调课候选查询与申请记录、教学记录与交接确认、教学计划与学时统计、教室使用与联系方式。复杂审批系统、学校统一身份认证、自动教务抓取可以后续接入，但不能把这些集成当作四个方向完全不实现的理由。
- APP 内消息中心、课程提醒、手动刷新、数据更新时间、断网查看已缓存课表、备注离线草稿及重试同步。

### 1.2 角色

| 角色 | 必要能力 |
| --- | --- |
| 任课教师 | 看自己的课表与本课程进度，填写自己的授课记录，查看同课程授权共享记录，发起调课、交接与联系请求 |
| 课程负责人 | 同上；维护同一个课程开设的教学计划，纠正已完成学时或分工 |
| 教务管理员 | 现成后台中维护基础数据、处理导入、确认学校批准的调课结果、修正排课异常 |

首版每个部署服务一个学校；不做租户平台、收费体系、学生端、家长端、实时聊天、复杂审批流引擎。角色与数据范围用简单归属关系实现，不先建通用权限配置平台。

## 2. 核心对象与数据关系

区分“课程名称”“本学期这门课的开设”“每次上课”三层。这直接决定多教师协同、调课和学时能否算对。

| 对象 | 关键字段及规则 |
| --- | --- |
| `Term` 学期 | 学校、名称、开始/结束日期、教学第 1 周周一、学校时区、schedule_version；一个日期映射唯一教学周 |
| `Period` 节次 | 序号、开始/结束时间；由学校配置，不写死为 45 或 50 分钟 |
| `CalendarException` 校历例外 | 日期、停课/替换教学日、被替换的教学日期、说明；用于节假日、补课日 |
| `TeacherProfile` 教师 | 账号关联、学校教师编号、正式姓名、显示名、院系、联系方式及各自可见范围、账号关联状态、assignment_scope_version |
| `Student` 学生 | 学校学生编号；是冲突判断的对象，不要求首版有学生账号 |
| `TeachingClass` 教学班 | 学期、名称、编号；展示和筛选用途，不能代替实际选课关系 |
| `Course` 课程目录 | 课程代码、课程名；可跨学期复用 |
| `CourseOffering` 课程开设 | 学期、课程、教学班、总目标学时、负责人、每学时分钟数、进度基准版本 |
| `Enrollment` 实际选课 | 课程开设、学生、生效/失效日期；冲突按目标日期筛选有效关系 |
| `TeachingAssignment` 任课分工 | 课程开设、教师、角色、生效区间；课次实际授课教师还需明确关联 |
| `Classroom` 教室 | 校区、楼宇、房间号、容量、设备、可用状态；同名教室按校区区分 |
| `RoomBlock` 教室占用 | 教室、起止时间、原因、来源；覆盖考试、维修等非课程占用 |
| `PersonBlock` 人员不可用 | 教师或学生、起止时间、原因、来源；覆盖考试、会议及明确登记的不可用时段 |
| `ScheduleRule` 排课规则 | 课程开设、星期、起止节、教学周集合、教师、教室；只作生成规则与导入溯源 |
| `ClassSession` 课次 | 稳定 ID、课程开设、日期、起止时间、起止节、教室、状态、计划学时、完成学时、版本号、来源编号；通过 `SessionTeacher` 关联实际授课教师 |
| `SessionChange` 课次变更 | 同一课次的调整前后值、原因、确认人、确认时间、批准依据描述；保留实际变更记录 |
| `RescheduleRequest` 调课申请 | 原课次、目标时间与教室、说明、查询时版本、当前状态、学校审批结果；申请本身不占用教室 |
| `TeachingNote` 教学记录 | 课次、作者、已讲、未完成、下次计划、草稿/已发布、更新时间、版本号 |
| `Handoff` 教学交接 | 课程开设、来源记录、发送教师、接收教师、目标课次可空、内容、待确认/已确认/已撤回、确认时间 |
| `TeachingPlan` 教学计划 | 课程开设、负责人、基准版本、计划状态 |
| `PlanItem` 计划专题 | 教学计划、排序、标题、预计学时、计划完成课次/周次、实际状态 |
| `SessionProgress` 课次专题关联 | 课次、专题、本次完成学时；同一课次可以覆盖多个专题 |
| `Notification` 消息 | 接收教师、类型、对象 ID、内容摘要、已读时间、唯一事件键 |
| `Inquiry` 协查请求 | 发起人、教室、起止时间、物品描述、open/replied/closed、结案说明及时间；支持勾选多个相关教师 |
| `InquiryRecipient` 协查接收人 | 请求、被选中的教师、相关课次引用、首次查看时间；不因后续调课默默替换收件人 |
| `InquiryReply` 协查回复 | 请求、回复教师、未见到/已保管/其他、文字说明、回复时间；只供相关接收人回复 |
| `ImportBatch` 导入批次 | 文件、类型、数据范围、预览错误、确认状态、统计、确认人；用于导入结果和修正追踪 |
| `ImportIdentity` 导入身份映射 | 学校、来源系统、对象类型、外部编号、本地对象 ID；维持源数据到稳定课次 ID 的映射 |
| `DataCoverage` 数据覆盖 | 数据类别、学期/资源范围、生效日期范围、complete/partial/missing、来源更新时间、确认批次；显式表达检查覆盖 |
| `DeviceRegistration` 推送设备 | 账号、平台、厂商、客户端安装 ID、推送客户端 ID、启用状态、更新时间；退出时解除绑定 |
| `DeliveryAttempt` 投递记录 | 消息、设备/渠道、尝试序号、状态、下次尝试时间、供应商消息 ID、失败摘要；支持查询和有限重试 |
| `ClientMutation` 客户端去重 | 账号、client_mutation_id、操作类型、请求摘要、结果对象/版本、响应摘要；重试返回原结果 |
| `SchoolSyncState` 同步计数器 | 学校、已提交 change_seq；写事务锁定此行后分配序号，保持同步游标与提交顺序一致 |
| `ChangeLog` 同步变更 | 学校、change_seq、对象类型/ID、所属任课范围、version、tombstone、变更时间；支持增量拉取 |

`ClassSession.id` 与日期时间无关。调课更新同一个课次，不先删后建；教学记录、交接、完成学时、专题关联都继续指向原 ID。

### 2.1 通用字段与实际建表约束

除关联表和计数器外，业务表统一 UUID 主键、created_at/updated_at；可被移动端修改的对象增加正整数 version。school_id 是单校部署的数据归属字段，不建设多租户平台。学时为 `Decimal(6,1)`，时间为带时区 datetime；日期有效区间采用 `[effective_from,effective_until)`，失效日期为空表示持续有效。

| 对象 | 必须落地的字段/约束/索引 |
| --- | --- |
| Term / Period / CalendarException | Term：school_id+code 唯一，start_date≤end_date，schedule_version 默认 1。Period：term_id+ordinal 唯一，start_time<end_time。例外：term_id+date 唯一，kind=cancel/replace/custom；replace 必须有 source_teaching_date；索引 term_id+date |
| TeacherProfile / Student / TeachingClass | 教师：school_id+teacher_no 唯一，user_id 可空且一对一；邮箱归一化后匹配名册，不靠姓名匹配；assignment_scope_version 默认 1，任课可见范围变化时同事务递增，并随 me/同步响应返回，客户端据此清除失去范围的缓存并重新 bootstrap，保留但不自动重试无权限课程草稿。学生：school_id+student_no 唯一，学号是文本。教学班：term_id+class_code 唯一；名称允许重复 |
| Course / CourseOffering | Course：school_id+course_code 唯一。Offering：term_id+offering_code 唯一；course_id、teaching_class_id、lead_teacher_id、target_hours≥0、minutes_per_hour>0；索引 term_id+teaching_class_id。一个 Offering 代表一学期一个教学班的一次开课，教学班单独聚合多门课 |
| Enrollment / TeachingAssignment | Enrollment：offering_id、student_id、effective_from、effective_until，三元组 offering_id+student_id+effective_from 唯一；索引 student_id+effective_from+effective_until 及 offering_id+effective_from。任课同理，增加 role。服务层拒绝同一对象同一人员的重叠有效期；数据库检查 until>from |
| Classroom / RoomBlock / PersonBlock | 教室：school_id+campus+building+room_no 唯一，capacity≥0，equipment 为代码集合。RoomBlock 索引 room_id+start_at，检查 end_at>start_at。PersonBlock 为 teacher_id/student_id 两个可空 FK 且恰好填写一个，索引分别为 teacher_id+start_at、student_id+start_at；同样检查时间正长 |
| ScheduleRule / ClassSession / SessionTeacher | Rule：offering_id、weeks 列表、weekday、period_start/end、classroom_id、source_key；源 key 在来源范围内唯一。Session：offering_id、start_at、end_at、origin_teaching_date、origin_period、rule_id 可空、classroom_id、status、planned_hours≥0、actual_hours、completed_by_teacher_id/at、completion_ref、recorded_by_user_id、version；scheduled 的 actual_hours 为空，completed 必填非负量和教师确认来源，cancelled 为 0；检查 end_at>start_at；索引 offering_id+start_at、classroom_id+start_at。规则生成键 rule_id+origin_teaching_date+origin_period 唯一且调课后不变；SessionTeacher：session_id+teacher_id 唯一，索引 teacher_id+session_id |
| SessionChange / RescheduleRequest | Change：session_id、request_id 可空、before_json、after_json、session_version、actor_id、reason、approval_ref、forced_override、recorded_at；session_id+session_version 唯一。申请：session_id、applicant_id、target_date、target_start_period/end_period、target_start_at/end_at、target_classroom_id、reason、base_session_version、checked_schedule_version、check_summary、status、needs_verification、decision_by/at/ref/note、forced_override；索引 applicant_id+created_at、status+created_at；同一课次+申请人的 submitted 申请唯一；状态迁移与正式生效统一服务 |
| TeachingNote / Handoff | Note：session_id+author_id 唯一，draft_covered/unfinished/next_plan 与 published_covered/unfinished/next_plan 分开、published_at、published_version、version；published_at 与 published_version 同时为空或非空；保存草稿不覆盖已发布快照。Handoff：offering_id、note_id/version 可空、sender_id、recipient_id、target_session_id 可空、content_snapshot、status、read_at、acknowledged_at；索引 recipient_id+status+created_at，acknowledged 必须有确认时间；一条交接一个接收人 |
| TeachingPlan / PlanItem / SessionProgress | Plan：offering_id 一对一、owner_id、baseline_version、status、version。Item：plan_id+position 唯一，title、planned_hours≥0、due_date 或 due_session_id、baseline_version。Progress：session_id+plan_item_id 唯一、actual_hours≥0；索引 plan_item_id。每次完成确认事务校验分配和≤课次实际学时、专题与课次同属一个 Offering |
| Inquiry / InquiryRecipient / InquiryReply | Inquiry：initiator_id、classroom_id、from_at、to_at、item_description、status、closed_at、close_note；索引 initiator_id+status+created_at，检查 to_at>from_at。Recipient：inquiry_id+teacher_id 唯一，related_session_ids 快照。Reply：inquiry_id、author_id、result=not_seen/in_custody/other、body、created_at；索引 inquiry_id+created_at。允许同一接收人补充回复，每条回复追加保存；只有发起人结案 |
| ImportBatch / ImportIdentity / DataCoverage | Batch：source_system、term_id、kind、mode、scope_json、file_hash、status=uploaded/previewed/committed/failed、preview_base_schedule_version、row_counts、errors_json、committed_by/at；索引 term_id+created_at。Identity：school_id+source_system+entity_type+external_id 唯一。Coverage：school_id、term_id、kind、scope_key、from_date、until_date、completeness、source_updated_at、batch_id，索引 term_id+kind+scope_key+from_date；覆盖包含目标范围且标记完整才可证明该类数据完整，不能以“无记录”代替 complete |
| Notification / DeviceRegistration / DeliveryAttempt | 消息：recipient_id+event_key 唯一，type、object_type/id、read_at；索引 recipient_id+created_at、recipient_id+read_at。设备：installation_id 唯一、user_id、provider、provider_client_id、platform、enabled、last_seen_at。投递：notification_id+device_id+channel+attempt_no 唯一，status=pending/processing/sent/failed、next_attempt_at、provider_message_id、provider_delivered_at 可空、error_code、error_summary；索引 status+next_attempt_at；供应商受理 sent、设备回执 provider_delivered_at 与 App 已读 read_at 分开 |
| ClientMutation / SchoolSyncState / ChangeLog | Mutation：user_id+client_mutation_id 唯一、operation、request_hash、result_object_id/version、response_json、completed_at；同 key 不同请求内容返回 409，不重复写。State：school_id 一对一、last_seq。Log：school_id+change_seq 唯一、entity_type/id、offering_id 可空、version、tombstone；索引 school_id+change_seq。所有同步相关写操作在事务中先锁 State 行，日程写再锁 Term 行，更新对象、递增版本、分配序号与日志同事务，统一锁顺序 |

跨表合计、有效期不重叠、状态流转由同一业务服务校验，不为每条规则引入通用规则引擎。索引围绕已列读取路径建立，试点测量后再调整；不预建所有字段的组合索引。

## 3. 课表与校历

### 3.1 日期、周次与课次生成

1. 时间统一按学校时区处理，默认 `Asia/Shanghai`；设备时区变化不改变课表所属日期。
2. 教学周按“第 1 周周一”计算；学期开始日期可以不是周一，首周未开学的日期不生成课次。
3. 排课规则保存明确的周次集合，例如 `[1, 3, 5, 7]`；“单周/双周”只是编辑捷径。不能只存一条每周重复规则而丢失考试周、停课周。
4. 先展开基础课次，再应用校历例外和已确认的单次变更，形成 APP 与冲突查询共同使用的最终课次表。
5. 调休需要明确“某个实际日期执行哪天的教学安排”。不得根据星期几猜测，也不允许同一次调休意外生成两次课程。
6. 自动生成只修改未来、尚未人工调整且未完成的课次；已经发布记录或已确认调课的课次在导入预览中列为冲突，要求管理员选保留或替换，不静默覆盖。
7. 已确定口径：停课、`custom`、`replace` 目标日上的基础课次以 `cancelled` 状态保留，不删除、不忽略；保留原生成键与 UUID，校历以后撤销时可恢复同一课次。

### 3.2 展示及状态

- 今日首页按开始时间排序；展示课程名、时间、教学班、教室、授课教师、临时调整标记；空课日展示“今天没有课程”和本周入口。
- 周课表支持滑动/选择教学周及回到本周；同一时段多课并列显示并标记冲突，不覆盖前一条。
- 课程操作菜单对应参考图：教学班课表、查询可调时间、教学备注、教学交接、教学计划、课程详情、申请调课。
- 课次状态采用 `scheduled / completed / cancelled`；调课是变更事件，不是 `rescheduled` 占用状态。取消的课次不参与占用、剩余课次和完成学时统计。
- “本学期所有课”“某个教学班课表”“我的课”应共用同一批课次数据，区别只在筛选条件。

## 4. 调课冲突与推荐

### 4.1 查询输入

课次 ID、目标日期范围、允许的节次范围、候选教室范围。默认一次调整一个完整课次，固定当前 SessionTeacher 集合、planned_hours 与连续节次数；目标起止时间取目标日节次表，不固定墙钟持续分钟数。非标准时段由教务登记；自动换教师、拆分合并和两课交换留到后续版本。

### 4.2 计算步骤

1. 根据目标日期构造连续时间窗口；候选必须在学校开放授课时段内、学期内，不能跨午休等明确禁止的间隔。
2. 排除当前课次自身，读取窗口内所有有效课次、教室占用和教师/学生明确不可用时段（PersonBlock）。时间交叉按半开区间判断：`a.start < b.end && b.start < a.end`；前一节结束等于后一节开始不算时间重叠。目标日期存在任意 `CalendarException`（`cancel`、`custom`、`replace` 目标日）时不作为自动调课候选日；`replace` 目标日执行别的基础教学日安排，原课次本就不在该日执行，因此统一采用“有任意校历例外即排除”的保守口径。
3. 检查本课次所有实际授课教师，不使用课程负责人替代实际教师；多教师同时授课需要全部可用。
4. 取当前 CourseOffering 在目标日期有效的实际选课学生集合，与每个重叠课次所属 CourseOffering 在该日有效的选课集合求交，再与重叠 PersonBlock 中的学生合并；按学生 ID 去重返回冲突人数及冲突课程/不可用摘要。原教学班不同但有同一个选课学生也算冲突；同一行政班但无人选该课则不能误报。
5. 检查候选教室的有效占用、容量和必需设备。容量与实际有效选课人数比较；设备是硬要求时不得将不满足的教室列为可用。
6. 查询 DataCoverage，确认目标日期及资源范围内校历、任课关系、教师/全校课次、实际选课、人员不可用、教室课程及非课程占用是否完整。只有学校明确声明该类没有额外限制时，才能将无 PersonBlock/RoomBlock 视为完整；表内零行本身不证明完整。无数据与确认空闲是不同结果。同一 `kind` 与 `scope_key` 在目标日既有 `complete` 又有 `partial`/`missing` 时按缺失处理；学校明确声明的 `partial` 不得被同一范围内的另一条 `complete` 覆盖。
7. 首版使用数据库区间查询、集合求交和固定排序即可；不需要 AI 模型，也不需要先上排课约束求解器。若后续发展为同时安排上百课次的全局优化，再评估求解器。

### 4.3 返回三种结果

| 分类 | 产品表现 |
| --- | --- |
| `available` 可推荐 | 三类资源数据覆盖完整，教师与学生冲突人数为 0，教室可用，容量和设备满足 |
| `conflict` 有冲突 | 显示明确冲突原因，仅供查看；不显示“推荐”，不能以绿色表示可直接调课 |
| `unverified` 待核验 | 例如缺实际选课名单、未导入其他学院课程、教室维修占用不完整；明确列出缺哪类数据，不能写“无冲突” |

参考图中“学生冲突 1 人”的时段不能继续放在正常推荐列表。若既有已知冲突又缺数据，分类优先为 conflict，同时列出缺失数据；无已知冲突且数据不完整才是 unverified。首版允许保存草稿；conflict 只能保存草稿、不能提交，unverified 可以提交为标记 `needs_verification` 的申请，但不能普通批准，正式确认前必须有学校核验结果或走强制登记。

结果先按 available / conflict / unverified 分组，只有 available 称为推荐。组内采用透明排序：同教室 → 与原上课日期的绝对天数差更小 → 与原开始时间的分钟差更小 → 开始时间、结束时间、教室编号稳定排序。不额外插入“同校区优先”改变这一基线。若校园有跨校区通勤要求，学校可配置缓冲分钟，作为候选与正式确认共同使用的条件；首版不计算地图路线。

### 4.4 申请与生效

申请状态：`draft → submitted → approved / rejected / withdrawn`。学校可以继续使用现有线下流程；管理员在现成后台记录学校批准结果，即可完成 APP 首版闭环，不强制对接学校审批接口。

已确定的实现口径（B31）：

- 一次申请只对应一个课次。调课保持课次 UUID、现有 SessionTeacher 集合、planned_hours 和节次数不变，只允许改变目标日期、节次和教室。批量调课、换课、换教师和拆并课不在本版本范围。
- 申请人必须是该课次的实际授课教师，或该 offering 当前有效的任课教师。提交时服务端重新检查目标，不能沿用候选查询的旧结果。`available` 可直接提交；`unverified` 可提交，但申请标记 `needs_verification=true`；`conflict` 只能保存草稿，提交返回 409 `RESCHEDULE_CONFLICT` 并附冲突明细。
- 硬条件不满足时返回 409 `TARGET_NOT_ALLOWED` 和中文原因，不保存为提交状态。硬条件包括：目标节次存在且连续、属于同一时段组（不能跨午休/上下午）、不在过去、在学期教学周内、目标日无任何校历例外、遵守调用方传入的周末策略、教室启用且属于本校、容量满足目标日实际选课人数、必需设备满足、目标节次数与源课次一致。硬条件不能被强制安排绕过。
- 同一课次同一申请人已有 `submitted` 申请时，不允许再提交新的申请（409 `REQUEST_ALREADY_PENDING`）；草稿数量不受限制。
- 撤回仅允许申请人本人操作，`draft`/`submitted` 变为 `withdrawn`；其他状态返回 409 `REQUEST_NOT_WITHDRAWABLE`。撤回和驳回都不修改课表。普通教师不能批准申请。
- 普通批准只适用于事务内重新检查结果为 `available` 的申请。`unverified` 或 `conflict` 不能普通批准（统一写入口分别返回 `DATA_UNVERIFIED`/`SCHEDULE_CONFLICT`，附评估结果）；学校已书面批准时，管理员使用“登记学校强制安排”，必填学校审批编号/依据，记录 `forced_override=true`、风险说明和评估快照。强制安排不能绕过上述硬条件。
- 批准生效时，按统一顺序先锁 SchoolSyncState，再锁本学期 Term 行，再锁 RescheduleRequest 行，最后锁 ClassSession 行，并在锁内重新检查当前课次版本与目标资源；通过后在同一事务内更新课次、递增课次/学期版本、写 SessionChange 前后快照与同步日志、创建通知及待投递记录。导入等不由申请发起的写入口同样先锁 State/Term，再按需锁申请或课次。申请本身不占用教室。
- 若批准时发现新冲突或数据缺口，申请保持 `submitted`，服务端把最新评估写回 `check_summary` 并返回业务错误；不直接覆盖，也不把课次移动到未确认目标。待审批期间首页继续显示原安排；批准后显示新安排及变更提示，原日期可在变更记录中查看。
- 首版不支持交换两节课、批量连环调课和抢占预约，也不提供“一次确认两条变更”的隐含换课入口。交换课统一列为后续版本能力。

## 5. 教学备注与多教师交接

### 5.1 教学记录

- 已讲内容、未完成内容、下次计划三个输入区，支持保存草稿和发布；语音输入优先调用系统键盘听写，首版不搭建语音识别服务。
- 每位授课教师对同一课次最多一份当前记录；草稿正文与最近已发布正文/版本分开保存。多位老师的记录分别显示作者，避免多人编辑同一个文本框互相覆盖。
- draft 仅作者可见；教师显式点击发布后才有 published 版本。同一 CourseOffering（学期+课程+教学班）的任课教师查看该 published 版本；不是同一 TeachingClass 的所有课程教师都能共享。跨学期同名课程不默认共享。重新编辑草稿不改变其他人已看到的发布快照，直到再次显式发布。实现口径：写备注只允许该课次的实际 `SessionTeacher`；读取只允许该课次实际授课教师或该 offering 当前有效任课教师，不按教学班或课程名称扩大范围。
- 打开某课次时，“上次记录”只从同一 CourseOffering 中筛选：课次状态 completed、授课时间早于当前课次、且存在 published 备注；按课次实际 `start_at` 倒序选最近一课次，再显示该课次各教师的 published 记录，并带作者、日期与课次引用。当前/更晚课次、未完成课次的发布备注不进入“上次记录”。补录旧课即使更晚发布，也按上课时间排序，不能替换最近一次已完成课次。普通历史列表可以另行查看，并显示其课次状态。
- 用户补录历史记录后，下次提醒仍按上课日期排序，不按最近修改时间误把旧课放最前。
- 调课前后记录绑定同一课次，不丢失；取消且未授课课次的草稿仍可查看，但不作为最近已授课记录。
- 实现口径：每次保存草稿或发布都令备注 `version+1`，保存草稿只更新 draft 列，发布才复制到 published 列并设置 `published_at/published_version`。写接口必须携带 `client_mutation_id` 与 `base_version`；不存在备注时 base_version=0，存在时必须等于当前 version，否则返回 409 并附服务器当前三段文本和 version。没有本人备注、或三段草稿去掉首尾空白后都为空时，发布返回 409 `NOTHING_TO_PUBLISH`，不创建备注/mutation/ChangeLog，也不允许用空内容覆盖既有发布快照。同一账号同 key 同请求返回原响应且不重复写；同 key 不同正文返回 409，自动重试只保存草稿，不自动发布。备注写入与 ChangeLog 在同一事务提交，不改变课次完成状态或学时。

### 5.2 交接

发布记录后可选择下一位任课教师并生成交接，预填未完成内容和下次计划，发送前可以编辑。系统默认找同一课程开设的下一课次教师，允许在该课程任课教师中改选。

- 交接是明确发送的对象，有发送者、接收者、关联课次和确认状态；普通教学记录浏览不自动等于交接确认。
- 接收人点击“确认收到”才写确认时间；显示已读不代表同意内容。
- 交接一经发送就保存内容快照及原备注发布版本，不随草稿或发布版本修改而变化；发送者需要补充说明或发送新交接，保留前文。一次交接只对应一个接收人，选择多位教师时生成多条独立交接。
- 调课或换教师后，系统提示发送者核对待确认交接的接收人，不自动把已有交接转给另一个人。
- 交接可以无附件；首版不做富文本协作、实时共同编辑、审批会签。

## 6. 教学计划与学时

### 6.1 口径

- 一次课的“课次”数量与“学时”数分开存储。例如 1–2 节为 1 次课、2 学时；不能把课表卡片数当学时。
- 每学时分钟数、两节课之间的休息由学校配置。默认计划学时来自节次数量；非标准时段由管理员填写计划学时，不直接用整段墙钟分钟除以某个常数。
- 过了结束时间只显示“待确认完成”，不会自动累计已完成学时。教师确认本次已完成，必要时填写实际完成学时；取消课次完成学时为 0。
- 同一课次有两名教师同时授课，课程进度只累计一次；教师个人工作量如需统计另建口径，不把两位教师的记录数相加。
- 总目标学时来自本课程开设；已完成为有效 `completed` 课次完成学时之和；剩余目标学时为 `max(目标 − 已完成, 0)`；剩余课次为所有未取消且未完成课次数，不因日期已经过去而漏计。另显示“过去待确认课次”数量（end_at<当前时间且 scheduled），提示教师补确认或纠正；未来剩余课次可作为独立辅助指标。超额学时单列显示，不能简单隐藏。
- 补课只有在确实增加独立课次时新增 ID；把原课移到另一日不增加课次和总学时。

### 6.2 专题计划

负责人维护章节/专题名称、排序、计划学时和计划完成课次/周次。某课次确认完成时，教师选择涉及专题并分配本次完成学时；专题分配合计不得超过本课次完成学时；未分配部分标为“未归入专题”，保证课程总数可对账。

延期判断以已到计划截止的专题与实际完成状态比较，展示“原计划第 8 周完成，目前还差 2 学时”；未填写计划则显示“未设置基准”，不擅自判定落后。取消课和停课应提示负责人重新排计划；系统不能静默把基准日期后移后宣称没有延期。

历史学时修正需要填写简短原因；课程汇总与专题汇总由服务端统一计算，手机不再实现另一套算法。首次导入若学期已过半，导入明确标注 actual_hours 和完成确认来源的历史具体课次；不再引入一项无课次依据的“期初已完成学时”，也不能把所有历史课次自动视为完成。

## 7. 教室历史与失物协查

- 按教室 ID 和日期显示排定课次、时间、课程、授课教师、取消或调课状态；当前课次高亮。前后课次以最终有效安排为依据，保留变更历史供展开查看。
- 页面固定说明“排定课程记录，不代表实际到场或物品去向”。不实现物品责任自动判断。
- 教师可以分别为电话和微信选择 `private / course_teachers / school_teachers`；默认 `private`，页面明确“未提供”或“暂不可见”。首版不加复杂自定义人群规则。
- 有可见电话则提供拨号与复制；有微信则复制微信号并提示用户在微信联系，不承诺直接跳转到某个人的微信聊天。
- 联系方式不可见或需要失物协查时，发起人选择教室、日期/时间段，填写物品描述；系统根据排定记录列出相关教师供勾选，可选择多位，但只发送给实际选中的教师，不自动群发。
- 生成 Inquiry（status=open）及每位教师的 InquiryRecipient。接收人在消息中心打开后可以回复 InquiryReply，result 为 not_seen / in_custody / other（未见到/已保管/其他），并填写说明；每人可以补充多条回复，历史追加保存，不覆盖。
- 首条回复让状态变为 replied，后续回复继续通知发起人；open 或 replied 均可由发起人结案为 closed，填写结案说明。closed 后不继续回复，首版不做重新开启；查看请求不自动标为 replied。
- 协查只做请求、回复、结案，不做聊天会话、在线状态、消息撤回；选定收件人保留快照，不因后续调课而默默换人。课程记录仍不代表实际到场。

## 8. 导入、更新与数据完整性

### 8.1 首版模板

先固定 Excel/CSV 模板及字段说明，覆盖学期节次、教师、教室、课程开设、任课、实际选课、排课、校历例外/占用。不同学校原表通过列映射转换，不先承诺自动识别任意截图或 PDF。OCR 可作为后续辅助，并必须经过人工预览确认。

### 8.2 流程

上传 → 解析与列映射 → 预览新增/修改/错误/需人工决定项 → 确认范围 → 事务内写入 → 返回批次结果 → 通知相关教师。

- 校内稳定编号是去重键；名称仅作展示，禁止用“同名教师/同名课程”合并数据。
- 默认增量导入；重复同一文件不新增重复课次。删除旧数据必须使用明确的“替换指定学期/课程范围”模式，并在确认页列出影响。
- 错误需要指明文件、工作表、行号、字段、原值和修正建议；首版整批有错不提交，避免“导入成功但少了几门课”。
- 优先使用原始系统稳定课次编号作为映射键。没有稳定编号时，第一次导入生成本地课次 ID 并导出映射表；后续调课更新必须带原课次引用，或在预览中由管理员明确配对。时间发生变化时系统不能凭名称与新日期猜出它是原课还是新增课，也不能把日期时间当作长期主键。
- 校历、选课、教师任课、全校课次、人员不可用及教室外部占用各自维护 DataCoverage 的日期/资源范围与来源更新时间；导入提交与覆盖范围更新同事务。普通教师不能通过客户端自行声称数据完整，冲突查询读取这些实体返回完整或待核验。
- 教务系统正式对接后实现同样的导入接口和领域对象；APP 无须改写页面。

## 9. 消息、提醒与离线

### 9.1 通知

APP 内消息是业务事实来源。调课生效、被退回、收到交接及确认、Inquiry 请求及 InquiryReply、导入导致个人课表变化均生成消息；点击进入对应对象。以“业务事件 ID + 接收人”去重，重复同步不重复弹消息。

课程开始提醒由手机对已缓存课次安排本地通知，默认提前 15 分钟，可关闭或修改。刷新课表、调课、取消课、退出账号后应撤销并重建相应提醒。系统通知权限未授予时继续正常显示 APP 消息，并在设置中提示启用入口。

v1 必须完成真实远程推送，默认接入个推官方 Flutter 插件、服务端 REST API、Android 国内厂商离线通道与 iOS APNs；开发期可用日志推进页面，正式验收不能以日志代替真机接收。正式课表变更、交接/确认、调课结果、协查请求/回复需要远推，教师关闭某类通知时尊重其设置，但站内消息仍落库。

业务事务创建 Notification 和首条 pending DeliveryAttempt；定时管理命令每分钟取待投递项，记录供应商受理 ID 或失败摘要。网络失败按 1/5/30 分钟有限重试，每次增加 attempt_no；已受理不重复发送，供应商受理状态 sent 不代表用户已读。到达重试上限标为失败并可在 Admin 重试；站内消息始终可见。回调只更新投递状态，不重复产生业务消息。

真机验收覆盖 Android/iOS 锁屏、APP 后台及系统允许推送的进程关闭场景，点击通知后读取当前对象详情。设备通知权限关闭、网络断开或系统强行停止限制必须显示实际状态，不能承诺这些条件下必达；正常配置下无法接收正式变更则 v1 不完成。

### 9.2 离线能力边界

- 缓存当前及近期教学周、课程详情、已发布教学记录、自己的草稿，页面显示最后同步时间。
- 断网可查看缓存并编辑自己的备注草稿；本地草稿随写随存，网络恢复后上传。
- 调课候选、申请提交、生效、交接发送/确认、完成学时确认需要联网，以服务端结果为准；界面直接说明当前需要联网，不假装已经成功。
- 草稿上传带 `client_mutation_id` 与 base_version；服务端在同一事务中检查 ClientMutation 唯一键和请求摘要、执行版本校验、写备注、保存响应。相同账号同 key 同内容重试返回原结果，不新增记录；同 key 不同内容返回 409。若另一设备已有新版本，保留两份内容供教师选择或合并，不能用最后写入直接覆盖。离线自动同步只更新 draft，不自动 published。
- 账户切换只显示当前账户缓存；退出登录清除当前会话及相关通知，未上传草稿先提供保存/放弃选择。

## 10. 关键验收场景

| 编号 | 操作与预期 |
| --- | --- |
| A01 | 正常教师完成账号激活、登录、重启 APP、改密、忘记密码与退出；每个流程有成功和失败提示 |
| A02 | 今日无课显示空状态；第 8 周的单双周课、跨月日期、节假日停课与调休补课展示正确 |
| A03 | 同一课程开设两名教师分周授课；教师首页只显示本人课次，课程详情可看到共同进度 |
| A04 | 两教学班有同一实际选课学生，目标时段冲突人数为 1；不能被列为正常推荐 |
| A05 | 行政班人数 30、该课程实际选课 18；容量 20 的教室通过容量检查；实际选课变化按目标日期生效 |
| A06 | 缺选课名单或未覆盖其他院系课表时返回待核验，并显示缺失项，不显示 0 冲突即推荐 |
| A07 | 前一课 09:40 结束、后一课 09:40 开始不算重叠；教师明确通勤缓冲另行判断 |
| A08 | 候选查询后另一个申请占用同教室；批准时重新校验，阻止无提示覆盖 |
| A09 | 调课申请待审批时显示原课；批准后只显示新课，备注 ID、学时与交接关联均保留 |
| A10 | 教师发布已完成的 10 月 19 日记录再补录已完成的 10 月 5 日记录；打开 10 月 26 日课次仍预览 10 月 19 日；打开 10 月 12 日课次只能选 10 月 5 日 |
| A11 | 两名教师各有记录，但同一个课次确认完成 2 学时后课程总进度只增加 2 学时 |
| A12 | 课程结束时间已过仍为待确认；取消课不累计；补课新增完成后才增加学时 |
| A13 | 专题学时分配超过本课次完成学时时给出明确提示；未分配学时仍进入课程总进度 |
| A14 | 交接已读但未点击确认时仍为待确认；确认后发送者可见确认时间；正文修订发补充 |
| A15 | 教室原课取消、调课改到新房间后，两间教室历史均按最终有效安排展示，并可查变更 |
| A16 | 电话私密时其他教师看不到号码；发起人填写物品描述勾选两位相关教师，双方分别回复未见到/已保管，发起人收到消息并结案；未勾选教师不收到请求 |
| A17 | 同一文件重复导入不重复课次；错误表预览指向具体行，修正后再提交；手工调整不被静默覆盖 |
| A18 | 断网编辑草稿、杀掉并重开 APP 内容仍在；恢复网络重试仅生成一份；跨设备版本冲突不丢字 |
| A19 | 调课成功后本地旧提醒撤销；新时间生成提醒；消息重复拉取不重复产生通知 |
| A20 | Android 和 iOS 真机分别完成登录、锁屏/后台/进程关闭条件下的真实远程通知、点击读取最新课次、课表浏览、备注输入及申请流程；不能只用模拟器截图或日志验收 |
| A21 | 某实际选课学生在候选窗口有 PersonBlock 考试安排、某教师有会议不可用；即便没有重叠课表也返回 conflict，学生冲突人数按人去重 |
| A22 | 候选仅返回 available/conflict/unverified 三种值；可推荐组排序严格为原教室、距原日期、距原开始时间、日期时间及教室稳定排序；已知冲突兼缺数据归 conflict |
| A23 | 同一 client_mutation_id 的备注网络重试返回同一结果与版本；同 key 换正文返回 409；自动重试不把草稿发布给其他老师 |
| A24 | 一门课有过去待确认 1 次、未来未完成 3 次、已取消 1 次，剩余课次为 4，过去待确认为 1；取消课不计入 |
| A25 | 教师保存草稿后共同授课人不可见，显式发布后可见；再次编辑草稿仍看到上一发布版本；未来或未 completed 课次的发布内容不进入“上次记录” |
| A26 | 数据表为空且没有 DataCoverage complete 时返回 unverified；教务明确确认“该范围无额外人员占用”后该类检查可完整，未覆盖日期仍不可推荐 |
| A27 | 推送服务失败可看到投递失败及重试序号，重试不重复生成站内消息；供应商已受理但教师未打开时，消息仍显示未读 |

## 11. 优先级与实施建议

先做一条可演示的纵向链路：导入一学期样例数据 → 教师登录 → 今日课表 → 写并发布备注 → 确认 2 学时 → 下一任课教师收到交接。随后接入调课生效、教室协查与完整计划管理，完成离线、真实远程推送和发布。M0 即验证 Android/iOS 推送最小通道并列明试点机型与开通条件；消息/投递实体及事件接口在早期建立，M4 完成目标机型的完整通道、锁屏/后台/系统允许进程关闭场景验收。各方向可以并行，但必须共享课次和课程开设模型。

最值得优先确认的业务参数是学校节次时间表、一个学时口径、课表与选课数据来源、学校实际审批办法、真实部署环境和目标手机。其他界面与技术细节按默认方案推进，无须等所有学校集成都齐备才开始开发。

首版最有收益的优化是“大字今日课表 + 一键记教学备注 + 自动带出上次计划 + 清晰显示数据是否完整”。不用生成式 AI 包装确定性冲突计算，也不用在初期扩展成全校排课系统。
