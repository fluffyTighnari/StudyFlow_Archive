# 觅学之契 · 我的学习范式 — M7 Session（v0.4）

> 定位：**持久对话式学习工作台**。一个 Session = 围绕一个主题目标的可持续对话上下文，包含任务规划、自由聊天、计划调整三种能力。
> v0.4 为统一版：合并 v0.3（持久工作台 + 暂存区 + 三种沉淀触发）与 M8 引入的修订（输入层可手动修改、动机持久化状态）。
> **修订（对齐 M0 v0.1 §11）**：A1 新增 `state_snapshot`（§2 结构 + §4.1）；A2 `task_plan.checkpoints[]` 字段对齐 M2-A4（补 check_method / pass_criteria / fail_action / result）。

---

## 1. 定位与能力

| 能力 | 说明 | 产物 |
|---|---|---|
| 任务规划 | Agent 按输入层 + 全局画像生成可执行计划 | `task_plan`（持久化对象） |
| 自由聊天 | 随时交流学习方法、困惑、心得 | `chat_log` |
| 计划调整 | 按实际进展/状态修改当前计划 | `plan_revisions`（版本化） |

- Folder-Session 结构：folder=领域（英语学习），session=主题（背单词、学翻译）。
- Session 生命周期可长：长期计划 = 长生命周期 Session，内部反复调整/聊天/里程碑，全部 Session 内生效。

---

## 2. Session 内部结构

```
Folder（folder_id / folder_name / folder_goal / C证据检索维度）
  └── Session（session_id / folder_id）
        ├── input（任务定义区：可手动修改，input_revisions 留痕）
        ├── motivation（动机持久状态：可随时更新）
        ├── task_plan（持久化计划对象）
        ├── chat_log（对话记录；每条消息可带 tool_trace，见 §2.1）
        ├── notes（会话笔记）
        ├── state_snapshot（动态状态：精力/压力/环境——Session 级，不进 M1）
        ├── stage（暂存区：session 内即时生效，结束即弃或沉淀）
        ├── milestones[]（里程碑检查点）
        ├── plan_revisions[]（计划修改历史）
        └── summary（完成总结 → 触发沉淀）
```

---

### 2.1 工具调用轨迹（`chat_message.tool_trace`，P6 新增）

一次讨论里模型调了哪些工具、参数是什么、结果是被接受还是被拒绝 —— 这些必须落在**那条 assistant 消息**上，否则"它为什么这么说"就无从复核。

**为什么是一个可空 JSON 列，而不是新建表**：轨迹没有独立生命周期（它随消息一起产生、一起读、一起删），却会带来外键、级联与查询复杂度；表数保持在 29。代价是**不能在库层对轨迹做聚合查询**，而目前没有任何需求要那样查。

一条轨迹的样子（`ToolTraceEntry`）：`step`（第几轮）、`call_id`、`tool`、`scope`（只读/会话写入/全局提交）、`arguments`、`status`（`success|empty|rejected|error`）、`message`、`duration_ms`。

三条边界：

- **只有 `role=assistant` 的消息能带轨迹**，库层强制（`add_chat_message` 对非 assistant 带轨迹直接抛 `InvalidField`）。用户的消息没有"我调了什么工具"这回事。
- **轨迹里不存模型的思考内容**。`LLMReply.thinking` 与 `content` 分开存是有意的：并进去就等于把模型的推理当成它的回答，而本项目从头到尾在区分"理由"与"事实"。轨迹记录的是**动作**，不是动机。
- **轨迹不参与任何判定**。没有任何代码读它来决定行为 —— 它是给人看的（界面展示 + 事后复核）。一旦它参与判定，它就从"记录"变成了"依据"，而依据需要校验。

---

## 3. 任务定义区（input，可手动修改）

```json
{
  "goal": "学什么",
  "why": "为什么学（动机陈述，由 motivation 支撑）",
  "starting_point": "当前基础",
  "task_type": "理解概念|掌握技能|完成项目|解决问题",
  "constraints": "时间/资源/限制",
  "success_criteria": "什么结果才算完成",
  "priority": 0-10,
  "intent_raw": "用户原始话",
  "intent_confidence": "stated|inferred|missing",
  "input_revisions": [ { "rev_id":"","at":"","by":"user|agent","field":"","old":"","new":"","reason":"" } ]
}
```

