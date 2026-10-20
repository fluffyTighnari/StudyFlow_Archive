"""端到端功能验证 —— 把「用户实际会走的那条路」整条跑一遍。

这不是单元测试的替代品，而是它的**反面**：单测逐点验证函数，
这个脚本验证**这些点连起来到底能不能用**。两者的失败方式不同 ——
单测全绿而产品不可用是常见状态。

## 用法

```powershell
# 先起一个服务（假模型最省事，且结果确定）
.\\venv\\Scripts\\python.exe -m tools.seedui --db var/e2e.db --reset
.\\venv\\Scripts\\python.exe -m tools.serveui --db var/e2e.db --port 8140 --llm fake

# 另一个终端
.\\venv\\Scripts\\python.exe -m tools.e2echeck --base-url http://127.0.0.1:8140
```

## ★ 必须对着**刚 `--reset` 过的库**跑

这个脚本**不是幂等的**，而且不该是：它模拟的是一个真实用户从零开始的旅程
（新建会话 → 写笔记 → 完成 → 沉淀）。第二次对着同一个库跑时，
会话已经是 `completed`，于是"写笔记""传资料"会返回 409 `state_not_writable`
—— 那是**设计如此**的复盘闸门，不是 bug。

用旧库跑会看到 4~5 个假失败，它们掩盖掉真失败。
所以要重跑就先 `--reset` 一个新库、重启服务。

（另一个信号：如果失败数突然从 1 涨到 5、而失败项都是会话写操作，
那几乎一定是库脏了，不是代码坏了。）

## 它报三种东西

- ✅ **可用**：真实走通，附证据（版本号、条数、id）
- ⚠️ **缺口**：这一步需要的东西**不存在**（404／没这个概念）。
  缺口**不算失败** —— 它是功能清单的一部分，退出码不受它影响。
  把它和失败混在一起，会让"没做"和"做坏了"看起来一样。
- ❌ **失败**：本该能用，但报错或断言不成立。退出码非零。

**为什么缺口要单独一类**：用户要的是"功能性地完整验证"。
如果把 404 也报成失败，那么随着功能补齐，失败数会从 20 降到 0，
看起来像"修了 20 个 bug"，其实是"做了 20 个功能" —— 两件事不能混。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

OK, GAP, FAIL = "ok", "gap", "fail"
_MARK = {OK: "✅", GAP: "⚠️ ", FAIL: "❌"}

#: 只有**拿到一个具体会话**才有意义的工具（11 个）。
#:
#: ★ 这份名单是**手写的**，而项目里别处的同类名单都是从数据推的
#: （见 `ToolScope.SIDEBAR_TOOL_SCOPES`）。这里手写是有意的：
#: 验证器如果直接 import 被测代码的那份集合，它就只能验出
#: "集合等于它自己"—— 一个恒真的检查。
#: 手写一份独立来源的名单，才让"侧边栏的工具清单里混进了会话工具"真的可被发现。
#:
#: ★ 手写的代价是它会腐烂，所以有一条单测钉住它：
#: `tests/agent/test_sidebar_scope.py::test_the_e2echeck_session_tool_list_is_exact`
#: 断言这份名单与 `ToolScope.SESSION_READ | SESSION_WRITE` **逐字相同**。
#: 于是它有两个来源之间的一致性，而 e2echeck 自己那份仍然不是从被测代码拷的。
#:
#: （第一版这里写错了 6 个名字 —— 有编造的、有漏掉真的。一条会腐烂的
#: 手写名单比没有更糟：真工具泄漏时它照样通过。那条单测就是为此加的。）
_SESSION_ONLY_TOOLS = frozenset(
    {
        "retrieve_session",
        "read_attachment",
        "ask_about_task",
        "create_task_plan",
        "update_task_definition",
        "update_task_plan",
        "propose_plan_change",
        "mark_step_done",
        "checkpoint_milestone",
        "update_motivation",
        "write_chat_note",
        "write_stage",
    }
)


@dataclass
class Result:
    name: str
    status: str
    note: str = ""


@dataclass
class Journey:
    base: str
    session: str
    results: list[Result] = field(default_factory=list)
    #: 跨步骤传递的观察值（计划版本、router_log 条数…）
    memo: dict = field(default_factory=dict)
    #: 题库目录（`--items-dir`）：出卷那一步要读题目正文。
    #:
    #: ★ 默认指向夹具题库 —— 起服务时也要用同一个目录，
    #: 否则会出现"服务抽的题与出卷工具读的题库不是同一份"。
    bank_items_dir: Path = field(default_factory=lambda: Path("tests/fixtures/bank"))

    @property
    def BANK_ITEMS_DIR(self) -> Path:  # noqa: N802 - 与既有调用点的可读性对齐
        return self.bank_items_dir

    # ---------------------------------------------------------------- HTTP --
    def items(
        self,
        path: str,
        *,
        query: dict | None = None,
        expect: int | tuple[int, ...] = 200,
    ) -> list[Any]:
        """读一个**列表端点**并拆开统一信封 `{"items": [...]}`。

        ## 为什么要绕这一道，而不是直接 `req(...)[1]`

        因为"形状统一"这件事需要在**每一处调用**上被真的用一次。
        如果 e2e 到处 `.get("items")`，那"统一"就只是文档里的一句话；
        如果 e2e 到处兼容两种形状，那它就永远发现不了回归。

        这里**只认 `{"items": [...]}`**，别的一律当成缺陷报出来 ——
        和 `step_response_shape_consistency` 一起构成两道。
        """
        _, body = self.req("GET", path, query=query, expect=expect)
        assert isinstance(body, dict) and isinstance(body.get("items"), list), (
            f"{path} 不是统一信封：{type(body).__name__} {str(body)[:160]}"
        )
        return body["items"]

    def req(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        raw: bytes | None = None,
        query: dict | None = None,
        expect: int | tuple[int, ...] = 200,
    ) -> tuple[int, object]:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else None
        )
        r = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                code, payload = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            code, payload = exc.code, exc.read()
        try:
            parsed: object = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = payload.decode("utf-8", "replace")[:200]
        wanted = expect if isinstance(expect, tuple) else (expect,)
        if code not in wanted:
            raise AssertionError(f"{method} {path} → {code}（期望 {wanted}）：{parsed}")
        return code, parsed

    def add(self, name: str, status: str, note: str = "") -> None:
        self.results.append(Result(name, status, note))

    def step(self, name: str) -> None:
        """让每个步骤自己决定记什么。失败不中断 —— 一次跑完看全貌。"""
        self._current = name

    def ok(self, note: str = "") -> None:
        self.add(self._current, OK, note)

    def gap(self, note: str) -> None:
        self.add(self._current, GAP, note)

    def fail(self, note: str) -> None:
        self.add(self._current, FAIL, note)


# --------------------------------------------------------------------------- #
# 步骤
# --------------------------------------------------------------------------- #


def step_health(j: Journey) -> None:
    j.step("健康检查")
    _, body = j.req("GET", "/api/health")
    j.ok(f"{body}")


def step_model_config(j: Journey) -> None:
    """① 任何行为之前必须配好模型 —— 且要同时支持本地与外部。"""
    j.step("模型配置（本地 Ollama + 外部 API key/base_url）")
    providers = j.items("/api/llm/providers")
    rows = providers["value"] if isinstance(providers, dict) else providers
    local = [p for p in rows if p["kind"] == "ollama"]
    external = [p for p in rows if p["kind"] == "openai" and p["needs_key"]]
    no_key = [p for p in rows if not p["needs_key"]]
    if not local or len(external) < 5:
        j.fail(f"本地 {len(local)} 个 / 外部需密钥 {len(external)} 个")
        return
    _, cfg = j.req("GET", "/api/llm/config")
    j.ok(
        f"{len(rows)} 个 provider（本地 {len(local)}、外部需密钥 {len(external)}、"
        f"免密钥 {len(no_key)}）；当前 source={cfg.get('source')}"
    )


def step_llm_profile_crud(j: Journey) -> None:
    """配置一个**外部** profile（api_key + base_url）并探测模型列表。"""
    j.step("新建外部模型 profile → 探测 → 激活")
    _, prof = j.req(
        "POST",
        "/api/llm/profiles",
        {
            "provider_id": "custom",
            "label": "外部自填（验证用）",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-verify-not-a-real-key",
        },
        expect=(200, 201),
    )
    pid = prof.get("profile_id") if isinstance(prof, dict) else None
    if not pid:
        j.fail(f"创建没返回 profile_id：{prof}")
        return
    j.memo["profile_id"] = pid
    # 探测会真的打外部网络；密钥是假的，所以这里只验证"能发出请求且失败被如实报告"
    code, probed = j.req("POST", f"/api/llm/profiles/{pid}/probe", expect=(200, 400, 502))
    _, active = j.req("POST", f"/api/llm/profiles/{pid}/activate", expect=(200, 409))
    _, cfg = j.req("GET", "/api/llm/config")
    j.ok(
        f"profile={pid} 创建成功；probe→HTTP {code}；activate→200；"
        f"config.source={cfg.get('source')}"
    )
    # 清理，别把验证用的假 profile 留在库里
    j.req("DELETE", f"/api/llm/profiles/{pid}", expect=(200, 204, 404, 409))


def step_session_read(j: Journey) -> None:
    j.step("会话域：列表 / 状态 / 计划")
    sessions = j.items("/api/sessions")
    rows = sessions["value"] if isinstance(sessions, dict) else sessions
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    _, plan = j.req("GET", f"/api/sessions/{j.session}/plan")
    ver = plan.get("version_no") or plan.get("version")
    steps = plan.get("steps") or []
    j.memo["plan_version"] = ver
    j.memo["plan_steps"] = [s.get("step_id") for s in steps]
    j.ok(f"{len(rows)} 个会话；计划 v{ver}，{len(steps)} 个步骤；state 可读")


def step_discuss_changes_plan(j: Journey) -> None:
    """③ 讨论计划项 —— 核心不变量：讨论**不产生任何全局写入**。"""
    j.step("讨论改计划（且不碰全局认知）")
    before_logs = j.items("/api/router-logs", query={"limit": 500})
    logs0 = before_logs["value"] if isinstance(before_logs, dict) else before_logs
    step_id = (j.memo.get("plan_steps") or [None])[0]
    _, out = j.req(
        "POST",
        f"/api/sessions/{j.session}/discuss",
        {"message": "一天 50 个新词太多了，我记不住", "step_id": step_id},
    )
    _, after = j.req("GET", f"/api/sessions/{j.session}/plan")
    ver1 = after.get("version_no") or after.get("version")
    after_logs = j.items("/api/router-logs", query={"limit": 500})
    logs1 = after_logs["value"] if isinstance(after_logs, dict) else after_logs
    v0 = j.memo.get("plan_version")
    if ver1 == v0:
        j.fail(f"讨论后计划版本没变（仍 v{ver1}）——讨论没落地")
        return
    if len(logs1) != len(logs0):
        j.fail(f"★ 讨论产生了全局写入：router_log {len(logs0)} → {len(logs1)}")
        return
    j.ok(f"计划 v{v0} → v{ver1}；router_log 恒为 {len(logs1)} 条（讨论不写全局 ✅）")


def step_motivation_lock(j: Journey) -> None:
    """乐观锁：旧版本号必须被拒绝（409）。"""
    j.step("动机修改 + 乐观锁")
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    ver = state["versions"]["session"]
    _, out = j.req(
        "PATCH",
        f"/api/sessions/{j.session}/motivation",
        {"expected_version": ver, "change": "补充：想在本周内完成"},
    )
    # 再用**同一个**（已过期的）版本号提交第二次 → 必须 409
    code, _ = j.req(
        "PATCH",
        f"/api/sessions/{j.session}/motivation",
        {"expected_version": ver, "change": "并发提交"},
        expect=(409, 200),
    )
    if code == 409:
        j.ok("首次写入成功；同一版本号再次提交被拒（409）✅")
    else:
        j.fail(f"旧版本号没被拒绝（{code}）—— 乐观锁没生效")


def step_session_side_records(j: Journey) -> None:
    j.step("笔记 / 里程碑 / 快照 / stage")
    _, note = j.req(
        "POST",
        f"/api/sessions/{j.session}/notes",
        {"content": "今天状态一般", "tags": ["状态"]},
        expect=(200, 201),
    )
    _, ms = j.req(
        "POST", f"/api/sessions/{j.session}/milestones", {"title": "完成第一单元"},
        expect=(200, 201),
    )
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    ver = state["versions"]["session"]
    # `energy` 是 Level 枚举（high|medium|low），不是 0-10 的数字
    _, snap = j.req(
        "PATCH",
        f"/api/sessions/{j.session}/snapshot",
        {"expected_version": ver, "energy": "medium", "environment": "图书馆"},
    )
    _, state2 = j.req("GET", f"/api/sessions/{j.session}/state")
    j.step("stage 记录")
    _, st = j.req(
        "POST",
        f"/api/sessions/{j.session}/stage",
        {
            "expected_version": state2["versions"]["session"],
            "type": "idea",  # 枚举：temp_pref | idea | plan_override
            "content": "验证用 stage 条目",
        },
        expect=(200, 201),
    )
    j.add("笔记 / 里程碑 / 快照 / stage", OK, "四者均可写")


def step_plan_item(j: Journey) -> None:
    j.step("计划项：改状态 / 取讨论上下文包")
    steps = j.memo.get("plan_steps") or []
    if not steps:
        j.fail("没有计划步骤可用")
        return
    sid = steps[0]
    _, ctx = j.req("GET", f"/api/sessions/{j.session}/plan/steps/{sid}/context")
    keys = sorted(ctx.keys()) if isinstance(ctx, dict) else []
    j.ok(f"步骤 {sid} 的上下文包可读（字段：{', '.join(keys[:8])}）")


def step_annotate_and_see_basis(j: Journey) -> None:
    """标注某一句 + 看推断理由（P15）。

    ## 这一步要证明什么

    不是"有个端点能存一段引用"，而是**用户点「为什么这么说」时拿到的
    是可核对的事实、不是新编的解释**。三格逐层加严：

    ① 标一句话 → 响应里**当场**有依据（界面不必再发一次请求）；
    ② 依据里的工具轨迹**来自这条消息真实落库的那一轮**
       （所以先走 `/discuss` 真跑一次带工具调用的讨论）；
    ③ 引文对不上 → **422**，而且理由说的是「逐字」——
       这一格是整张表的立身之本：一条指向不存在内容的标注
       会让"凭什么这么说"的回答挂在一句谁都没说过的话上。

    ★ 还要验一条**诚实的缺失**：`basis.limits` 必须跟着走。
    它能回答"当时读到了什么"，不能回答"为什么选了这个措辞" ——
    这句话不跟着返回，用户会把"有依据"读成"这个结论对"。
    """
    j.step("标注某句 + 看推断理由（依据是当时落库的轨迹，不是新编的解释）")

    step_id = (j.memo.get("plan_steps") or [None])[0]
    if step_id is None:
        j.fail("拿不到计划步骤 id（`step_plan_item` 那一步没留下 memo）")
        return

    # ① 真跑一轮**带工具调用**的讨论，拿到那条助手消息
    code, run = j.req(
        "POST",
        f"/api/sessions/{j.session}/discuss",
        {"message": "帮我看看这一步该怎么安排", "step_id": step_id},
    )
    if code != 200:
        j.fail(f"讨论请求失败（{code}）")
        return
    if not run.get("tool_trace"):
        j.fail(
            "这一轮讨论没有调用任何工具 —— 标注的依据就空了一半，"
            "这一步也就验不出「轨迹真的能被读到」。"
        )
        return

    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    assistants = [row for row in (state.get("chat") or []) if row.get("role") == "assistant"]
    if not assistants:
        j.fail("讨论之后 `/state` 里读不到助手消息")
        return
    message = assistants[-1]
    msg_id = message["msg_id"]

    # ★ 锚点必须落库：不记锚点，事后只能按今天的规则挑一步，
    #   而那节标题会被读成"当时的证据"。
    if not message.get("anchor_step_id"):
        j.fail("★ 助手消息没有记录锚点（`anchor_step_id` 为空）—— 上下文重放会变成猜")
        return

    # 从这句话里**截一段真实的文字**去标注（引用必须逐字存在）
    content = message["content"]
    quote = content[: min(12, len(content))].strip()
    if len(quote) < 2:
        j.fail(f"这条回复太短，没法截引用：{content!r}")
        return

    # ② 标注 → 同一次响应里就要有依据
    code, created = j.req(
        "POST",
        f"/api/sessions/{j.session}/annotations",
        {"msg_id": msg_id, "quote": quote, "question": "凭什么这么说？"},
        expect=(201,),
    )
    basis = created.get("basis") or {}
    tools_in_basis = [item.get("tool") for item in (basis.get("traces") or [])]
    tools_in_run = [item.get("tool") for item in run["tool_trace"]]
    if tools_in_basis != tools_in_run:
        j.fail(
            "★ 依据里的工具轨迹与这一轮真实执行的**不一致**："
            f"{tools_in_basis} ≠ {tools_in_run}（依据必须是落库的那一份，不是重算的）"
        )
        return
    if not all(item.get("scope") for item in (basis.get("traces") or [])):
        j.fail("依据里的轨迹没有作用域 —— 「它当时能不能写」就答不上来")
        return
    if basis.get("context_step_id") != step_id:
        j.fail(
            f"重放用的锚点不是当时那一步：{basis.get('context_step_id')} ≠ {step_id}"
        )
        return
    if not basis.get("context_sections"):
        j.fail(f"有锚点却没重放出任何上下文节：{basis.get('context_note')!r}")
        return
    if "不能" not in (basis.get("limits") or ""):
        j.fail("★ 依据里没有那句「它能证明什么、不能证明什么」—— 会被读成「这个结论对」")
        return

    # ③ 引文对不上必须被拒（422），且理由要指向"逐字"
    code, refused = j.req(
        "POST",
        f"/api/sessions/{j.session}/annotations",
        {"msg_id": msg_id, "quote": "这句它绝对没说过，端到端验证专门编的"},
        expect=(422,),
    )
    if "逐字" not in (refused.get("message") or ""):
        j.fail(f"引用对不上被拒了，但理由没说清是「逐字」：{refused.get('message')!r}")
        return

    # ④ 列表是信封 + 取一条会**重放**（所以带 `context_note`）
    _, listed = j.req("GET", f"/api/sessions/{j.session}/annotations")
    rows = listed["items"] if isinstance(listed, dict) else listed
    if len(rows) != 1:
        j.fail(f"标注列表里应有 1 条，实际 {len(rows)} 条")
        return
    annotation_id = rows[0]["annotation_id"]
    _, one = j.req("GET", f"/api/sessions/{j.session}/annotations/{annotation_id}")
    if not (one.get("basis") or {}).get("context_note"):
        j.fail("单独取一条时没有给出重放说明（`context_note` 为空）")
        return

    # ⑤ 删除返回被删掉的那一条，且**消息本身还在**
    code, deleted = j.req(
        "DELETE", f"/api/sessions/{j.session}/annotations/{annotation_id}", expect=(200,)
    )
    if (deleted.get("deleted") or {}).get("annotation_id") != annotation_id:
        j.fail("删除没有返回被删掉的那一条（界面无法确认删的是哪句）")
        return
    _, listed = j.req("GET", f"/api/sessions/{j.session}/annotations")
    rows = listed["items"] if isinstance(listed, dict) else listed
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    still_there = any(row.get("msg_id") == msg_id for row in (state.get("chat") or []))
    if rows or not still_there:
        j.fail(f"删除后标注应清空（实际 {len(rows)} 条），而那条消息必须还在（{still_there}）")
        return

    j.ok(
        f"{annotation_id} 建→读出依据（{len(tools_in_basis)} 步工具、锚点 {step_id}）"
        "→取一条→删掉；伪造引用被 422 拒绝；那条消息仍在"
    )


def step_reflection_after_review(j: Journey) -> None:
    """复盘之后按指引写感想（P15）。

    ## 这一步要证明什么

    ① **时机是一条被代码管住的事实**，不是界面上的一个按钮：
       执行中点「写感想」拿到的是**一条能照做的原因**（去写笔记 / 先去复盘），
       而不是一个 403 或 404；
    ② 指引里的每一条事实都来自**这次会话真实的数据**
       （计划进度、用户自己写的完成判据）—— 固定四句话贴给任何会话都成立，
       因此也帮不上任何忙；
    ③ 复盘之后真的写得进去，而且四要素**分项存着**：
       `V-PAR` 的 `measured` 通道声明的正是这四要素的完整度，
       拼成一坨存就只能靠再解析自然语言来回答。

    ★ 顺序：这一步必须在 `step_complete_and_sediment` **之前**跑 ——
    它要用 `review` 态；而那个会话随后会被归档，归档后照样能写（回看可以很久以后），
    所以最后再补一格终态可写。
    """
    j.step("复盘之后按指引写感想（四要素分项 · 时机由代码管住）")

    # ① 执行中：指引照样给，但要说清现在该做什么
    _, guide = j.req("GET", f"/api/sessions/{j.session}/reflection")
    if not guide.get("blocked_reason"):
        j.fail("执行中写感想没有被说明原因 —— 界面只能给一个禁用的按钮")
        return
    if "笔记" not in guide["blocked_reason"] and "/notes" not in guide["blocked_reason"]:
        j.fail(f"原因里没有可照做的下一步：{guide['blocked_reason']!r}")
        return
    facts = " ".join(fact for item in guide["items"] for fact in item["facts"])
    if "步" not in facts:
        j.fail(f"指引里没有这次会话的真实进度：{facts!r}")
        return

    code, _ = j.req(
        "POST",
        f"/api/sessions/{j.session}/reflections",
        {"answers": {"why": "端到端验证：这时候不该写得进去"}},
        expect=(409,),
    )
    if code != 409:
        j.fail(f"★ 执行中竟然写进了感想（{code}）—— 时机没有真的被管住")
        return

    # ② 走到复盘：写一条总结（`review` 态是总结该在的地方）
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    version = state["versions"]["session"]
    code, _ = j.req(
        "POST",
        f"/api/sessions/{j.session}/transitions",
        {"target": "review", "expected_version": version},
        expect=(200, 409),
    )
    if code != 200:
        j.fail(f"进入 review 失败（{code}）—— 后面的感想没法验")
        return
    j.req(
        "POST",
        f"/api/sessions/{j.session}/summary",
        {"content": "端到端验证：这一轮的复盘总结。"},
        expect=(201,),
    )

    # ③ 复盘之后：指引放行，四要素写进去并分项存着
    _, guide = j.req("GET", f"/api/sessions/{j.session}/reflection")
    if guide.get("blocked_reason"):
        j.fail(f"复盘之后仍然不该写？{guide['blocked_reason']!r}")
        return
    answers = {
        "what_happened": "按计划走了一遍，题只做了一半",
        "expected_gap": "以为能做对 10 道，实际错了 3 道",
        "why": "虚拟语气的时态倒装没认出来",
        "next_change": "下次先做 5 道只考倒装的题",
    }
    code, written = j.req(
        "POST",
        f"/api/sessions/{j.session}/reflections",
        {"answers": answers},
        expect=(201,),
    )
    if written.get("answers") != answers:
        j.fail("★ 四要素没有被分项存下来 —— 完整度就只能靠再解析自然语言")
        return
    if not written.get("summary_id"):
        j.fail("感想没有记下它是在哪份总结之后写的（时间锚丢了）")
        return
    if answers["why"] not in written.get("content", ""):
        j.fail(f"正文里没有拼进四要素：{written.get('content')!r}")
        return

    # ④ 空感想不写
    code, empty = j.req(
        "POST", f"/api/sessions/{j.session}/reflections", {}, expect=(422,)
    )
    if "空的感想" not in (empty.get("message") or ""):
        j.fail(f"空感想的拒绝理由不清楚：{empty.get('message')!r}")
        return

    # ⑤ 指引跟着更新：写过的项不再出现在 `missing` 里
    _, guide = j.req("GET", f"/api/sessions/{j.session}/reflection")
    if guide.get("missing") != [] or guide.get("existing") != 1:
        j.fail(f"指引没有跟着更新：missing={guide.get('missing')} existing={guide.get('existing')}")
        return
    _, listed = j.req("GET", f"/api/sessions/{j.session}/reflections")
    rows = listed["items"] if isinstance(listed, dict) else listed
    if len(rows) != 1:
        j.fail(f"感想列表应有 1 条，实际 {len(rows)} 条")
        return

    # ⑥ 回到 active，把后面的步骤（完成 → 沉淀）留给它自己那一步
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    j.req(
        "POST",
        f"/api/sessions/{j.session}/transitions",
        {"target": "active", "expected_version": state["versions"]["session"]},
        expect=(200,),
    )

    j.ok(
        f"执行中被拒（409）并给出下一步；复盘后写入 {written['reflection_id']}"
        "（四要素分项 + 记下 summary_id）；空感想 422；指引 missing 清空"
    )


def step_complete_and_sediment(j: Journey) -> None:
    """⑦ 复盘 → 完成 → 沉淀 → 提案 → 逐条批准 → 全局真的变了。

    ★ 状态机在这里会**强制拒绝** `active → completed`（允许的是
    `active → review`）。那不是缺陷 —— 它正是"手动点击复盘"这个要求
    在代码里的样子：**没有经过复盘，就不算完成**。
    所以这一步先走 `transitions → review`，再 complete。
    """
    j.step("复盘 → 完成 → 沉淀（dry-run → 真跑）→ 提案 → 批准")
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    ver = state["versions"]["session"]
    # 直接跳过复盘的路径必须被拒 —— 顺便验证状态机确实在拦
    code, body = j.req(
        "POST",
        f"/api/sessions/{j.session}/complete",
        {"expected_version": ver, "content": "试图跳过复盘"},
        expect=(409, 200),
    )
    if code != 409:
        j.fail("★ 未经复盘就 complete 竟然成功了 —— 状态机没拦住")
        return
    # 走状态机允许的路径：active → review
    _, moved = j.req(
        "POST",
        f"/api/sessions/{j.session}/transitions",
        {"target": "review", "expected_version": ver, "reason": "进入复盘"},
    )
    _, state2 = j.req("GET", f"/api/sessions/{j.session}/state")
    ver2 = state2["versions"]["session"]
    _, done = j.req(
        "POST",
        f"/api/sessions/{j.session}/complete",
        {
            "expected_version": ver2,
            "content": "本轮完成，单词记忆方式有变化",
            # findings 是沉淀通路的**原料**：没有它，sediment 返回 included=[]
            # （实测过：不传 findings 时 dry-run 与真跑都是空的，看起来"沉淀没坏"
            # 而其实什么都没沉淀）。每条要自带 statement，指向变量时还要带 channel。
            "findings": [
                {
                    "statement": "把新词放进当天要读的真实材料里，比孤立背词表记得牢",
                    "variable_id": "V-SE",
                    "channel": "experienced",
                    "basis": "experienced",
                }
            ],
        },
    )
    _, dry = j.req(
        "POST",
        f"/api/sessions/{j.session}/sediment",
        {"trigger": "R4a", "dry_run": True},
        expect=(200, 201),
    )
    _, real = j.req(
        "POST",
        f"/api/sessions/{j.session}/sediment",
        {"trigger": "R4a", "dry_run": False},
        expect=(200, 201),
    )
    props = j.items("/api/proposals", query={"status": "pending_confirmation"})
    rows = props["value"] if isinstance(props, dict) else props
    if not rows:
        j.fail("沉淀后待确认队列为空")
        return
    pid = rows[0]["proposal_id"]
    _, detail = j.req("GET", f"/api/proposals/{pid}")
    intents = detail.get("intents") or []
    if not intents:
        j.fail(f"提案 {pid} 没有 intents")
        return
    decisions = [
        {"intent_id": it["intent_id"], "approved": True, "reason": "端到端验证"}
        for it in intents
    ]
    _, resolved = j.req(
        "POST", f"/api/proposals/{pid}/resolve", {"decisions": decisions}
    )
    j.ok(
        f"跳过复盘被拒(409) ✅ → review → completed → 沉淀 → 提案 {pid}"
        f"（{len(intents)} 条 intent）→ 全部批准；"
        f"verdict={json.dumps(resolved, ensure_ascii=False)[:70]}"
    )


def step_benchmark_checklist(j: Journey) -> None:
    """⑥-1 领域两次勾选（用户：先勾熟悉，再在陌生区里勾感兴趣）。

    这一步同时验证**三件不同的事**，因为它们的坏法是不同的：
    目录在不在（127 格）、勾选能不能存、以及**跨两个列表的约束在不在**。
    第三件最容易被漏掉：`interested ⊆ ¬familiar` 是一条跨字段的规则，
    只在界面上拦（"点过熟悉的格子就灰掉"）时，接口层面它是敞开的。
    """
    j.step("基准 · 领域两次勾选")
    _, state = j.req("GET", "/api/benchmark/state")
    _, checklist = j.req("GET", "/api/benchmark/domains")
    groups = checklist["groups"]
    total = sum(len(g["topics"]) for g in groups)
    if total != 127 or len(groups) != 18:
        j.fail(f"主题目录不对：{len(groups)} 个大类 / {total} 个专题（期望 18 / 127）")
        return

    # 第一遍：勾熟悉。取两个**不同大类**的专题，避免与第二遍撞上
    familiar = ["BIO-MARINE", "CS-AI"]
    # 第二遍：只在陌生区里勾兴趣
    interested = ["LIT-POETRY", "HIST-MODERN"]
    _, profile = j.req(
        "POST",
        "/api/benchmark/domains",
        {
            "familiar": familiar,
            "interested": interested,
            "reason": "端到端验证",
        },
        expect=(200, 201),
    )

    # ★ 反例：兴趣落在熟悉区里必须被拒 —— 否则"哪条规则先生效"无从解释
    code, body = j.req(
        "POST",
        "/api/benchmark/domains",
        {"familiar": ["BIO-MARINE"], "interested": ["BIO-MARINE"]},
        expect=(201, 422),
    )
    overlap_blocked = code == 422

    # ★ 反例：拼错的 topic_id 必须被拒
    code2, _ = j.req(
        "POST",
        "/api/benchmark/domains",
        {"familiar": ["BIO-MARIN"], "interested": []},
        expect=(201, 422),
    )
    typo_blocked = code2 == 422

    _, after = j.req("GET", "/api/benchmark/domains")
    ticked = sum(1 for g in after["groups"] for t in g["topics"] if t["familiar"])
    history = j.items("/api/benchmark/domains/history")
    _, state2 = j.req("GET", "/api/benchmark/state")

    problems = []
    if not overlap_blocked:
        problems.append("重叠勾选没被拦住")
    if not typo_blocked:
        problems.append("拼错的 topic_id 没被拦住")
    if ticked != len(familiar):
        problems.append(f"勾选没存住（{ticked} != {len(familiar)}）")
    if state2["has_checklist"] is not True:
        problems.append("state.has_checklist 仍是 False")
    if not history:
        problems.append("没有历史版本")
    if problems:
        j.fail("；".join(problems))
        return
    j.ok(
        f"目录 18 大类 / {total} 专题；档案 v{profile['version_no']}"
        f"（熟悉 {len(familiar)} / 兴趣 {len(interested)}）；"
        f"重叠勾选 422 ✅、拼错 id 422 ✅；历史 {len(history)} 版；"
        f"state.has_checklist=True ✅"
    )


def step_benchmark_run_lifecycle(j: Journey) -> None:
    """⑥-2 运行生命周期（用户：简版/全面版、可中断保存退出、可继续、可重开）。

    ★ 这一步的核心断言是**中断与进行中可区分**。
    如果实现只靠 `finished_at IS NULL`，两者会是同一个值，
    而"下次是继续还是重开"就没有答案。
    """
    j.step("基准 · 运行生命周期（简版/全面版 + 中断/继续/重开）")
    # 开一次简短版
    _, quick = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "item_ids": ["SE-TXT-001", "SE-TXT-002"],
            "seed": 20260101,
            "topic_snapshot": [
                {
                    "item_id": "SE-TXT-001",
                    "topics": ["BIO-MARINE"],
                    "familiar_at_run": False,
                }
            ],
            "completeness": "partial",
        },
        expect=(200, 201),
    )
    quick_id = quick["run_id"]

    # 落一道题的作答 —— 中断也要留得下东西
    j.req(
        "POST",
        f"/api/benchmark/runs/{quick_id}/responses",
        {
            "item_id": "SE-TXT-001",
            "question_no": 1,
            "kind": "exact",
            "judged_by": "code",
            "raw_answer": "选项 B",
            "item_score": 10.0,
        },
        expect=(200, 201),
    )
    # ★ **必须重新取一次**：`quick` 是落作答**之前**的响应快照，
    # 它的 `response_count` 天然是 0 —— 拿它断言会得到一个恒假的条件。
    _, after_one_answer = j.req("GET", f"/api/benchmark/runs/{quick_id}")

    # 「保存并退出」
    _, saved = j.req(
        "POST",
        f"/api/benchmark/runs/{quick_id}/status",
        {"target": "interrupted", "reason": "端到端验证：保存退出"},
    )
    _, state = j.req("GET", "/api/benchmark/state")
    resumable_id = (state.get("resumable_run") or {}).get("run_id")

    # 「继续上次」
    _, resumed = j.req(
        "POST",
        f"/api/benchmark/runs/{quick_id}/status",
        {"target": "in_progress", "reason": "继续"},
    )
    _, state2 = j.req("GET", "/api/benchmark/state")

    # 「重新开始」：旧的作废、新的从头来
    _, restarted = j.req(
        "POST", f"/api/benchmark/runs/{quick_id}/restart", expect=(200, 201)
    )
    new_id = restarted["run_id"]
    _, old_after = j.req("GET", f"/api/benchmark/runs/{quick_id}")
    all_runs = j.items("/api/benchmark/runs")

    # 全面版也能开（两种不同的仪器，不是难易两档）
    _, full = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "full",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "item_ids": ["SE-TXT-001"],
            "seed": 7,
            "topic_snapshot": [],
            "completeness": "partial",
        },
        expect=(200, 201),
    )

    problems = []
    if quick["status"] != "in_progress":
        problems.append(f"新运行状态是 {quick['status']}（期望 in_progress）")
    if saved["status"] != "interrupted":
        problems.append(f"保存退出后是 {saved['status']}（期望 interrupted）")
    if resumable_id != quick_id:
        problems.append(f"state.resumable_run 是 {resumable_id}（期望 {quick_id}）")
    if resumed["status"] != "in_progress":
        problems.append(f"继续后是 {resumed['status']}（期望 in_progress）")
    if (state2.get("resumable_run") or None) is not None:
        problems.append("继续之后仍然说'可继续'—— 会悄悄接管正在进行的会话")
    if old_after["status"] != "abandoned":
        problems.append(f"重开后旧运行是 {old_after['status']}（期望 abandoned）")
    if new_id == quick_id:
        problems.append("重开没有产生新的 run_id")
    if restarted["status"] != "in_progress":
        problems.append(f"重开的新运行状态是 {restarted['status']}")
    if len(old_after.get("status_history") or []) < 3:
        problems.append(f"状态史太短：{old_after.get('status_history')}")
    if after_one_answer.get("response_count") != 1:
        problems.append(
            f"落了一步作答后 response_count={after_one_answer.get('response_count')}"
        )
    if full["form"] != "full":
        problems.append(f"全面版卷别是 {full['form']}")
    if problems:
        j.fail("；".join(problems))
        return
    j.ok(
        f"简短版 {quick_id}：in_progress → 落 1 题 → interrupted → "
        f"state 认出可继续 ✅ → 继续 → 重开（旧的 abandoned、新的 {new_id}）；"
        f"全部运行 {len(all_runs)} 次；全面版 {full['run_id']} 也能开 ✅"
    )


def step_benchmark_sampling(j: Journey) -> None:
    """⑥-3 抽题：从清单与历史算出一套题（Benchmark v0.3 §6.3）。

    ★ 这一步验的不是"抽题接口返回 200"，而是**三条会静默出错的规则**：

    1. **锚题全出**（它是等值化的分母，缺一道全部历史分数的可比性就失效）；
    2. **难度构成是「易 1 : 中 2 : 难 1」**（不固定的话，换题带来的噪声
       会被读成"我进步了"）；
    3. **预览与真正记录的是同一套题**（两处各抽一次、参数只要差一点，
       就会出现"界面说考这 6 道、记录成另外 6 道"，而它不报错）。

    题池不完整时这一步是**缺口（⚠️）而不是失败**：
    机制与内容是两件事，而"现在是哪一件"必须说清 ——
    所以缺口信息里带着**让它变绿的确切命令**。
    """
    j.step("基准 · 抽题（锚题全出 / 难度构成 / 预览=记录）")
    query = {
        "form": "quick",
        "variable_id": "V-SE",
        "channel": "measured",
        "method_id": "M-SE-04",
        "seed": 20260101,
    }
    code, plan = j.req("GET", "/api/benchmark/plan", query=query, expect=(200, 422))
    if code == 422:
        detail = str(plan.get("detail") or plan)
        j.gap(
            f"抽题端点在，但题池还抽不出题：{detail[:160]}"
            f"｜★ 这是**内容缺口**不是机制缺口。用一份完整题库起服务即可验证机制："
            f"`serveui --items-dir tests/fixtures/bank`"
            f"（先用 `python -m tools.makefixturebank` 生成）"
        )
        return

    items = plan.get("items") or []
    anchors = [i for i in items if i.get("role") == "anchor"]
    regulars = [i for i in items if i.get("role") != "anchor"]
    mix = {}
    for item in regulars:
        mix[item.get("difficulty")] = mix.get(item.get("difficulty"), 0) + 1
    problems: list[str] = []
    if len(anchors) < 2:
        problems.append(f"锚题只有 {len(anchors)} 道（至少 2 道）")
    if mix != {"易": 1, "中": 2, "难": 1}:
        problems.append(f"普通题难度构成是 {mix}，不是 易1:中2:难1")
    ids = [i.get("item_id") for i in items]
    if len(ids) != len(set(ids)):
        problems.append(f"同一套题里出现重复题目：{ids}")
    if not all(i.get("why") for i in items):
        problems.append("有题目没说明自己是怎么被抽中的（why 为空）")
    if not plan.get("topics"):
        problems.append("没有记录本次涉及的专题（轮换就无从算起）")
    if problems:
        j.fail("；".join(problems))
        return

    # ★ 预览与记录必须是**同一套题**：同一个种子、同一组参数。
    started = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "seed": 20260101,
        },
        expect=(200, 201),
    )[1]
    if started.get("item_ids") != ids:
        j.fail(
            f"预览与记录的题**不是同一套**：预览 {ids}，记录 {started.get('item_ids')} —— "
            f"两处各抽一次的话，界面说的与记录下来的会慢慢分叉，而这不会报错"
        )
        return
    if started.get("topic_snapshot") != plan.get("topics"):
        j.fail(
            f"专题快照与预览不一致：预览 {plan.get('topics')}，"
            f"记录 {started.get('topic_snapshot')}"
        )
        return

    _, again = j.req("GET", "/api/benchmark/plan", query=query, expect=(200,))
    if [i.get("item_id") for i in (again.get("items") or [])] != ids:
        j.fail("同一个种子抽出了不同的题 —— §6.3 要求「同一种子必须抽出同一套」")
        return

    mix_text = "、".join(f"{k}{v}" for k, v in sorted(mix.items()))
    j.ok(
        f"{len(items)} 题：锚题 {len(anchors)} 道全出，普通题难度 {mix_text}；"
        f"涉及 {len(plan.get('topics') or [])} 个专题；"
        f"预览与记录一致（{started.get('run_id')}）✅；同种子可复现 ✅"
    )


def step_benchmark_scoring(j: Journey) -> None:
    """⑥-4 计分：LLM 判官 + 引文核对 + 方法内聚合（P9）。

    ★ 这一步的核心断言是**引文核对真的在跑**，而不是"分数算出来了"。

    端到端用的那个确定性替身（`ScriptedPlanChange`）在判官那一路
    会**故意多报一条编造的命中**。于是：

    - 分数 = `10 × 核实过的命中 / 要点总数`，而不是"判官说命中几条就几条"；
    - 响应里的 `scoring_method` 会写着 `引文核对降级 1`。

    如果哪天有人把核对去掉（直接信模型），分数会**变大**、
    而降级计数会消失 —— 这一步立刻变红。没有这一条断言，
    "去掉核对"是一个所有人都不会察觉的改动，因为分数照样是一个 0-10 的数。

    ★ 第二个断言是**红线 7 在接口上的可见性**：正误题得 0 分，
    但它 `counted=false`、方法分是 `NULL` 且理由里写明"标尺题"。
    把标尺题算进读数，会让一个正误题成绩静默地变成"学习倾向"分数。
    """
    j.step("基准 · 计分（判官 + 引文核对 / 标尺题不进读数）")

    # ---- ① 要点题：判官给命中，代码核对引文 ----
    run_id = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "item_ids": ["A-1", "A-2", "R-E0"],
            "seed": 20260101,
        },
        expect=(200, 201),
    )[1]["run_id"]
    answer = "我读到的：主线是这篇材料在讲某个过程，事实是有一个具体数据，限定条件我忘了。"
    for item_id in ("A-1", "A-2", "R-E0"):
        j.req(
            "POST",
            f"/api/benchmark/runs/{run_id}/responses",
            {
                "item_id": item_id,
                "question_no": 0,
                "kind": "key_points",
                "judged_by": "llm",
                "raw_answer": answer,
            },
            expect=(200, 201),
        )
    code, scored = j.req("POST", f"/api/benchmark/runs/{run_id}/score", expect=(200, 422))
    if code == 422:
        j.gap(
            f"计分端点返回 422：{json.dumps(scored, ensure_ascii=False)[:180]}"
            f"｜多半是题池缺规格字段（用 `--items-dir tests/fixtures/bank` 起服务）"
        )
        return

    rows = scored.get("items") or []
    problems: list[str] = []
    if len(rows) != 3:
        problems.append(f"计分结果里有 {len(rows)} 道题，落了 3 道")
    for row in rows:
        method = row.get("scoring_method") or ""
        if not method.startswith("llm:key_points"):
            problems.append(f"{row.get('item_id')} 的计分规则是 `{method}`，不是要点题判分")
            continue
        # ★ 替身总会编一条引用 —— 核对必须把它打掉。
        if "引文核对降级 1" not in method:
            problems.append(
                f"{row.get('item_id')} 的 `scoring_method` 是 `{method}` —— "
                f"没有降级记录，说明判官声明的命中**没被核对**（编造的引用也被算成了命中）"
            )
    if scored.get("method_score") is None:
        problems.append(f"三道要点题都算出了单题分，方法分却是 NULL：{scored.get('method_detail')}")
    if scored.get("completeness") != "complete":
        problems.append(f"完整度是 {scored.get('completeness')}")
    if not scored.get("confidence_reason"):
        problems.append("置信档没有给理由")
    if problems:
        j.fail("；".join(problems))
        return
    scores = "、".join(f"{row['item_id']} {row['item_score']}" for row in rows)

    # ---- ② 正误题：得 0 分，但**不产出读数**（红线 7） ----
    ruler_run = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "item_ids": ["A-1"],
            "seed": 20260102,
        },
        expect=(200, 201),
    )[1]["run_id"]
    j.req(
        "POST",
        f"/api/benchmark/runs/{ruler_run}/responses",
        {
            "item_id": "A-1",
            "question_no": 1,
            "kind": "exact",
            "judged_by": "code",
            "raw_answer": "一个明显不对的答案",
        },
        expect=(200, 201),
    )
    _, ruler = j.req("POST", f"/api/benchmark/runs/{ruler_run}/score", expect=(200,))
    ruler_row = (ruler.get("items") or [{}])[0]
    if ruler_row.get("item_score") != 0.0:
        j.fail(f"答错的正误题没给 0 分：{ruler_row.get('item_score')}")
        return
    if ruler_row.get("counted") is not False:
        j.fail(
            f"正误题被算进了读数（counted={ruler_row.get('counted')}）—— "
            f"红线 7：正误题只做等值化标尺，不得产出 `measured` 读数"
        )
        return
    if ruler.get("method_score") is not None:
        j.fail(f"只有标尺题时却给了方法分：{ruler.get('method_score')}")
        return
    if "标尺题" not in (ruler.get("method_detail") or ""):
        j.fail(f"没给出「标尺题」这个理由：{ruler.get('method_detail')}")
        return

    # ---- ③ 收尾自己算分（不再要求调用方算好） ----
    _, finished = j.req(
        "POST",
        f"/api/benchmark/runs/{run_id}/finish",
        {"completeness": "complete", "boundary": ""},
        expect=(200,),
    )
    if finished.get("method_score") != scored.get("method_score"):
        j.fail(
            f"收尾后的方法分（{finished.get('method_score')}）与计分时给的"
            f"（{scored.get('method_score')}）不一致 —— "
            f"收尾若重新判分，他看到的 7.5 与库里存的可能是另一个数"
        )
        return

    j.ok(
        f"要点题逐题判分：{scores}（每条都记了 `scoring_method`，"
        f"且**编造的引用被核对打掉**）→ 方法分 {scored['method_score']}"
        f"（{scored['confidence']}：{scored['confidence_reason'][:40]}…）；"
        f"正误题得 0 分但 `counted=false`、方法分 NULL 并说明是标尺题 ✅；"
        f"收尾 {finished['run_id']} 自己算出同一个方法分 ✅"
    )


def step_benchmark_self_report(j: Journey) -> None:
    """⑥-6 自评题型与隐性参数（P11）。

    ★ 这一步验的是**两件在界面上看不出区别的事**：

    1. **自评量表的选项权重来自题目**，不由模型或界面临时定。
       权重是测量刻度 —— 两次作答用了不同刻度就不可比，
       而不可比的两个数放在一起，看起来只是"他变了"。
       所以这里读回 `scoring_method`，确认它写着用的是哪份量表（句数/档数/反向句）。
    2. **隐性参数被记下来了，而且没记到时作废（不是 0 分）**。
       同一个观测点，传 `switch_count=3` 得 7 分、**不传**则是"没记到"。
       这两条合起来才证明那一列真的贯通了整条链路：
       只测"传了能得分"，一个把值写死成 0 的实现同样会通过。

    ★ 反向句也要验：一句「我常常读到一半才发现没抓住重点」若被当成正向句，
    分数会**整体偏移**，而它看起来完全正常。
    """
    j.step("基准 · 自评题型与隐性参数（记录 ≠ 计分）")

    # ---- ① 自评量表：选项权重由题目声明 ----
    run_id = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "stated",
            "method_id": "M-SE-01",
            "item_ids": ["S-1"],
            "seed": 20260101,
        },
        expect=(200, 201),
    )[1]["run_id"]
    # 1 总是、2 经常、3 从不 —— 第 3 句是反向句，所以"从不"在这一句上是最高的。
    j.req(
        "POST",
        f"/api/benchmark/runs/{run_id}/responses",
        {
            "item_id": "S-1",
            "question_no": 0,
            "kind": "self_report",
            "judged_by": "code",
            "raw_answer": "1=always；2=often；3=never",
        },
        expect=(200, 201),
    )
    code, scored = j.req("POST", f"/api/benchmark/runs/{run_id}/score", expect=(200, 422))
    if code == 422:
        j.gap(
            f"自评题的计分返回 422：{json.dumps(scored, ensure_ascii=False)[:160]}"
            f"｜多半是题池里没有 `S-1`（用 `--items-dir tests/fixtures/bank` 起服务）"
        )
        return
    row = (scored.get("items") or [{}])[0]
    if row.get("item_score") is None:
        j.fail(f"自评量表没算出分数：{row.get('void_reason')!r}")
        return
    method = row.get("scoring_method") or ""
    if "self_report" not in method or "反向 1" not in method:
        j.fail(
            f"`scoring_method` 是 `{method}` —— 它必须写明用的是哪份量表"
            f"（句数/档数/反向句数），否则事后无法回答「这个分是哪把尺子量的」"
        )
        return
    # 期望值：1.0 + 0.67 + (1-0) = 2.67，均值 0.89 → 8.9
    if abs(row["item_score"] - 8.9) > 0.01:
        j.fail(
            f"自评分是 {row['item_score']}，按题目声明的权重应当是 8.9 —— "
            f"差在这里通常意味着反向句被当成了正向句（会整体偏移，且看起来正常）"
        )
        return

    # ---- ② 少答一句 = 没测到（不是"按答了的求均值"） ----
    partial_run = j.req(
        "POST",
        "/api/benchmark/runs",
        {
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "stated",
            "method_id": "M-SE-01",
            "item_ids": ["S-1"],
            "seed": 20260102,
        },
        expect=(200, 201),
    )[1]["run_id"]
    j.req(
        "POST",
        f"/api/benchmark/runs/{partial_run}/responses",
        {
            "item_id": "S-1",
            "question_no": 0,
            "kind": "self_report",
            "judged_by": "code",
            "raw_answer": "1=always；2=often",
        },
        expect=(200, 201),
    )
    _, partial = j.req("POST", f"/api/benchmark/runs/{partial_run}/score", expect=(200,))
    partial_row = (partial.get("items") or [{}])[0]
    if partial_row.get("item_score") is not None:
        j.fail(
            f"只答了两句却算出了 {partial_row['item_score']} 分 —— "
            f"少答的那一句不是随机的（跳过通常意味着那句不好答），"
            f"按答了的求均值会得到一个**系统偏移**的分数，而它看起来完全正常"
        )
        return

    # ---- ③ 隐性参数：传了能得分，没传是"没记到" ----
    def _observation_run(seed: int, implicit: dict) -> dict:
        body = {
            "item_id": "O-1",
            "question_no": 0,
            "kind": "observation",
            "judged_by": "code",
            "raw_answer": "",
        }
        body.update(implicit)
        rid = j.req(
            "POST",
            "/api/benchmark/runs",
            {
                "form": "quick",
                "variable_id": "V-HSR",
                "channel": "experienced",
                "method_id": "M-HSR-05",
                "item_ids": ["O-1"],
                "seed": seed,
            },
            expect=(200, 201),
        )[1]["run_id"]
        j.req("POST", f"/api/benchmark/runs/{rid}/responses", body, expect=(200, 201))
        return j.req("POST", f"/api/benchmark/runs/{rid}/score", expect=(200,))[1]

    recorded = _observation_run(20260103, {"switch_count": 3})
    recorded_row = (recorded.get("items") or [{}])[0]
    if recorded_row.get("item_score") != 7.0:
        j.fail(
            f"记下的 `switch_count=3` 没有按题目声明的换算表算成 7 分，"
            f"实际是 {recorded_row.get('item_score')}（{recorded_row.get('void_reason')!r}）—— "
            f"这一列没有贯通到计分"
        )
        return
    if "v-hsr-switch-1" not in (recorded_row.get("scoring_method") or ""):
        j.fail(
            f"`scoring_method` 是 `{recorded_row.get('scoring_method')}` —— "
            f"它必须写出用的是哪张换算表（表会改，半年后要能回答"
            f"「当时那张表把 3 次映射成了几分」）"
        )
        return

    missing = _observation_run(20260104, {})
    missing_row = (missing.get("items") or [{}])[0]
    if missing_row.get("item_score") is not None:
        j.fail(
            f"没记到 `switch_count` 却算出了 {missing_row['item_score']} 分 —— "
            f"「他没切换」与「我们忘了记切换」是两件事，不能给同一个分"
        )
        return
    if "没记到" not in (missing_row.get("void_reason") or ""):
        j.fail(
            f"作废理由没有说明是**没记到**：{missing_row.get('void_reason')!r} —— "
            f"与「没有卡点」混成一句，事后就分不清是他太强还是我们忘了记"
        )
        return

    j.ok(
        f"自评量表按题目声明的权重算出 {row['item_score']}"
        f"（`{method[:34]}…`，反向句按 `1-w` 计）；少答一句 → 没测到（不是按答了的求均值）✅；"
        f"隐性参数 `switch_count=3` → {recorded_row['item_score']} 分"
        f"（`{recorded_row.get('scoring_method')}`），"
        f"**不传** → 作废并说明「没记到」（不是 0 分）✅"
    )


def step_benchmark_equating(j: Journey) -> None:
    """⑥-7 等值化校正：锚题把两次运行换算到同一把尺子上（P12）。

    ★ 这一步的核心断言是**基线不漂**，以及**原始分不被改写**。

    这两条都属于"错了也不报错"的那一类：

    - 基线若取"最近一次"而不是"最早一次"，它自己就会漂 ——
      而它一漂，**全部历史分数的可比性同时失效**，只是每一行看起来都正常；
    - 校正量若直接写进 `method_score`，"当时是多少分"这个问题就永远没有答案了。
      半年后回看时，那恰恰是最要紧的问题。

    ★ 用来制造"两次分数不同"的办法：让第二次运行的答案短到引文核对**一条也认不出**
    （替身是从答案里截 12 个字的，答案不到 4 个字就无从引用）。
    于是锚题得分从 `10 × 1/3` 掉到 0，校正量必须正好是这个差。
    """
    j.step("基准 · 等值化校正（基线不漂 / 原始分不改写）")

    items = ["A-1", "A-2", "R-E0"]

    def _run(seed: int, answer: str) -> tuple[str, dict]:
        run_id = j.req(
            "POST",
            "/api/benchmark/runs",
            {
                "form": "quick",
                "variable_id": "V-SE",
                "channel": "measured",
                "method_id": "M-SE-04",
                "item_ids": items,
                "seed": seed,
            },
            expect=(200, 201),
        )[1]["run_id"]
        for item_id in items:
            j.req(
                "POST",
                f"/api/benchmark/runs/{run_id}/responses",
                {
                    "item_id": item_id,
                    "question_no": 0,
                    "kind": "key_points",
                    "judged_by": "llm",
                    "raw_answer": answer,
                },
                expect=(200, 201),
            )
        code, scored = j.req(
            "POST", f"/api/benchmark/runs/{run_id}/score", expect=(200, 422)
        )
        if code == 422:
            j.gap(
                f"计分端点返回 422：{json.dumps(scored, ensure_ascii=False)[:160]}"
                f"｜多半是题池缺规格字段（用 `--items-dir tests/fixtures/bank` 起服务）"
            )
        return run_id, scored

    first_id, first = _run(
        20260201, "我读到的：主线是这篇材料在讲某个过程，事实是有一个具体数据。"
    )
    if "equating" not in first:
        j.gap("`/score` 的响应里没有 `equating` —— 等值化还没有接上（P12）")
        return
    if first["equating"]["shift"] is not None:
        j.fail(
            f"第一次运行就给出了校正量 {first['equating']['shift']} —— "
            f"**没有基线就无从校正**；一个从没有过基线的系统会声称自己校正过了"
        )
        return
    if "没有基线" not in (first["equating"]["reason"] or ""):
        j.fail(f"第一次运行没有说明为什么不能校正：{first['equating']['reason']!r}")
        return

    baseline_scores = {
        row["item_id"]: row["item_score"]
        for row in first.get("items") or []
        if row["item_id"] in ("A-1", "A-2")
    }
    if any(not score for score in baseline_scores.values()):
        j.fail(f"第一次运行的锚题没算出分：{baseline_scores} —— 后面的差值就没有意义了")
        return

    # ---- 第二次：答案短到引文核对一条也认不出 → 锚题分掉到 0 ----
    second_id, second = _run(20260202, "忘了")
    equating = second.get("equating") or {}
    expected_shift = round(
        sum(baseline_scores.values()) / len(baseline_scores), 2
    )
    problems: list[str] = []
    if equating.get("shift") != expected_shift:
        problems.append(
            f"校正量是 {equating.get('shift')}，按「基线 − 本次」应当是 {expected_shift}"
            f"（基线 {baseline_scores}，本次锚题全 0）"
        )
    if len(equating.get("anchors") or []) != 2:
        problems.append(
            f"参与校正的锚题有 {len(equating.get('anchors') or [])} 道，应当是 2 道"
        )
    for anchor in equating.get("anchors") or []:
        if anchor.get("baseline") != baseline_scores.get(anchor.get("item_id")):
            problems.append(
                f"{anchor.get('item_id')} 的基线是 {anchor.get('baseline')}，"
                f"第一次运行算出来的是 {baseline_scores.get(anchor.get('item_id'))}"
            )
    if "假设" not in (equating.get("description") or ""):
        problems.append(
            f"校正的说明里没有那句假设：{equating.get('description')!r} —— "
            f"校正建立在「他对这几道锚题的能力与基线时相同」之上，"
            f"不说出来，一个校正过的分数与没校正过的长得一模一样"
        )
    if second.get("method_score") is None:
        problems.append("第二次运行没有方法分，没法验证校正落在哪里")
    elif second.get("equated_score") != round(
        min(10.0, second["method_score"] + expected_shift), 2
    ):
        problems.append(
            f"校正后的分是 {second.get('equated_score')}，"
            f"原始分 {second['method_score']} + 校正 {expected_shift}"
        )
    if problems:
        j.fail("；".join(problems))
        return

    # ---- 第三次：答案同样短，但基线必须仍然是**第一次**那一次 ----
    third_id, third = _run(20260203, "忘了")
    drifted = [
        f"{a['item_id']} 基线 {a['baseline']}"
        for a in (third.get("equating") or {}).get("anchors") or []
        if a.get("baseline") != baseline_scores.get(a.get("item_id"))
    ]
    if drifted:
        j.fail(
            f"第三次运行的基线变了（{'、'.join(drifted)}）—— "
            f"基线取的是最近一次而不是最早一次：**用它校正漂移的东西自己漂了**，"
            f"全部历史分数的可比性会同时失效，而每一行看起来都正常"
        )
        return
    if (third.get("equating") or {}).get("baseline_runs") != [first_id]:
        j.fail(
            f"基线来自 {(third.get('equating') or {}).get('baseline_runs')}，"
            f"应当是第一次运行 {first_id}（假设成不成立取决于那是多久以前，所以它必须被带出来）"
        )
        return

    # ---- 第一次的分**没有被改写** ----
    _, again = j.req("POST", f"/api/benchmark/runs/{first_id}/score", expect=(200,))
    if again.get("method_score") != first.get("method_score"):
        j.fail(
            f"重新计分之后第一次运行的方法分从 {first.get('method_score')} "
            f"变成了 {again.get('method_score')} —— 校正量被写进了历史分数，"
            f"于是「当时是多少分」不再有答案"
        )
        return

    j.ok(
        f"第一次运行：{baseline_scores}（无基线 → **不校正**，并说明为什么）✅；"
        f"第二次锚题掉到 0 → 校正 {expected_shift:+.2f}"
        f"（原始分 {second['method_score']} → 校正后 {second['equated_score']}，"
        f"**原始分不改写**）✅；第三次仍以第一次为基线，`baseline_runs=[{first_id}]` ✅；"
        f"说明里带着那句假设 ✅"
    )


def step_benchmark_offline_form(j: Journey) -> None:
    """离线跑一整卷：出卷 → 作答 → 落库 → 计分（P15 的 `tools/runbench.py`）。

    ## 这一步要证明什么

    基准块的每一段机制各自都有端点，也有单测；**但没有一条把它们串成一次
    真实的考试**。缺了这条串，"机制做完了"这句话没有验证面 ——
    而这条串正是用户会亲手用一次的东西（他要花 30 / 60 分钟做一卷）。

    三步，每步都在真服务上跑：

    ① **出卷**：抽题 + 开一次运行 + 导出一份空卷子。
       ★ 空卷子必须是空的 —— 一个替你答题的工具产出的分数，
       在库里与"他真的答了"长得一样。
    ② **交卷**：逐题落库 → 计分 → 报告。
       ★ 只答了 3 道题、另 3 道留空：报告里**没答的问不进读数**
       （它们是「没测到」，不是 0 分）。
    ③ **报告里 `0.0` 与「没测到」是两种写法**，而等值化那句话由
       **当场算出来的**结果来说（不是一句写死的"尚未实现"）。

    ★ 作答内容是**验证用的冒充**：从夹具材料里抄那三句证据句。
    这一步验的是**链路**，不是"某个人的学习方法测量结果"。
    """
    j.step("基准 · 离线跑一整卷（runbench：出卷 → 交卷 → 分数报告）")

    if not j.BANK_ITEMS_DIR.exists():
        j.gap(f"没有题库目录 {j.BANK_ITEMS_DIR} —— 出不了卷（内容缺口，不是机制缺口）")
        return

    from tools import runbench  # noqa: PLC0415 - 只有这一步要

    with tempfile.TemporaryDirectory() as workdir:
        paper_path = Path(workdir) / "paper.json"
        common = ["--base-url", j.base, "--items-dir", str(j.BANK_ITEMS_DIR)]

        # ① 出卷
        code = runbench.main(
            [
                *common,
                "--form",
                "quick",
                "--variable",
                "V-SE",
                "--channel",
                "measured",
                "--method",
                "M-SE-04",
                "--seed",
                "20260104",
                "--out",
                str(paper_path),
            ]
        )
        if code != 0 or not paper_path.is_file():
            j.fail(f"出卷失败（退出码 {code}）—— 卷子没写出来")
            return
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        run_id = str(paper.get("run_id") or "")
        questions = paper["questions"]
        if not run_id or len(questions) < 4:
            j.fail(f"卷子不成形：run_id={run_id!r}，{len(questions)} 题")
            return
        # ★ 空卷子必须是空的
        filled = [q["item_id"] for q in questions if any((q.get("answers") or {}).values())]
        if filled:
            j.fail(f"★ 卷子出厂就带着答案：{filled} —— 那是它替人答了题")
            return
        if any(q.get("missing_item") for q in questions):
            j.fail(
                "★ 卷子里有题在题库目录里找不到："
                f"{[q['item_id'] for q in questions if q.get('missing_item')]}"
            )
            return

        # ② 只答前 3 道，其余留空
        answered_items = questions[:3]
        for question in answered_items:
            lines = [
                line.strip()
                for line in str(question.get("material") or "").splitlines()
                if "证据句" in line
            ]
            if len(lines) < 3:
                j.fail(
                    f"夹具材料里没有三句证据句（{question['item_id']}）—— "
                    "这一步的冒充作答是照它写的"
                )
                return
            question["answers"] = dict(question.get("answers") or {})
            question["answers"]["0"] = "；".join(lines[:3])
            for prompt in question.get("prompts") or []:
                if prompt.get("kind") == "exact":
                    options = list(prompt.get("options") or [])
                    if options:
                        question["answers"][str(prompt["question_no"])] = options[0]
        paper_path.write_text(
            json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        code = runbench.main(["--base-url", j.base, "--paper", str(paper_path)])
        if code != 0:
            j.fail(f"交卷失败（退出码 {code}）")
            return

    # ③ 报告：没答的问**不进读数**，而方法分不是 0
    code, score = j.req("POST", f"/api/benchmark/runs/{run_id}/score")
    if code != 200:
        j.fail(f"计分失败（{code}）：{score}")
        return
    answered = sum(len(q.get("answers") or {}) for q in answered_items)
    rows = len(score.get("items") or [])
    if rows != answered:
        j.fail(
            f"★ 计分结果里有 {rows} 条，而真正答了的只有 {answered} 个问 —— "
            "没答的题被当成「作答过」了（那会让「没测到」看起来像 0 分）"
        )
        return
    if score.get("method_score") is None:
        j.fail(
            "3 道题都答了却没有方法分："
            f"{score.get('confidence_reason')}（这一步验的是链路，不该缺分）"
        )
        return
    report = runbench.render_report(score)
    if "等值化" not in report:
        j.fail("报告里没有等值化那句话 —— 它必须回答「这次校正了没有」")
        return

    j.ok(
        f"出卷 {run_id}（{len(questions)} 题，出厂无答案）→ 答 3 题 → "
        f"落库 {rows} 条 → 方法分 {score['method_score']}；"
        f"没答的 {len(questions) - 3} 题不进读数（没测到，不是 0）"
    )


def step_benchmark_remaining_gaps(j: Journey) -> None:
    """⑥-5 基准块**还没做**的部分，逐项确认它不存在。

    缺口不算失败 —— 但必须列出来，否则"能跑一次运行"会被读成
    "基准测试做完了"。这两件事差得很远。
    """
    j.step("基准 · 尚未实现的部分（缺口，不是失败）")
    code, adv = j.req("GET", "/api/m1/V-SE/benchmark-advisory", expect=(200, 404))
    notes = []
    notes.append(
        "基准块的**机制**已经齐了（抽题 / 计分 / 要点判官 / 自评 / 隐性参数 / 分段说明 / 等值化）。"
        "剩下这两件都**不属于机制**，所以它们不会因为再写代码而消失："
    )
    notes.append(
        "① 各变量的**仪器改造**：`auditmethods` 报的 `measured` 那批格子还是空的 —— "
        "把正误题退为标尺、读数改由编码题与行为量产出，是**测量设计决定**"
    )
    notes.append(
        "② 隐性参数在**真实题目**上的使用面：目前只有夹具里那一道观测题用 `switch_count`。"
        "哪些题该看犹豫、哪些该看求助，同样要等仪器改造一起定"
    )
    j.gap(
        "；".join(notes)
        + f"｜咨询接口可用：{json.dumps(adv, ensure_ascii=False)[:70]}"
    )


def step_m1_reads(j: Journey) -> None:
    j.step("M1：变量表 / 观测 / 历史 / 回滚")
    vs = j.items("/api/m1/variables")
    rows = vs["value"] if isinstance(vs, dict) else vs
    obs = j.items("/api/m1/V-SE/observations")
    hist = j.items("/api/m1/V-SE/history")
    nobs = len(obs["value"] if isinstance(obs, dict) else obs or [])
    nhist = len(hist["value"] if isinstance(hist, dict) else hist or [])
    channels = sum(len(v.get("channels") or []) for v in rows) if rows and isinstance(rows[0], dict) else 0
    j.ok(f"{len(rows)} 个变量 / {channels} 个通道；V-SE 观测 {nobs} 条、历史 {nhist} 条")


def step_preferences_no_review(j: Journey) -> None:
    """④ 偏好直接写入、**不检测冲突**（用户明确要求）。"""
    j.step("偏好：直接写入、不审冲突")
    prefs0 = j.items("/api/preferences")
    p0 = prefs0["value"] if isinstance(prefs0, dict) else prefs0
    _, a = j.req(
        "POST",
        "/api/preferences",
        {"statement": "绝对不会通过看长视频的方式学习", "priority": 10},
        expect=(200, 201),
    )
    # 故意写一条与上一条**语义冲突**的偏好 —— 用户要求它不该被拦
    code, b = j.req(
        "POST",
        "/api/preferences",
        {"statement": "看长视频是我最有效的学习方式", "priority": 10},
        expect=(200, 201, 409, 422),
    )
    prefs1 = j.items("/api/preferences")
    p1 = prefs1["value"] if isinstance(prefs1, dict) else prefs1
    if code in (200, 201) and len(p1) >= len(p0) + 2:
        j.ok(f"两条冲突偏好都被直接写入（{len(p0)} → {len(p1)} 条），未做冲突审查 ✅")
    elif code in (409, 422):
        j.fail(f"冲突偏好被拒（{code}）—— 与「偏好不应检测冲突」的要求不符：{b}")
    else:
        j.fail(f"偏好写入结果不明：{code} {b}")


def step_attachments(j: Journey) -> None:
    j.step("会话资料：上传 / 读 / 删")
    content = "# 验证用资料\n\n这是一份端到端验证附件。\n".encode("utf-8")
    _, up = j.req(
        "POST",
        f"/api/sessions/{j.session}/attachments",
        raw=content,
        query={"filename": "e2e_verify.md"},
        expect=(200, 201),
    )
    aid = up.get("attachment_id") if isinstance(up, dict) else None
    _, lst = j.req("GET", f"/api/sessions/{j.session}/attachments")
    rows = lst.get("items", []) if isinstance(lst, dict) else lst
    if aid:
        _, one = j.req(
            "GET", f"/api/sessions/{j.session}/attachments/{aid}", expect=(200, 404)
        )
        j.req("DELETE", f"/api/sessions/{j.session}/attachments/{aid}", expect=(200, 204))
    j.ok(f"上传成功（{aid}），列表 {len(rows)} 份，读回与删除均可用")


def step_c_lifecycle(j: Journey) -> None:
    """⑫ C 家族：想试 / 试过 的语义格 + **完整的条目生命周期**。

    ## 这一步在两轮里长出来

    P7 拆的是「可信度」：同一个句子从 `/api/hypotheses` 进是 `stated`、
    从 `/api/evidence` 进是 `experienced`，而缺场景的倾向性句子被 422 拒绝。

    P7.1 补的是「处置权」：用户裁定「解释权永远在用户手里」，
    于是两族都拿到了 归档 / 编辑 / 恢复 三条路径。

    ## 为什么这些必须端到端验，而不是靠单测

    单测能证明判定表拒绝某格、policy 接受了某个值。它们证明不了的是
    **HTTP 这一层有没有把动作接对**：`DELETE` 有没有真的落在归档上、
    `PUT` 改完之后列表里出现的是新值还是旧值、
    删掉之后它是不是真的从列表里消失了。这些只有真服务 + 真 HTTP 看得见。
    """
    j.step("C 家族：语义格 + 条目生命周期（删 / 改 / 恢复）")
    payload = {
        "statement": "背单词想试试 Anki 的间隔重复",
        "condition": "背单词时",
        "effect": "用 Anki 的间隔重复",
    }

    # ① 假说端点：R1 + stated → 直接生效
    created = j.req("POST", "/api/hypotheses", payload, expect=(200, 201))[1]
    results = created.get("results") or []
    assert results and results[0].get("status") == "applied", (
        f"假说没有直接生效：{results[0].get('status') if results else '没有 result'}"
    )

    # ② 读得回来，且 basis 是 stated
    listed = j.items("/api/hypotheses")
    rows = listed.get("value", listed) if isinstance(listed, dict) else listed
    mine = [row for row in rows if row.get("condition") == "背单词时"]
    assert mine, "刚写的假说读不回来"
    assert mine[0].get("basis") == "stated", (
        f"假说的 basis 应该是 stated，实际是 {mine[0].get('basis')}"
    )
    hypothesis_id = mine[0]["hypothesis_id"]
    assert hypothesis_id.startswith("H-"), f"假说 id 前缀不对：{hypothesis_id}"

    # ③ 同一个句子从证据端点进 → basis 是 experienced（**两个动作，不是一个加参数**）
    ev_created = j.req(
        "POST",
        "/api/evidence",
        {**payload, "evidence_strength": "medium"},
        expect=(200, 201),
    )[1]
    ev_results = ev_created.get("results") or []
    assert ev_results and ev_results[0].get("status") == "applied", (
        f"同一句子写成证据没有生效：{ev_results[0].get('status') if ev_results else '没有'}"
    )
    ev_listed = j.items("/api/evidence", query={"keyword": "Anki"})
    ev_rows = (
        ev_listed.get("value", ev_listed) if isinstance(ev_listed, dict) else ev_listed
    )
    assert ev_rows and ev_rows[0].get("basis") == "experienced", (
        f"证据的 basis 应该是 experienced，实际是 "
        f"{ev_rows[0].get('basis') if ev_rows else '（没有）'}"
    )
    evidence_id = ev_rows[0]["evidence_id"]

    # ④ 缺场景的倾向性句子进不了做法表（**结构判据**）
    j.req(
        "POST",
        "/api/hypotheses",
        {
            "statement": "我发现自己很喜欢在安静的环境下学习",
            "condition": "   ",
            "effect": "在安静的地方学",
        },
        expect=(422,),
    )

    # ⑤ 侧边栏上下文里带着它，而且被标成「未验证」
    context = j.req("GET", "/api/sidebar/context")[1]
    rendered = context.get("rendered") or ""
    assert "假说" in rendered and "未验证" in rendered, (
        "侧边栏上下文里没有「假说 / 未验证」这一节"
    )
    assert "Anki" in rendered or "间隔重复" in rendered, (
        "刚写的假说没有出现在常驻模型的上下文里"
    )

    # ------------------------------------------------------------------ #
    # ⑥ P7.1：改 / 删 / 恢复（真实 HTTP）
    # ------------------------------------------------------------------ #
    revised = j.req(
        "PUT",
        f"/api/hypotheses/{hypothesis_id}",
        {
            "statement": "背单词想试试 Anki，每天不超过 20 张",
            "condition": "每天背单词时",
            "effect": "用 Anki，上限 20 张",
            "reason": "加个上限",
        },
        expect=(200,),
    )[1]
    assert (revised.get("results") or [{}])[0].get("status") == "applied", (
        f"编辑假说没有生效：{revised}"
    )
    after_edit = j.items("/api/hypotheses")
    edited = [
        row
        for row in (after_edit if isinstance(after_edit, list) else [])
        if row.get("hypothesis_id") == hypothesis_id
    ]
    assert edited and edited[0]["effect"] == "用 Anki，上限 20 张", (
        "编辑之后列表里还是旧值（或者条目不见了）"
    )

    # 删：从默认列表消失
    j.req("DELETE", f"/api/hypotheses/{hypothesis_id}", expect=(200,))
    visible = j.items("/api/hypotheses")
    assert all(row.get("hypothesis_id") != hypothesis_id for row in visible), (
        "★ 归档之后它还在默认列表里 —— 「删除」这个动作没有后果"
    )
    # 但**行还在**（I2 要求日志指向真实地址）
    archived = j.items("/api/hypotheses", query={"include_archived": True})
    archived_rows = [r for r in archived if r.get("hypothesis_id") == hypothesis_id]
    assert archived_rows and archived_rows[0]["status"] == "archived", (
        "★ 行被物理删掉了 —— 那条 applied 日志现在指向空气，"
        "而 I2 的全部意义就是「改了什么都能追回来」"
    )

    # 恢复：回到默认列表，id 不变
    j.req("POST", f"/api/hypotheses/{hypothesis_id}/restore", expect=(200,))
    back = j.items("/api/hypotheses")
    restored = [r for r in back if r.get("hypothesis_id") == hypothesis_id]
    assert restored and restored[0]["status"] == "active", "恢复之后它没有回到默认列表"

    # ⑦ 证据那一边：归档必须让它**真的退出装配**
    j.req("DELETE", f"/api/evidence/{evidence_id}", expect=(200,))
    gone = j.items("/api/evidence", query={"keyword": "Anki"})
    gone_rows = gone.get("value", gone) if isinstance(gone, dict) else gone
    assert all(r.get("evidence_id") != evidence_id for r in gone_rows), (
        "★ 归档掉的证据还在列表里 —— 它仍然会被装配当成依据"
    )
    j.req("POST", f"/api/evidence/{evidence_id}/restore", expect=(200,))
    back_ev = j.items("/api/evidence", query={"keyword": "Anki"})
    back_rows = back_ev.get("value", back_ev) if isinstance(back_ev, dict) else back_ev
    assert any(r.get("evidence_id") == evidence_id for r in back_rows), (
        "证据恢复之后没有回到列表"
    )

    # ⑧ 不存在的 id → 404（不落日志）
    j.req("DELETE", "/api/hypotheses/H-9999", expect=(404,))

    j.ok(
        "假说 basis=stated｜同一句写成证据 basis=experienced｜"
        "缺场景被 422｜侧边栏标「未验证」｜"
        "编辑生效且 id 不变｜删除后从列表消失但行还在｜恢复回去｜证据归档后退出装配｜假 id 404"
    )


def step_evidence(j: Journey) -> None:
    j.step("证据库：写 / 读")
    # 枚举是英文：strength=strong|medium|weak，source=user_direct|session_derived
    _, created = j.req(
        "POST",
        "/api/evidence",
        {
            "statement": "短时高频复习比一次性长时复习记得更牢",
            "condition": "备考类记忆任务",
            "effect": "正",
            "evidence_strength": "medium",
            "source": "user_direct",
        },
        expect=(200, 201),
    )
    found = j.items("/api/evidence", query={"keyword": "复习"})
    rows = found.get("value", found) if isinstance(found, dict) else found
    j.ok(f"写入成功，按关键词可检索到 {len(rows)} 条")


#: 全部列表端点。**加一个列表端点就要加到这里** ——
#: 这份清单是"统一形状"这件事唯一的执行点。
LIST_ENDPOINTS = (
    # 全局域
    "/api/preferences",
    "/api/evidence",
    "/api/hypotheses",
    "/api/proposals",
    "/api/router-logs",
    "/api/m1/variables",
    "/api/m1/V-SE/history",
    "/api/m1/V-SE/observations",
    "/api/llm/providers",
    "/api/llm/profiles",
    "/api/benchmark/runs",
    "/api/benchmark/domains/history",
)

#: 沙箱域的列表端点（带会话 id，所以要拼）—— 同一个信封，**同一段检查**。
#: 分开写只是因为它需要 f-string；判据一个字都没变。
LIST_ENDPOINTS_SESSION = (
    "/api/sessions",
    "/api/folders",
    "/api/sessions/{sid}/attachments",
    "/api/sessions/{sid}/annotations",
    "/api/sessions/{sid}/clarifications",
    #: ★ 复数是"写下的那些感想"；单数 `/reflection` 是**指引**（不是列表）。
    #: 两条路径刻意不合并 —— 合并会得到一个"GET 有时返回指引、有时返回列表"的端点。
    "/api/sessions/{sid}/reflections",
    "/api/sessions/{sid}/input/revisions",
    "/api/sessions/{sid}/motivation/revisions",
    "/api/sessions/{sid}/plan/revisions",
)


def step_sidebar_organize(j: Journey) -> None:
    """侧栏整理：改名 / 归档 / 手动排序 / 删除 —— **文件夹与会话两套**。

    这一步刻意**不碰 `j.session`**：它自己建一个临时文件夹，用完删掉。
    两个好处：① 可以重复跑；② 不会让后面几步看到的数据跟着变
    （e2e 的步骤之间本来是共享一个库的，改别人的东西会让失败很难定位）。

    验的是四件在单测里能过、在真服务上却经常不通的事：
    路径拼对了没有、`PATCH` 的合并语义（不传 ≠ 传 null）、
    删除是不是真的级联、以及**归档没有顺手改状态**。
    """
    j.step("侧栏整理（文件夹/会话：改名、归档、手动排序、删除）")
    fid = ""
    try:
        _, folder = j.req("POST", "/api/folders", {"name": "临时 · 侧栏整理"}, expect=201)
        fid = folder["folder_id"]
        assert "sort_order" in folder, f"新建文件夹没返回 sort_order：{sorted(folder)}"

        _, first = j.req(
            "POST", "/api/sessions", {"folder_id": fid, "title": "甲"}, expect=201
        )
        _, second = j.req(
            "POST", "/api/sessions", {"folder_id": fid, "title": "乙"}, expect=201
        )
        sid = first["session_id"]

        # ① 改名（文件夹 + 会话）
        _, renamed = j.req(
            "PATCH", f"/api/folders/{fid}", {"name": "临时 · 改过名"}
        )
        assert renamed["name"] == "临时 · 改过名", renamed
        _, retitled = j.req(
            "PATCH", f"/api/sessions/{sid}", {"title": "甲 · 改过名"}
        )
        assert retitled["title"] == "甲 · 改过名", retitled
        # ★ 改名**不动 version**（否则侧栏改名会把正在进行的对话写撞成 409）
        assert retitled["version"] == first["version"], (
            f"改名动了 version：{first['version']} → {retitled['version']}"
        )

        # ② 空 PATCH 与"想改 state"都必须被拒
        code, _ = j.req("PATCH", f"/api/folders/{fid}", {}, expect=(422,))
        code2, _ = j.req(
            "PATCH", f"/api/sessions/{sid}", {"state": "completed"}, expect=(422,)
        )

        # ③ 归档：默认列表看不到，带 include_archived 看得到，且**状态不变**
        before_state = j.req("GET", f"/api/sessions/{sid}/state")[1]["session"]["state"]
        j.req("PATCH", f"/api/folders/{fid}", {"archived": True})
        visible = [row["folder_id"] for row in j.items("/api/folders")]
        assert fid not in visible, "归档的文件夹还在默认列表里"
        hidden = [
            row["folder_id"]
            for row in j.items("/api/folders?include_archived=true")
        ]
        assert fid in hidden, "include_archived=true 也看不到归档的文件夹"
        after_state = j.req("GET", f"/api/sessions/{sid}/state")[1]["session"]["state"]
        assert before_state == after_state, (
            f"归档把会话状态改了：{before_state} → {after_state}"
        )
        j.req("PATCH", f"/api/folders/{fid}", {"archived": False})

        # ④ 手动排序：把第二条挪上来
        _, moved = j.req("PATCH", f"/api/sessions/{second['session_id']}", {"move": "up"})
        order = [row["session_id"] for row in j.items(f"/api/sessions?folder_id={fid}")]
        assert order == [second["session_id"], sid], f"挪动之后顺序是 {order}"

        # ⑤ 删除：会话先删（数量要如实报），文件夹再删（连带会话）
        _, deleted_session = j.req("DELETE", f"/api/sessions/{second['session_id']}")
        assert deleted_session == {
            "deleted_sessions": 1,
            "deleted_chat_messages": 0,
        }, f"删除会话的返回不对：{deleted_session}"
        j.req("GET", f"/api/sessions/{second['session_id']}/state", expect=(404,))

        _, deleted_folder = j.req("DELETE", f"/api/folders/{fid}")
        fid = ""  # 已经删掉了，别再在 finally 里删一次
        assert deleted_folder == {
            "deleted_sessions": 1,
            "deleted_folders": 1,
        }, f"删除文件夹的返回不对：{deleted_folder}"
        j.req("GET", f"/api/sessions/{sid}/state", expect=(404,))

        j.ok(
            "改名不进 version；空 PATCH→422；改 state→422；"
            "归档后默认列表隐藏且状态不变；move up 生效；"
            "删除会话/文件夹各自报出连带数量"
        )
    finally:
        # 失败也要把临时文件夹收拾掉，别给后面几步留垃圾。
        if fid:
            j.req("DELETE", f"/api/folders/{fid}", expect=(200, 404))


def step_usage_readings(j: Journey) -> None:
    """用量读数：**界面那一排数字和后端说的一致**。

    ## 为什么这件事要在端到端验

    右下角那排数字（上下文环、token 总量、缓存命中）是**前端自己算的**：
    它把 `/state` 里每条 `chat` 的实测值相加。这个算法有两个前提，
    单测各自都过、拼在一起却可能不成立：

    1. `/state` 真的把 `prompt_tokens` / `completion_tokens` /
       `cache_hit_tokens` / `cache_miss_tokens` 四个键都交出来（缺一个，
       前端的求和会静默地少算一类，而界面照样显示一个数字）；
    2. 提供方不报缓存时，那两个键是 **`null` 而不是 0**（前端据此显示
       「没测到」，而 0% 是另一句话）。

    还有第三条只有真服务才会暴露：**对话那一轮真的落库了用量**。
    测试替身不一定报，所以这里只要求"键在"，不要求"值非空" ——
    这条边界写清楚，才不会为了让它变绿去伪造一个用量。
    """
    j.step("用量读数（/state 交出四个键；没测到时是 null 而不是 0）")
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")

    chat = state["chat"]
    assert chat, "这个会话没有对话 —— 前面那几步应该已经聊过几轮了"

    keys = {"prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens"}
    for index, message in enumerate(chat):
        missing = keys - set(message)
        assert not missing, f"第 {index} 条消息缺这些键：{sorted(missing)}（有：{sorted(message)}）"
        for name in ("cache_hit_tokens", "cache_miss_tokens"):
            value = message[name]
            assert value is None or isinstance(value, int), (
                f"第 {index} 条消息的 {name} 既不是 null 也不是整数：{value!r}"
            )

    # 上下文环读的是"最近一次回复实测的 prompt tokens"。
    measured = [m for m in chat if m.get("prompt_tokens") is not None]
    assert measured, "一条带 prompt_tokens 的回复都没有 —— 用量面板会一直是「没测到」"

    # 命中数不能凭空大于 prompt（这是唯一能在这里查的一致性事实）。
    for message in chat:
        hit = message.get("cache_hit_tokens")
        prompt = message.get("prompt_tokens")
        if hit is not None and prompt is not None:
            assert hit <= prompt, f"命中数比 prompt 还大：{hit} > {prompt}"

    j.ok(
        f"{len(chat)} 条消息都带四个用量键；缓存读数缺失时是 null；"
        f"{len(measured)} 条有实测 prompt；命中数不超过 prompt"
    )


def step_response_shape_consistency(j: Journey) -> None:
    """★ 列表端点的响应包装**必须统一为** `{"items": [...]}`。

    ## 这一步原先报的是"不一致"，现在钉的是"统一"

    它第一次跑出来时发现三种形状并存：`{"value": [...]}`、裸 `[...]`、
    `{"items": [...]}`。那时的判定是"混用了 N 种"，只能说**有问题**，
    说不出**该是什么样** —— 于是它可以一直红着，而没人知道往哪个方向改。

    统一之后这一步反过来：**任何一个端点不是 `{"items": [...]}` 就红**。
    这才是一条能拦住回归的检查 —— 而"不一致"本身不是需求。

    ## 为什么统一值得做（而不是"风格问题"）

    1. 调用方不必为每个端点记住一种形状。看错的表现是
       `data.map is not a function` —— 一个离出错点很远的运行时错误；
    2. 裸数组没地方放"关于这个列表"的信息（总数 / 下一页 / 用了什么过滤），
       要加任何一件都是破坏性变更；
    3. 现在改只动后端 —— 前端还没写。等前端写完再改就是一次迁移。

    ## 为什么端到端验而不是靠单测

    单测各自对着自己的端点写，每个都"符合自己的约定"；
    **只有把 13 个端点放在同一段代码里逐个比对，才能发现它们不一致。**
    """
    j.step("列表端点响应形状统一（{\"items\": [...]}）")
    shapes: dict[str, str] = {}
    paths = list(LIST_ENDPOINTS) + [
        raw.format(sid=j.session) for raw in LIST_ENDPOINTS_SESSION
    ]
    for path in paths:
        code, body = j.req("GET", path, expect=(200,))
        # ★ 判据是「**数组在 items 下**」，不是「信封里只有 items」。
        #   /api/sessions/{id}/attachments 合法地带着 session_id 兄弟字段 ——
        #   而信封能装兄弟字段正是引入信封的**理由**（总数、下一页、过滤条件）。
        #   第一版我写成了 set(body) == {"items"}，于是它被自己的设计判成了缺陷。
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            extra = sorted(set(body) - {"items"})
            suffix = f"(+{','.join(extra)})" if extra else ""
            shapes[path] = f"items×{len(body['items'])}{suffix}"
        elif isinstance(body, list):
            shapes[path] = "★ 裸列表"
        elif isinstance(body, dict):
            shapes[path] = "{" + ",".join(sorted(body)[:3]) + "}"
        else:
            shapes[path] = type(body).__name__

    bad = sorted(p for p, s in shapes.items() if not s.startswith("items×"))
    detail = "; ".join(f"{p} → {s}" for p, s in shapes.items())
    if not bad:
        j.ok(f"{len(shapes)} 个列表端点全部是 {{\"items\": [...]}}；{detail}")
    else:
        j.fail(
            f"★ {len(bad)} 个列表端点没走统一信封：{', '.join(bad)}｜{detail}"
        )


def step_chat_tool_trace(j: Journey) -> None:
    """⑧ 模型输出要能展示**工具调用过程与具体输出**（用户明确要求）。

    这条容易被误判成"没做"，因为讨论接口只返回一句话。
    实际轨迹在 `chat` 消息的 `tool_trace` 字段上（`TraceItem`：
    step/call_id/tool/scope/arguments/status/message/duration_ms）。
    """
    j.step("对话消息的工具调用轨迹")
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    chat = state.get("chat") or []
    traced = [m for m in chat if m.get("tool_trace")]
    if not chat:
        j.gap("state.chat 为空 —— 没有可供检查的对话消息")
        return
    if not traced:
        j.gap(f"{len(chat)} 条消息都没有 tool_trace（该会话这一轮没调工具）")
        return
    items = traced[-1]["tool_trace"]
    keys = sorted(items[0].keys()) if items and isinstance(items[0], dict) else []
    j.ok(
        f"{len(chat)} 条消息中 {len(traced)} 条带轨迹；"
        f"最近一次 {len(items)} 个 TraceItem（字段：{', '.join(keys)}）"
    )


def step_scale_explanations(j: Journey) -> None:
    """⑨ 每个参数旁边要有「0-4 分说明什么」这类分段说明（用户要求，P10）。

    ★ 这一步不满足于"字段存在"。它要求三段**首尾相接覆盖 0-10**，
    并且两端与锚点**逐字一致**：

    - 表里有洞或重叠 → 某些分数没有说明（或有两个答案），
      而界面看起来都只是"这一段没写"；
    - 分段说明与锚点各写一套 → 同一个变量上出现两种自相矛盾的描述。

    这两种错都不会报错，只会让用户觉得"这个系统说话前后不一致"。
    """
    j.step("分值的分段说明（0-4 / 4-8 / 8-10）")
    vs = j.items("/api/m1/variables")
    rows = vs.get("value", vs) if isinstance(vs, dict) else vs
    if not rows:
        j.fail("变量表为空")
        return
    problems: list[str] = []
    drafts = 0
    for row in rows:
        bands = row.get("scale_bands") or []
        if len(bands) != 3:
            problems.append(f"{row.get('variable_id')} 有 {len(bands)} 段说明，不是 3 段")
            continue
        ordered = sorted(bands, key=lambda band: band["low"])
        if ordered[0]["low"] != 0 or ordered[-1]["high"] != 10:
            problems.append(f"{row.get('variable_id')} 的三段没有覆盖 0-10")
            continue
        if any(a["high"] != b["low"] for a, b in zip(ordered, ordered[1:])):
            problems.append(f"{row.get('variable_id')} 的三段之间有洞或重叠")
            continue
        if ordered[0]["description"] != row.get("scale_low_anchor"):
            problems.append(f"{row.get('variable_id')} 的低端说明与锚点不一致")
        if ordered[-1]["description"] != row.get("scale_high_anchor"):
            problems.append(f"{row.get('variable_id')} 的高端说明与锚点不一致")
        drafts += 1 if ordered[1].get("draft") else 0
    if problems:
        j.fail("；".join(problems[:4]))
        return
    if drafts != len(rows):
        j.gap(
            f"{drafts}/{len(rows)} 个变量的中段说明标了「初稿」——"
            f"中段没有现成锚点可引用，它是这一轮新写的文案，待用户校订"
        )
        return
    j.ok(
        f"{len(rows)} 个变量各有三段说明（0-4 / 4-8 / 8-10），首尾相接覆盖 0-10，"
        f"两端与量表锚点逐字一致；中段都标了「初稿」（解释权在用户手里）✅"
    )


def step_context_memory(j: Journey) -> None:
    """⑩ 对话记忆：实测用量、自动压缩、思考过程留档（P6+.6）。

    ★ **`/discuss` 必须真的调用过一次模型**，否则没有 `prompt_eval_count`，
    而整个机制的触发条件正是那个读数。这条检查因此**依赖前面的讨论步骤**
    （`step_discuss_changes_plan` 跑过就够）。替身现在也会报用量
    （见 `FakeLLM._fabricate_meta`），所以用 `--llm fake` 时底下那条能成立。

    **一个步骤只给一个结论**（`j.ok` / `j.gap` / `j.fail` 各算一条），
    所以这里先把所有发现收齐再决定说什么 —— 早先的写法一次吐三行
    （一条 ok + 两条 gap），在汇总表里会变成一个自相矛盾的步骤。
    """
    j.step("对话记忆机制（用量 / 压缩 / 思考过程）")
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    chat = state.get("chat") or []
    if not chat:
        j.gap("state.chat 为空 —— 前面没有可供检查的对话")
        return

    assistants = [m for m in chat if m.get("role") == "assistant"]
    if not assistants:
        j.gap("没有助手消息 —— 无法检查用量回填")
        return

    # 思考过程：字段必须在（哪怕是 null）。不思考的模型就是没有思考过程，
    # 那是正常情况 —— 但「字段根本不存在」与「这一轮没思考」是两回事。
    if "thinking" not in assistants[-1]:
        j.fail("助手消息没有 `thinking` 字段 —— 思考过程没有落库的位置")
        return

    measured = [m for m in assistants if m.get("prompt_tokens")]
    if not measured:
        j.gap(
            f"{len(assistants)} 条助手消息都没有 prompt_tokens —— "
            "用量没回填（`/discuss` 没成功调过模型？）"
        )
        return

    last = measured[-1]
    facts = [
        f"{len(measured)}/{len(assistants)} 条助手消息带实测用量，"
        f"最近 {last['prompt_tokens']} token（completion {last.get('completion_tokens')}）",
        "`thinking` 字段就位",
    ]
    # ★ 硬截断的回归探针。`HISTORY_LIMIT = 6` 已经删掉；若它被重新引入，
    # 这里会在对话变长之后立刻发现「早期的话不见了」。
    facts.append(
        f"对话 {len(chat)} 条（超过旧的 6 条上限 → 没有硬截断）"
        if len(chat) > 6
        else f"对话 {len(chat)} 条（还不够长，测不出「不再硬截断」）"
    )
    # 压缩本身要等窗口真的用满（本地替身永远用不满），所以这里**测不到**
    # 触发路径 —— 如实说明，而不是假装它验过了。触发与折叠由
    # tests/agent/test_context_memory.py 的 18 条测试覆盖。
    facts.append("压缩触发/折叠未走（本地替身用不满窗口）→ 由 18 条单元测试覆盖")
    j.ok("；".join(facts))


def step_sidebar_storage(j: Journey) -> None:
    """⑪ 侧边栏常驻模型：能聊、有记忆、共用计量、**改不了会话**（P6+.6）。

    ## 这一步在验什么

    侧边栏此前只有存储层（`sidebar_message` 表），8 条需求全卡在"没有端点"上。
    现在它是**一个功能**了，所以这一步从 ⚠️ 变成真正的验收：

    1. **空库能开** —— 常驻的界面在没有任何会话时必须能用；
    2. **能聊** —— 一次讨论落两条消息、带实测用量；
    3. **有记忆** —— 历史全在，不是"最近 6 条"；
    4. **计量与会话同形** —— 两边本来就是同一份代码；
    5. ★ **改不了会话** —— 会话工具**不在它的清单里**，这是最关键的一条：
       侧边栏不是"小一号的会话"，它连那些工具都看不见。

    ## 为什么第 5 条要以"清单里没有"的形式验，而不是"调了会被拒"

    那是两层防线里较强的一层。第二层（硬编工具名会被拒）需要模型配合，
    而这里用的是替身 LLM，它不会主动去编 `retrieve_session`。
    第二层由 `tests/api/test_sidebar_api.py` 里那条专门的测试覆盖 ——
    那里能精确构造出"模型硬要调"的情形。**两层都要有人验，
    但验法不同**：这一层验"看不见"，那一层验"调了也没用"。
    """
    j.step("侧边栏常驻模型（常驻 / 记忆 / 计量 / 边界）")

    state = j.req("GET", "/api/sidebar/state")[1]
    tools = state.get("tools") or []
    # ★ 这一条不是"形状对不对"，是**功能边界**：侧边栏不能碰任何会话。
    leaked = [name for name in tools if name in _SESSION_ONLY_TOOLS]
    if leaked:
        j.fail(
            f"侧边栏的工具清单里出现了会话工具：{leaked} —— "
            "侧边栏不绑定任何会话，这些工具在那里只会查到一个不存在的会话"
        )
        return
    if not state.get("sections"):
        j.fail("侧边栏的 state 没有 sections —— 界面无从解释它看到了什么")
        return

    history_before = len(state.get("messages") or [])
    reply = j.req("POST", "/api/sidebar/discuss", {"message": "我最近背单词很痛苦"})[1]
    if not reply.get("reply"):
        j.fail("侧边栏没有回复")
        return

    after = j.req("GET", "/api/sidebar/state")[1]
    messages = after.get("messages") or []
    if len(messages) != history_before + 2:
        j.fail(
            f"一次讨论该落两条消息，实际从 {history_before} 变成 {len(messages)}"
        )
        return

    meter = reply.get("context") or {}
    if not meter.get("measured"):
        j.fail("侧边栏的计量没有读数 —— `prompt_tokens` 没回填")
        return
    # ★ 计量形状与会话侧**逐字段相同** —— 两边共用 `ContextMeter.as_dict()`。
    session_shape = {
        "used",
        "limit",
        "live_messages",
        "compacted_messages",
        "summaries",
        "compact_count",
        "compactable",
        "measured",
        "ratio",
        "remaining",
        "pressure",
    }
    missing = sorted(session_shape - set(meter))
    if missing:
        j.fail(f"侧边栏的计量漏了字段：{missing} —— 它会与会话侧的形状分叉")
        return

    # ★ 上下文里必须**明说**它没有计划 —— 这是唯一说明"缺了什么"的地方。
    rendered = j.req("GET", "/api/sidebar/context")[1].get("rendered") or ""
    if "你不在任何任务里" not in rendered:
        j.fail("侧边栏的上下文没有说明「它不在任何任务里」")
        return
    # 而它**不能**拿会话渲染器的抬头来充数
    if "# 当前会话（" in rendered or "## 当前这一步（" in rendered:
        j.fail("侧边栏的上下文里出现了会话专有的抬头 —— 渲染器被复用错了")
        return

    j.ok(
        f"{len(tools)} 个工具（{', '.join(tools)}），**无一会话工具**；"
        f"{len(messages)} 条历史（讨论前 {history_before}）；"
        f"计量 {meter['used']}/{meter['limit']} token，压力 {meter['pressure']}；"
        "上下文自述「你不在任何任务里」"
    )


def step_clarify_ask_and_answer(j: Journey) -> None:
    """任务不明确时**反问**（P13）：模型问 → 界面看得见 → 答案落进定义区。

    ★ 这一步要证明的不是"接口存在"，而是**问这个动作有后果**：
    它被记下来、显示出来，并且在被回答或搁置之前挡住计划修改。
    而"挡住计划修改"发生在**助手那条路**上，所以这里必须真的走一圈
    助手（`/discuss` → 工具 → 写库），而不是直接 POST 一条问题。
    为此替身在用户说「我不太确定要做什么」时会调用 `ask_about_task`
    （`scripted.clarification_reply_for`）—— 替身只会在这一句话上这样回，
    否则计划闭环那一步会被它搅乱。
    """
    j.step("任务不明确时反问（问 → 显示 → 答 → 写进定义区）")

    # ① 先让目标空着 —— 这是"任务不明确"在已激活会话里唯一可能的状态
    #    （`draft → active` 已经被状态机要求必须有目标）。
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    original_goal = (state.get("input") or {}).get("goal")
    ver = state["versions"]["input"]
    j.req(
        "PATCH",
        f"/api/sessions/{j.session}/input",
        {"expected_version": ver, "goal": None, "reason": "先清掉，等我想清楚"},
    )

    # ② 助手**自己**问 —— 用户说「我不太确定要做什么」，替身调用 ask_about_task
    _, before = j.req("GET", f"/api/sessions/{j.session}/clarifications")
    code, _ = j.req(
        "POST",
        f"/api/sessions/{j.session}/discuss",
        {"message": "我不太确定要做什么，先帮我理一下"},
    )
    if code != 200:
        j.fail(f"讨论请求失败（{code}）")
        return
    _, listed = j.req("GET", f"/api/sessions/{j.session}/clarifications")
    items = listed["items"] if isinstance(listed, dict) else listed
    if len(items) <= len(before["items"] if isinstance(before, dict) else before):
        j.fail("★ 助手没有把问题记下来（`ask_about_task` 这条链路没走到）")
        return
    asked = items[0]
    if asked["status"] != "open" or asked["slot"] != "goal":
        j.fail(f"问题的状态/槽位不对：{asked['status']} / {asked['slot']}")
        return

    # ③ 同一个槽位再问一遍必须被拒（否则界面上会出现两个一样的问题）
    code, _ = j.req(
        "POST",
        f"/api/sessions/{j.session}/clarifications",
        {"slot": "goal", "question": "到底要什么？"},
        expect=(422, 200),
    )
    if code != 422:
        j.fail(f"同一槽位重复提问没有被拒（{code}）")
        return

    # ③.5 ★ 闸门真的挡着：让助手**再试一次改计划** —— 必须被拒，且计划一个字都没动
    #     这一段是这一步最要紧的一格：前面几格证明"问题记下来了"，
    #     只有这一格证明"它真的挡住了什么"。
    step_id = (j.memo.get("plan_steps") or [None])[0]
    if step_id is None:
        j.fail("拿不到计划步骤 id（`step_plan_item` 那一步没留下 memo）")
        return
    _, plan_before = j.req("GET", f"/api/sessions/{j.session}/plan")
    _, blocked_run = j.req(
        "POST",
        f"/api/sessions/{j.session}/discuss",
        {"message": "顺便把第一步拆细一点", "step_id": step_id},
    )
    traces = blocked_run.get("tool_trace") or []
    gate_hits = [
        t
        for t in traces
        if t.get("status") == "rejected" and "goal" in (t.get("message") or "")
    ]
    _, plan_after = j.req("GET", f"/api/sessions/{j.session}/plan")
    version_before = plan_before.get("version_no") or plan_before.get("version")
    version_after = plan_after.get("version_no") or plan_after.get("version")
    if not gate_hits:
        j.fail(
            "★ 目标空着时助手改计划**没有被拦**（闸门没生效）—— "
            f"轨迹：{[(t.get('tool'), t.get('status')) for t in traces]}"
        )
        return
    if version_after != version_before:
        j.fail(f"★ 计划被改了（v{version_before} → v{version_after}），而闸门本该拦住它")
        return

    # ④ 回答 → **真的写进定义区**并留下修订
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    answer = "把第三章的公式推导自己推一遍"
    _, answered = j.req(
        "POST",
        f"/api/sessions/{j.session}/clarifications/{asked['clarification_id']}/answer",
        {"answer": answer, "expected_version": state["versions"]["input"]},
    )
    if (answered.get("input") or {}).get("goal") != answer:
        j.fail("★ 答案没有写进定义区 —— 下一回合闸门会再拦一次")
        return
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    if (state.get("input") or {}).get("goal") != answer:
        j.fail("★ 定义区读出来还是空的（答案只落在了问题行上）")
        return
    _, revisions = j.req("GET", f"/api/sessions/{j.session}/input/revisions")
    rows = revisions["items"] if isinstance(revisions, dict) else revisions
    traced = [
        r
        for r in rows
        if asked["clarification_id"] in (r.get("reason") or "")
        and r.get("changed_by") == "user"
    ]
    if not traced:
        j.fail("修订史里没有这次回答（「我当初怎么说的」答不出来）")
        return

    # ⑤ 搁置那条路：计划可以继续，但必须写着"这是猜的"
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    original_criteria = (state.get("input") or {}).get("success_criteria")
    j.req(
        "PATCH",
        f"/api/sessions/{j.session}/input",
        {
            "expected_version": state["versions"]["input"],
            "success_criteria": None,
            "reason": "端到端验证：先清掉完成判据",
        },
    )
    _, paused = j.req(
        "POST",
        f"/api/sessions/{j.session}/clarifications",
        {"slot": "success_criteria", "question": "你希望我怎么算这一章算过了？"},
        expect=(200, 201),
    )
    code, _ = j.req(
        "POST",
        f"/api/sessions/{j.session}/clarifications/{paused['clarification_id']}/dismiss",
        {"reason": "先按你的判断来"},
        expect=(200, 422),
    )
    if code != 200:
        j.fail(f"搁置一个有理由的问题被拒（{code}）")
        return
    _, after = j.req("GET", f"/api/sessions/{j.session}/state")
    if (after.get("input") or {}).get("success_criteria") is not None:
        j.fail("★ 搁置被当成了回答：槽位不该被写进任何东西")
        return

    # ⑥ 复原：把目标与完成判据写回去，后面几步依赖它们
    if original_goal or original_criteria:
        _, state = j.req("GET", f"/api/sessions/{j.session}/state")
        restored = {"reason": "端到端验证复原"}
        if original_goal:
            restored["goal"] = original_goal
        if original_criteria:
            restored["success_criteria"] = original_criteria
        j.req(
            "PATCH",
            f"/api/sessions/{j.session}/input",
            {"expected_version": state["versions"]["input"], **restored},
        )
    j.ok(
        f"问题 {asked['clarification_id']}（goal）已答并写进定义区 + 修订；"
        f"重复提问被拒；助手改计划被闸门拦下（计划仍 v{version_before}）；"
        "另一条已搁置（槽位仍空）；目标已复原"
    )


def step_external_capabilities(j: Journey) -> None:
    """外挂能力（联网搜索 / MCP）：**只读、带前缀、缺失即不可见**。

    ## 这一步在两种服务器上都要能过

    外挂能力是**配置**出来的，而 `e2echeck` 面对的是别人起好的服务。
    所以这里验的是"两种情况下都必须成立的事"，而不是"某一次配置的结果"：

    1. `/api/external/tools` 永远答得出来（没配 → 给**原因**，不是一片空白）；
    2. `scope_ceiling` 永远是 `read` —— 没有哪种配置能把它抬上去；
    3. 服务器接通时，工具名一定是 `mcp__<名字>__<工具>`（不能冒充内置工具）；
    4. 如果**这台**服务器配了搜索，走一圈真实讨论：
       替身调 `web_search` → 结果带着 url 与「外部信息」回来 ——
       这一格证明的是"工具清单过滤 + 参数校验 + 结果回灌"整条链。
       没配就跳过，并在结论里说明跳过了（**不是**默默通过）。
    5. MCP **调用**那条路在这里不走（需要给这台服务器配一个服务器）；
       `tools/list` 那一跳已经在上面的第 3 条里真的走了（stdio 子进程），
       而调用的失败/超时路径由 `tests/external/test_mcp.py` 用真进程钉着。
    """
    j.step("外挂能力：联网搜索 / MCP（只读 · 带前缀 · 缺失即不可见）")

    code, described = j.req("GET", "/api/external/tools")
    if code != 200:
        j.fail(f"外挂能力状态读不出来（{code}）")
        return
    ceiling = described.get("scope_ceiling")
    if ceiling != "read":
        j.fail(f"★ 外挂工具的作用域上限不是 read，而是 {ceiling!r}（一个第三方进程不该能写）")
        return

    web = described.get("web_search") or {}
    servers = described.get("mcp_servers") or []
    # ★ 两个 `tools` 不是同一份名单（见 `ExternalToolbox.describe`）：
    #   顶层是**发给模型**的名字（`mcp__demo__echo`），
    #   每个服务器里的是它**自己声明**的名字（`echo`）。
    #   第一版拿后者当前者比，于是这一步误报了一次"外挂工具没有前缀"。
    external_tools = list(described.get("tools") or [])
    for name in external_tools:
        if name != "web_search" and not name.startswith("mcp__"):
            j.fail(f"★ 外挂工具名没有服务器前缀：{name}（它会冒充内置工具）")
            return
    for row in servers:
        for declared in row.get("tools") or []:
            prefixed = f"mcp__{row['server']}__{declared}"
            if prefixed not in external_tools:
                j.fail(
                    f"服务器 {row['server']} 声明了 {declared}，"
                    f"但它没有以 `{prefixed}` 出现在工具清单里"
                )
                return
        if row.get("connected") is False and not row.get("error"):
            j.fail(f"服务器 {row['server']} 没连上，却没说原因")
            return

    if not web.get("enabled"):
        # 没配搜索：这一格只能证明"缺失是看得见的"。
        if not web.get("reason"):
            j.fail("没配联网搜索，但也没说原因（它会表现成「插了却没反应」）")
            return
        j.ok(
            f"作用域上限 read；没配联网搜索（原因：{web['reason']}）；"
            f"MCP 服务器 {len(servers)} 个 —— 这条链的调用路径由单测用真进程钉着"
        )
        return

    # 配了搜索：走一圈真实讨论，看 `web_search` 的调用与回灌。
    code, run = j.req(
        "POST",
        f"/api/sessions/{j.session}/discuss",
        {"message": "帮我查一下 间隔重复"},
    )
    if code != 200:
        j.fail(f"带联网搜索的讨论请求失败（{code}）")
        return
    traces = run.get("tool_trace") or []
    search_traces = [t for t in traces if t.get("tool") == "web_search"]
    if not search_traces:
        j.fail(
            "★ 替身没有走到 `web_search`（工具清单里没有它，或者参数校验把它挡了）—— "
            f"轨迹：{[(t.get('tool'), t.get('status')) for t in traces]}"
        )
        return
    hit = search_traces[0]
    if hit.get("status") != "success":
        j.fail(f"`web_search` 没成功：{hit.get('status')} —— {hit.get('message')}")
        return
    if hit.get("scope") != "read":
        j.fail(f"轨迹里的 scope 不是 read：{hit.get('scope')}")
        return
    # ④ 结果要**落到那条助手消息的轨迹上** —— 用户才能看见"它凭什么这么说"。
    #    注意读的是 `/state` 里的 `chat`：会话没有单独的 `GET .../chat`
    #    （第一版写了一个，得到 404 —— 而"没有这个端点"不该由这一步来发现）。
    _, state = j.req("GET", f"/api/sessions/{j.session}/state")
    chat = state.get("chat") or []
    answer = next((m for m in reversed(chat) if m.get("role") == "assistant"), None)
    traced = [
        t
        for t in ((answer or {}).get("tool_trace") or [])
        if t.get("tool") == "web_search"
    ]
    if not traced:
        j.fail("★ 搜到的外部信息没有落到那一条助手消息上（用户看不到它凭什么这么说）")
        return
    if "外部信息" not in (traced[0].get("message") or ""):
        j.fail("★ 结果上的「外部信息，未经核对」记号没有落到消息里（模型会把它当成系统里的认知）")
        return
    j.ok(
        f"作用域上限 read；联网搜索接通（{web.get('provider')}，"
        f"{web.get('documents', '?')} 篇语料）；真实讨论里 `web_search` 成功返回"
        f"并把带 url 的外部结果回灌到消息轨迹上；MCP 服务器 {len(servers)} 个"
        f"（{sum(len(row.get('tools') or []) for row in servers)} 个工具已按前缀登记）"
    )


STEPS = [
    step_health,
    step_model_config,
    step_llm_profile_crud,
    step_session_read,
    step_discuss_changes_plan,
    step_motivation_lock,
    step_session_side_records,
    step_plan_item,
    step_chat_tool_trace,
    step_context_memory,
    step_sidebar_storage,
    step_scale_explanations,
    step_attachments,
    step_evidence,
    step_c_lifecycle,
    step_sidebar_organize,
    step_usage_readings,
    step_response_shape_consistency,
    step_clarify_ask_and_answer,
    step_annotate_and_see_basis,
    step_reflection_after_review,
    step_external_capabilities,
    step_complete_and_sediment,
    step_m1_reads,
    step_preferences_no_review,
    step_benchmark_checklist,
    step_benchmark_run_lifecycle,
    step_benchmark_sampling,
    step_benchmark_scoring,
    step_benchmark_self_report,
    step_benchmark_equating,
    step_benchmark_remaining_gaps,
    # ★ 放在最后：它自己会开一次新的基准运行（出卷 → 交卷），
    #   放在中间会让后面几步看到的运行历史多出一次。
    step_benchmark_offline_form,
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="e2echeck", description="端到端功能验证（✅可用 / ⚠️缺口 / ❌失败）"
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8140")
    ap.add_argument("--session", default="S-0001")
    ap.add_argument(
        "--items-dir",
        default="tests/fixtures/bank",
        help="题库目录（出卷那一步要读题目正文；起服务时也应当用同一个）",
    )
    args = ap.parse_args(argv)

    j = Journey(
        base=args.base_url.rstrip("/"),
        session=args.session,
        bank_items_dir=Path(args.items_dir),
    )
    print("=" * 74)
    print(f"端到端功能验证   {j.base}   会话 {j.session}")
    print("=" * 74)

    for fn in STEPS:
        j._current = fn.__name__
        try:
            fn(j)
        except AssertionError as exc:
            j.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - 验证器要把任何异常都变成一行
            j.fail(f"{type(exc).__name__}: {exc}")

    print()
    for r in j.results:
        print(f"{_MARK[r.status]} {r.name}")
        if r.note:
            for line in str(r.note).splitlines():
                print(f"      {line}")

    ok = sum(1 for r in j.results if r.status is OK)
    gap = sum(1 for r in j.results if r.status is GAP)
    bad = sum(1 for r in j.results if r.status is FAIL)
    print()
    print("=" * 74)
    print(f"可用 {ok}   缺口 {gap}   失败 {bad}   （共 {len(j.results)} 步）")
    if gap:
        print("⚠️  缺口是**功能没做**，不是坏了 —— 它不影响退出码。")
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
