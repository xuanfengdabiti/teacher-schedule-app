# 演示活动数据补充

`scripts/seed_demo_activity.py` 在既有的四个演示种子（`seed_demo.py`、`seed_demo_extra.py`、`seed_demo_team.py`、`seed_demo_team_extra.py`）之后执行，用于填充“教学计划与进度”、历史完课以及少量教师活动页面，避免演示时大量 0 或空白页。

- 数据范围：仅 `DEMO-SCHOOL` / `2026-autumn` 的虚构数据。
- 固定演示日期：**2026-10-28**（脚本内常量 `DEMO_DATE`，不从系统时钟取日期）。
- 不新增课程，不修改既有课次的日期、时间、教室或任课关系。
- 写入全部通过现有业务服务（`save_plan`、`record_completion`、`save_draft`/`publish`、`create_handoffs`、`create_request`、`create_inquiry`/`reply_to_inquiry`），不直接写业务表，也不伪造 `Notification`、`ChangeLog` 或 `SessionProgress`。

## 执行方式

脚本在模块末尾直接调用 `main()`（`manage.py shell` 的 `__name__` 不是 `__main__`，不能用 `if __name__ == "__main__"` 判断）：

```powershell
docker cp scripts/seed_demo_activity.py teacher-schedule-demo-api-1:/tmp/seed_demo_activity.py
docker exec teacher-schedule-demo-api-1 sh -c "python manage.py shell < /tmp/seed_demo_activity.py"
```

`scripts/start-demo.ps1` 已把该脚本放在四个既有种子之后复制并执行；重复启动同样安全。

## 填充内容

### 教学计划

- 仅对**没有计划**的开课创建 4 个切题专题，标题取自课程名（如“刑事证据审查实务：典型案例分析”），不统一套用“第一章”。
- 4 个专题的计划学时合计等于该开课的 `target_hours`，并各带一个学期内阶段截止日期（按学期起止四等分）。
- 已有计划、已有专题（包括演示者编辑过的标题/学时/日期）完整保留，不覆盖。

### 历史完课与学时

- 只处理 `end_at < 2026-10-28 00:00 (Asia/Shanghai)` 且非取消的课次；今天与未来一律不登记。
- 每门开课按 `start_at, id` 稳定排序后取**固定前缀**约 75%（至少留 1–2 个最近的已结束课次仍待确认）。二次执行时该前缀已全部完成，新增为 0。
- 已 `completed` 或有 `actual_hours` 的课次直接跳过，不改历史记录。
- 实际学时等于该课次 `planned_hours`；学时按专题顺序填充，上完一个专题再进入下一个。
- 每门开课最多**一条**新增完课留 1.0 学时未归入专题（供“未归入专题”展示合理小额）；若该开课已有未分配学时，则不再另留。
- 未来专题保持 0 是合理的，不为了“数字都非 0”捏造进度。

### 教师活动样例

面向工号 `0001 / 0011 / 0012 / 0013`（教师甲、李、张、王）：

| 类型 | 内容 |
| --- | --- |
| 教学备注 | 每人最多补到 2 条短备注（1–2 句）；缺共享备注的优先补一条已共享短备注；已有内容不覆盖、不追加垃圾记录 |
| 进度交接 | 按 4 人循环各发 1 条、各收 1 条，收件人尽量不重复；使用双方当前共享任课的开课；保留待确认以便演示“确认/撤回” |
| 调课申请 | 只提交申请（`submitted`），不批准、不改正式课表；目标时间严格从 2026-10-29 之后候选；已有待审批/已批准的账号跳过 |
| 失物询问 | 按 4 人循环各发 1 条、各收 1 条，询问**收件人**过去课次的教室与时段以保证其为候选；约一半保留待回复，一半通过 `reply_to_inquiry` 展示已回复，均不自动结案 |

备注使用稳定的 `client_mutation_id` 和作者级脚本标记；交接、申请与询问按已有记录或稳定标记（`【演示活动】…`）跳过，重复执行不重复创建。

## 幂等与数据保留

- 第二次执行时各类“新增”应为 0，学时与既有业务记录不变。
- 只补缺失项，不覆盖演示者已编辑的计划、备注、交接、申请或询问。
- 调课候选评估中 `RescheduleRequestError` 表示该候选不可用，脚本会尝试下一个候选；都不行则如实计为跳过，不会写入申请。
- 其它服务抛出的业务错误向上抛出（脚本以 `SEED_ACTIVITY_FAILED: ...` 退出），不吞异常冒充成功。
- 输出每类的 `新增 / 跳过 / 检查` 计数，便于核对。

## 与 AI 备课的边界

本脚本**不生成任何 AI 备课内容**，也不写入 `LessonPreparation`。AI 备课由教师在 App 内真实调用模型后自行保存。