- **手动修改**：侧页任务定义区直接改字段 → 保存即生效 → 写 input_revisions。
- **防漂移**：goal 大幅变更时提示「是否新建 Session」（提示而非硬锁）。
- 输入层由「用户 prompt 分析 LLM」解析：只提取不编造；constraints 只接受 stated；推断字段回显确认。

---

## 4. 动机持久状态（motivation）

```json
{
  "type": "intrinsic|extrinsic|mixed",
  "strength": "high|medium|low",
  "drivers": ["引原文的动机表述"],
  "obstacles": [ { "type":"启动阻力|前置知识缺口|时间约束|情绪状态", "desc":"", "likelihood":"high|medium|low", "evidence":"" } ],
  "updated_at": "",
  "motivation_revisions": [ { "rev_id":"","at":"","by":"user|agent","change":"","reason":"","basis":"stated|experienced|inferred" } ]
}
```

- **随时更新**：主 Agent 在聊天中捕获动机变化（为什么学/新障碍/强度变化）→ 主动 `update_motivation`；侧页也可手动改。
- 调整任务时可随时查阅动机，作为生成/调整计划的上下文。
- 区分「动机性陈述」（更新）与「一次性吐槽」（只写 chat_note）。

### 4.1 动态状态快照（state_snapshot）

```json
{
  "energy": "high|medium|low",
  "stress": "high|medium|low",
  "environment": "安静|有人活动|易受通知干扰|其他",
  "note": "",
  "updated_at": ""
}
```

- **定位**：动态状态（当次精力/压力/环境）**永不进入 M1**（M1 红线 §5.5），只在当前 Session 有效。
- **写入方式**：**侧页手动更新为主**（可覆盖，不写 revision）；Agent **只读**，不通过 `update_motivation` 改写——`update_motivation` 管的是「为什么学/障碍/强度」，与当次状态是两回事（M8 §4.2 边界）。
- **Agent 的间接更新路径**：聊天中察觉状态变化时，Agent 在回复中建议用户在侧页一键更新（保持 14 工具不变）。若实测发现频繁需要，再考虑新增工具。
- **用途**：生成/调整计划时作为推翻软偏好 SP1 的依据之一（如「今日精力低 → 缩短 timebox」）。

> **★ P4 落地口径**
>
> - **`environment` 是自由文本，不是枚举**。上表给的取值集合以「其他」结尾 ——
>   一个以"其他"结尾的集合意味着它是**开放**的。把它钉成 `Enum` 只会在第一次遇到
>   「咖啡厅」「地铁上」时被迫改 schema，而改 schema 的代价远大于收益。
>   枚举只在**取值真的封闭**时才用（`energy` / `stress` 是封闭的：high|medium|low）。
> - **无 revision，借 `session.version`**：快照是"当时的状态"，不是一条会参与计划的声明，
>   因此覆盖式写入、不追加历史。但它的写入仍需过乐观锁（否则两个窗口同时更新会互相覆盖），
>   用的是 `session.version`（它没有自己的头表）。
> - **"永不进 M1"有两层保证**：一层是没有任何代码路径从 `state_snapshot` 写
>   `m1_baseline_version`；另一层是 `StateSnapshot` 的字段名与全部 M1 `variable_id`
>   不相交（防止"把 `energy` 当成一个 M1 变量"这种偷渡）。
>   两层都有测试：写快照前后全局域行数完全不变，且字段名交集为空。

---

## 5. 持久化计划对象（task_plan）

```json
{
  "plan_id": "",
  "goal_derived": "",
  "steps": [ { "step_id":"","action":"","output":"","method_ref":"","status":"pending|doing|done|skipped" } ],
  "checkpoints": [ { "cp_id":"","at_step":"","check_method":"","pass_criteria":"","fail_action":"",
                     "result":"pending|pass|fail","result_at":"","result_note":"" } ],
  "current_version": 0,
  "revisions": [ { "rev_id":"","at":"","by":"agent|user","change":"","reason":"","basis":"" } ]
}
```

- **持久化**：不随对话上下文丢失；任何时刻工具调用可查。
- 关键步骤必须带 `output`（M2-A3/HC3）；记忆类检查点验证「真实留存」（M2-A4）。
- **调整被步骤锚定**：无「已产出步骤 output」时不主动改计划；调整必须带 reason；无理由调整 Agent 提示「先跑一步再改」。

> **★ P4 落地口径：结构按版本存，进度就地改**
>
> 上面的 JSON 把结构（`steps`/`checkpoints`）与进度（`status`/`result`）放在一起，
> 落库时它们**分开处理**：
>
> | | 存储方式 | 改它会发生什么 |
> |---|---|---|
> | 结构（`action`/`output`/`method_ref`/`check_method`/`pass_criteria`/`fail_action`） | 每个 `version_no` 各存一份，旧版本**原地冻结** | 产生新 `plan_revision`，`reason` 必填（库层 CHECK 非空） |
> | 进度（`step.status`、`checkpoint.result`） | 当前版本内**就地更新** | **不产生新版本** |
>
> 理由双向：**改计划必须留痕**（本节第 3 条与 §9 红线 3），而**标记一步做完不该被记成
> 一次计划修订** —— 否则 `plan_revision` 立刻失去意义，每次勾选都会产生一条"计划变了"的假消息。
>
> **`step_id` 跨版本稳定，进度随之延续**：改计划时按同名 `step_id` 继承状态，
> 所以「第 3 步已经做完」不会因为改了一句措辞而回到 `pending`。
> 回看旧版本用 `GET /api/sessions/{id}/plan?version_no=n`。
>
> **检查点的两个实现细节**：
> ① `plan_checkpoint` 必须指向**同一版本内**的步骤（跨版本锚定会让"这一步当时的验收标准是什么"无法回答）；
> ② 记检查点失败（`result=fail`）**不改动计划本身** —— 「没通过」是一个事实，
> 而"要不要因此改计划"是另一个决定，由用户或（P6 的）Agent 显式做出。
>
> **★ 检查点的 `result` 不跨版本继承（P6 发现的缺口，待裁定）**
>
> 改计划时**步骤的 `status` 会按 `step_id` 继承，但检查点的 `result` 会归零成 `pending`**。
> 这不是笔误，是当前实现的真实行为，而它有两条可见后果：
>
> - 用户界面上："我记的'没过'不见了"。那一行还在（方法、标准、失败预案都在），变的是结果。
> - 更要紧的一条：P6 的 `update_task_plan` 靠 `result is FAIL` 找"计划该被改"的证据，
>   所以**一次计划改动就把那条证据抹掉了** —— 模型没法基于同一个失败再改第二次，
>   除非用户重新记一次结果。
>
> 该保留（并区分"标准没变所以结果仍然算数"与"标准变了所以结果作废"）还是该重置，
> 与「省略 = 保留」（M8 §4.4.1）属于同一族问题。**这里不擅自改 P4 的计划版本语义**：
> 它是单独一次裁定的事。测试 `test_a_step_only_change_does_not_delete_the_checkpoints`
> 钉住了当前行为，改它的人会看到这段说明。
>
> **检查点 ≠ 里程碑**（M8 §4.5 已定，此处重申，因为两者在数据上很像）：
> 检查点是**计划内的验收动作**（`at_step` 锚在步骤上，通过/不通过）；
> 里程碑是**跨会话的达成标记**（无步骤锚，达成即可能触发 R4b 沉淀）。
> 前者回答"这一步做对了没有"，后者回答"我是不是真的会了"。

---

## 6. Agentic RAG（全局只读契约）

| 数据 | 用途 |
|---|---|
| M1 认知基线（v0.4） | 生成计划时选择匹配的呈现/组织方式 |
| M2 偏好 + 策略库（v0.3） | 选方法、判断硬约束/软偏好 |
| C 个性化证据 | 同类型任务过去什么方法有效（按 folder 过滤） |
| 同 Folder 历史 Session | 最近同类任务做法与结果 |

**只读红线**：Agent 永不直接写全局；认为画像需更新时，唯一动作是产出 UpdateIntent → 路由（v0.4）。

> **★ P6 补记：上下文包里原来没有"检查点"**
>
> §6 那张表描述的是 Agent 该看到什么，而 P6 实施时发现
> `assemble_discussion_context` 产出的包里**没有检查点** ——
> 于是用户点开「每天过 50 个新词」说「我记不住」时，模型看不到这一步的
> 通过标准与失败预案（那里面本来就写着"回到上一批词重刷三天"）。
>
> 用户裁定：**加进包**，排在用户偏好之后、stage 之前。
> 理由是**验收标准是这一步的硬要求，而 stage 里是还没定的想法**。
> 相关的一节因此从九节变成十节，采用序 `1→5`（原来 `1→4`）：
> 用户偏好 > 检查点 > stage > 基线/证据 > 策略库。
> 包里的每一条检查点都带 `reason`，区分两种不同的相关：
> 「本步骤的验收标准」与「判为未通过（改计划的证据）」。

---

## 7. 暂存区（stage）与写入边界

### 7.1 stage（Session 内即时生效）· **P4 已落地**

```json
{ "stage_id": "", "items": [ { "type":"temp_pref|idea|plan_override", "content":"", "effective_in":"本 Session", "created_at":"" } ] }
```
- 作用域 = 当前 Session：Agent 生成/调整计划时读取，作为「当前想试试怎么学」。
- **不进全局**：stage 不经过路由；Session 完成时归档/丢弃。

> **★ P4 落地口径（用户裁定：保留行 + 标状态，永不删除）**
>
> 原文「归档/丢弃」的"丢弃"落成了 `status`，而不是 `DELETE`。
> 归宿取值四个：`active` / `discarded` / `deposited` / `archived`，且**单向**
> （定过归宿就不再改动）。理由不是"怕丢数据"，而是：
> **"我当时想试试这个"本身就是关于我的信息** —— 一个试过没用的方法，
> 和从没被想到过的方法，对"我擅长什么学习方法"的答案贡献完全不同。
> 删掉它，下次规划会**重新推荐同一个已经被否掉的方法**，而系统完全不知道自己犯过这个错。
>
> 四种归宿的判据（`app/session_domain/enums.py::StageItemStatus`）：
>
> | 归宿 | 何时 |
> |---|---|
> | `active` | 还没定；**勾选时被排除的条目留在这一档** —— 它还在等一个真正的处理 |
> | `discarded` | 用户明确不要了 |
> | `deposited` | 已沉淀进全局，`result_ref` 指回提案 id |
> | `archived` | 会话结束时仍未决，被动收尾 |
>
> **stage 的版本**：stage 没有独立的头表，因此它的乐观锁由 `session.version` 承担
> （M0 §6.2 三个版本域的落地口径）。写 stage 会让 `session.version` +1。
>
> **stage 与讨论上下文的关系（P4 新增，容易误解）**：讨论上下文包把
> **全部 active 的 stage 条目**放进第 2 档 —— **含 `plan_override`**。
> 因为讨论一个计划项时，"我想把第 2 步拆成两步"正是最相关的东西。
> 但在**沉淀**时，`plan_override` 与 `idea` 被明确排除（它们只关于"这次任务怎么做"）。
> **同一条目在两条路上命运不同，这是设计而不是不一致**：
> 一条路是"给模型看什么"，另一条路是"写进关于我的结论"。

### 7.2 三种沉淀触发（写入全局的唯一通道，走路由 v0.4）· **P4 已落地**

| 触发 | 时机 | 处理 |
|---|---|---|
| 完成沉淀（R4a） | Session 完成总结 | 写偏好与参数均需用户确认（**系统自动产出，用户还没看过**） |
| 里程碑沉淀（R4b） | 长 Session 检查点完成 | 同 R4a |
| 手动沉淀（R4c） | 用户一键「沉淀本次发现」（从 stage 勾选） | **两种 action 都直接生效** —— 勾选就是确认动作 |

> **★ 修订（P5.2，用户裁定）**：原 R4c 行写「加偏好直接生效；更新参数需确认」，
> 它与本节第 9 条红线「沉淀必确认」直接冲突，且**开例外处没有给出任何理由**。
> 唯一的候选理由（§5 的 R4 证据强度表：R4a 强 / R4b 中 / R4c **弱**）指向**相反**方向，
> 而且那张表自己声明「证据强度不改变路由动作」。
>
> 用户裁定以「**用户在那个手势里是否已经自己给出或确认了这个值**」为唯一判据
> （M0 §11-A21）：R4c 的勾选与 R2 的「确认写入基线」按钮同理，因此
> **`add_preference` 与 `update_parameter` 都直接生效**。
>
> **判据的边界**：确认动作只覆盖**当时摆在用户面前的东西**。R4c 的列表是 stage 里的
> 本次发现（`experienced`），因此勾选**不覆盖** `inferred` —— LLM 的推断不在 stage 里，
> 用户没看见它，`R4c + inferred` 仍为「需确认」。
> 另：trait 变量仍有 §7.3 的「≥2 条独立 Session 证据」硬前置条件（那是 blocker，
> 与判定正交），因此**「直接生效」不等于「一定会写进去」**。

> **★ P4 落地口径（三件原文没写、不写就会各写一份的事）**
>
> 1. **`basis` 从哪来**：stage 候选写死 `experienced`（用户亲手写下的尝试），
>    里程碑候选写死 `experienced`（做到过的事），总结候选**缺省 `inferred`**。
>    缺省取"更弱"的那个是刻意的：`inferred` 永远需要确认，所以"少声明一点"
>    是安全方向；反过来标成 `experienced` 会让 R4c 自动生效 ——
>    而总结根本不是用户选的（同一个道理，见 §9 红线 6 的边界）。
> 2. **什么能沉淀**：只有 `temp_pref` 可以。`idea` 与 `plan_override` **明确排除**
>    并给出理由，理由要能在界面上显示出来。
> 3. **排除必须可核查**：`POST /sediment` 的响应里 `excluded[]` 带 `source_ref` 与 `reason`，
>    与 `included[]` 同样是一等公民。用户的抱怨不应该是"勾了 5 条只写了 3 条，
>    另外 2 条去哪了" —— 界面必须能直接回答。
>
> 另外，沉淀候选的组装（`sedimentation.py`）**不认识 Router**：
> 它只产出 `SedimentationCandidate`（target/action/value/basis），
> 由 `app/api/sedimentation.py` 映射成 `UpdateIntent` 再提交。
> 这条边界由 **ARCH-11** 强制 —— 「沙箱域顺手写全局」的能力在依赖图上不存在（M0 §4.3-A22）。

### 7.3 完成会话：一个复合动作的时序（P4 新增）

`POST /api/sessions/{id}/complete` 是唯一同时做「沙箱写 + 全局提交」的动作，
它按固定次序做四件事：

```
① 校验 session.version（**第一句，在任何写入之前**）
② 写 session_summary
③ 跑 R4a 沉淀（可能产出 pending_confirmation 提案）
④ 归档仍未决的 stage 条目 → 转移 review → completed
```

**①必须最前**，这是 P4 期发现的一个缺口的落地（M0 §11-A23）：
若先写总结再校验，一次过期提交会留下「总结写了、归档没做、状态还是 review」的半成品，
而客户端只拿到一句"版本冲突"，它不知道该不该重试。
有测试专测这一点：过期版本提交后断言 `summary is None` 且状态仍是 `review`。

---

## 8. 状态机 · **P4 已落地为强制拒绝**

```
draft ──激活──▶ active ──（执行+聊天+调整+里程碑*）──▶ review ──▶ completed
                              │                              │
                              ▼                              ▼
                     （里程碑完成可触发 R4b）          （完成沉淀 R4a：统一总结点）
                          abandoned（部分条目可沉淀，其余丢弃）
```

**"强制拒绝"落在哪**（用户裁定：后端强制拒绝，`note` 留唯一例外口子）：

- 写权限表是**数据**不是 if-else 链：`app/session_domain/guards.py::WRITE_GUARDS`
  （15 个操作 × 允许状态 + 理由），测试断言 `unguarded_operations() == []` ——
  **新增一个操作就必须表态**，否则测试变红。
- 转移边 8 条，同样是数据（`TRANSITIONS`），非法边返回 409 并附 `allowed_targets`，
  让界面能直接告诉用户"现在能做的是这几件事"，而不是只报一个错误码。
- **终态不可写**：`completed` / `abandoned` 下 11 个内容操作被拒。
- **例外口子只有 `note`**：终态下仍可写笔记。理由：会话结束后"三个月后回看，
  发现当时的问题其实是另一个"这件事必须有个地方写；若连笔记都拒，
  用户唯一的选择是新建会话，而那条信息就从此和原会话脱钩了。
  换言之，拒绝写入是为了不篡改**已发生的事实**，而笔记是**新的事实**，不是篡改。
- **重新打开是两步**（`completed → review → active`）：一步回去会让"我已完成"
  这个事实消失得无声无息。
- `abandoned` 是终态，但仍允许 `settle_stage_item` —— 放弃一个会话时，
  用户最可能想做的事恰恰是"把这次没试成的记下来"。

---

## 9. Session Agent 行为契约（红线）

1. 可读全局，不可写全局；写全局唯一路径 = 沉淀 → UpdateIntent → 路由。
2. 计划可改，目标可改但留痕：task_plan 随时改（plan_revision）；input 手动改（input_revision）；goal 大变提示新建 Session。
3. 调整被步骤锚定：无产出不主动改；调整必须写 reason。
4. 聊天不自动进全局：可沉淀内容 → Agent 建议 → 用户确认。
5. stage 是 Session 内即时生效的唯一写入区；stage → 全局 = 沉淀 = 走路由。
6. **沉淀必确认 —— 但「确认」指的是「用户在那个手势里是否已经自己给出或确认了这个值」**（M0 §11-A21）：R4a/R4b 是**系统自动产出、用户还没看过** → 写偏好与参数都需确认；R4c 是**用户从 stage 勾选后点按钮** → 勾选就是确认动作，两种 action 都直接生效。**边界**：确认只覆盖当时摆在他面前的东西，因此 R4c 的勾选不覆盖不在 stage 里的 `inferred`。

---

## 10. 与各文档衔接

| 文档 | 衔接 |
|---|---|
| 更新路由层 v0.4 | stage 非全局；R4 三种沉淀触发 |
| M1 v0.4 | RAG 读取 M1 偏好类/trait 参数；基准测验 P1 分批 |
| M2 v0.3 | 计划生成执行 A 协议；硬/软约束 |
| M8 v0.2 | Agent 工具层：14 工具覆盖本模块全部能力 |
| Benchmark v0.2 | 测绘结果经 R2 写入 M1；Session 观测作为增量证据 |

**P4 新增的衔接（代码层）**：

| 落点 | 文件 | 说明 |
|---|---|---|
| 沙箱数据内核 | `app/session_domain/` | 16 表 / 10 文件；**不认识 Router**（ARCH-11） |
| 侧页单一状态源 | `GET /api/sessions/{id}/state` | 一次给全：状态机 + 三个版本域 + 全部区域 |
| 写入边界 | `app/api/sedimentation.py` | 沙箱 → 全局的**唯一**接口（`sediment` / `complete`） |
| 讨论上下文 | `GET .../plan/steps/{id}/context` | 采用序由确定性代码决定（M0 §7.4-A1）；**P6 起含检查点一节**（见 §6 下方） |
| 用户操作面 | `app/api/sessions.py` | 会话内的全部读写；**不碰 Router** |
| 讨论入口（P6） | `app/api/discuss.py` | `POST /api/sessions/{id}/discuss`；**沙箱写入端点，不依赖 Router**（唯一的全局通路是 `app.agent.handoff`） |
| Agent 运行时（P6） | `app/agent/`（6 文件） | `llm` / `tools` / `dispatch` / `handoff` / `loop`；**只有 `handoff.py` 可以 import `app.router`**（ARCH-12） |
| 工具轨迹（P6） | `chat_message.tool_trace` | 见 §2.1；只给人看，不参与判定 |

---

## 11. 待细化

1. Agentic RAG 检索实现（只读 API）—— P4 只做了 `plan_step` 级的讨论上下文
   （`assemble_discussion_context`），**会话级检索**（跨会话找"我以前怎么处理这类任务"）未做。
2. task_plan / 沉淀 UI —— P4 只提供后端契约；**界面侧有一条硬性义务**：
   `POST /sediment?dry_run=true` 返回的 `included`/`excluded` 必须**逐条列给用户看**，
   且 `inferred` 条目要在视觉上与用户亲手写的区分、默认不被批量勾选。
   否则"确认"会退化成连点，而 A21 的整个判据就落在这一点上。
3. chat_log 检索与完成总结提炼 —— 目前 `session_summary.findings` 是**原样保存**，
   不在沙箱内做任何加工；把聊天记录蒸馏成 findings 是 P6 的 Agent 职责。
   **P6 之后仍然没做**：`/discuss` 已经把聊天写进 `chat_log`（带 `tool_trace`），
   但没有任何代码去读它做蒸馏 —— `retrieve_session(scope="chat_log")` 只是把原话取回来。

**P6 期新增的待细化**：

7. **`PlanStepStatus` 没有 `blocked`**：只有 `pending|doing|done|skipped`。
   所以「这一步卡住了」目前**根本没有承载它的字段**，而它看起来那么自然 ——
   在 P6 的实现里，这一条被明确写成"不能用的判据"并注明了原因
   （写进去会让那个分支永远不成立，而代码看上去像是支持的）。
   要不要加 `blocked`，以及它是否算"计划该被改"的证据，需要一次单独的裁定。
8. **讨论上下文包只有九节加一**（P6 加了检查点），但它仍然**不含 `milestones`**
   —— 讨论某一步时看不到"更大的达成标记在哪"。检查点是"这一步做对了没有"，
   里程碑是"我是不是真的会了"，后者对讨论同样相关。同一族问题。

**P4 期新增的待细化**：

4. **`folder` 的语义尚未落地**：P4 只建了 `folder` 表与两个端点
   （列表 / 新建），"一个长期领域内多个会话如何共享认知"没有实现
   —— 例如「英语学习」这个 folder 下的多个会话，是否应该共享一套 stage/计划模板。
   当前裁定是**不共享**（每个会话各自独立），但这只是"先不做"，不是"想清楚了不做"。
5. **会话列表的排序与分页**：`GET /api/sessions` 现在按创建时间倒序返回全部，
   没有分页。会话变多之后这是必然要改的。
6. **`A20`（通用偏好无身份槽位）与本模块的关系**：两条互斥的 `stated` 偏好
   （"我讨厌网课" / "我能接受精品网课"）目前都是硬约束，会在同一份上下文包里
   同时以"硬"的身份出现 —— 讨论上下文只能照实呈现，**无法替用户裁决**。
   这不是本模块能解的问题（解在 Router 的偏好身份标识），但界面应当能提示这种并存。

---

*文档版本：v0.4 ｜ 状态：draft ｜ 归属模块：M7 Session（觅学之契系列）｜ 取代 M7 v0.3 ｜ 依赖：M1 v0.4、M2 v0.3、更新路由层 v0.4、M8 v0.2 ｜ 修订：v0.4 就地修正（对齐 M0 v0.1 §11，A1 / A2）；**§4.1 / §5 / §7 / §8 / §10 / §11 含 P4 实现期回写（沙箱已落地）；§2.1 / §6 / §10 / §11 含 P6 实现期回写（Agent 运行时 + 讨论计划项闭环）***
