"""HTTP 接口层（L5）。

ARCH-08 禁止本层 import `app.global_domain.models`，ARCH-01 禁止它 import
`app.global_domain.writes`。两条合起来意味着：**API 层拿不到任何写函数，
也拿不到 ORM 类** —— 它只能经 `reads.py` + `schemas.py` 取数，
并且只能通过 `Router` 写。

这不是靠自觉：`tests/api/test_api_boundaries.py` 用 AST 扫描整个 `app/api/`
把这两条钉死，且断言每个端点都声明了 `response_model`
（否则 FastAPI 会直接把 ORM 对象序列化出去，等于绕过 DTO 层）。
**唯一的例外是 SSE 端点**：它没有"一个 JSON 响应体"可言，
判据不是路径名而是 `response_class` 是 `StreamingResponse` 的子类，
且流式端点的名单在测试里是**显式列出**的。

## 端点清单（46 条路径 / 57 个「方法+路径」对，按模块分组）

| 模块 | 端点 |
|---|---|
| 全局·基线 | `GET /api/m1/variables`（逐通道覆盖，无「变量总分」）· `/{id}/history` · `/{id}/observations` · `/{id}/benchmark-advisory`（§5.4，**响应里没有阻断字段**）· `POST /{id}/rollback`（R5：生成新版本，不删历史） |
| 全局·偏好与证据 | `GET/POST /api/preferences`（R1 直写偏好，同样经 Router）· `GET/POST /api/evidence`（C 条目，`condition` 必填） |
| 全局·提案 | `POST /api/proposals`（提交意图）· `GET /api/proposals` · `GET /{id}`（含逐条 intent 的系统判定）· `POST /{id}/resolve`（逐条裁决） |
| 全局·审计 | `GET /api/router-logs`（统一审计日志，**只读，没有写接口**） |
| 沙箱·文件夹与会话 | `GET/POST /api/folders` · `GET/POST /api/sessions` · `GET /{id}/state`（侧页唯一数据入口）· `POST /{id}/transitions` |
| 沙箱·定义区 | `PATCH /{id}/input` · `GET /{id}/input/revisions` · `PATCH /{id}/motivation` · `GET /{id}/motivation/revisions` · `PATCH /{id}/snapshot` |
| 沙箱·计划 | `POST /{id}/plan` · `GET /{id}/plan` · `PUT /{id}/plan`（改版本）· `GET /{id}/plan/revisions` · `PATCH .../steps/{step_id}` · `PATCH .../checkpoints/{cp_id}` · `GET .../steps/{step_id}/context`（讨论上下文包） |
| 沙箱·过程记录 | `POST /{id}/stage` · `PATCH /{id}/stage/{item_id}` · `POST /{id}/chat` · `POST /{id}/notes` · `POST /{id}/milestones` · `POST /{id}/milestones/{mid}/complete` · `POST /{id}/summary` |
| 沙箱·讨论 | `POST /{id}/discuss`（一次性）· `POST /{id}/discuss/stream`（**SSE**） |
| 沙箱·资料 | `POST /{id}/attachments`（**请求体就是文件字节**，文件名走查询参数）· `GET /{id}/attachments`（清单，**不含正文**）· `GET /{id}/attachments/{aid}`（分段读正文）· `DELETE /{id}/attachments/{aid}`（连同磁盘原件） |
| 沙箱·沉淀（已声明的交接口） | `POST /{id}/sediment`（`dry_run=true` 必须逐条列示将要写什么）· `POST /{id}/complete` |
| 基础设施 | `GET /api/llm/providers` · `/config` · `/profiles`（增删改查 / `activate` / `models` / `probe`）—— **第三类域**：不经 Router、不碰认知四表 |
| 元 | `GET /api/health` |

## 三类写入，三种通道

这些端点按"能碰到什么"分成三类，**不要把它们混起来看**：

1. **全局域的正门**：`/api/proposals*`（意图与逐条裁决）、`/api/m1*`、
   `/api/preferences`、`/api/evidence` —— 全部经 `Router`，
   判定表与能力令牌在那一层。
2. **沙箱域**：文件夹 / 会话 / 定义区 / 计划 / 过程记录 / **资料** ——
   只写 `app.session_domain`，**不经过 Router**。这不是漏洞，是
   M0 §6.2「沙箱内自由读写」；边界由
   `test_session_write_routes_never_touch_the_router` 与 ARCH-12 双向钉住。
3. **通道与基础设施**：`/api/sedimentation*` 与 `/api/discuss*` 是三条写入
   通道中的两条 —— 前者是 M0 §6.3 声明的交接口（沙箱 → 全局的**唯一**通道），
   后者是沙箱写入端点，它自己**一个全局字节都不写**（唯一的全局出口是
   `app.agent.handoff`，模型提议沉淀时）；`/api/llm/*` 是第三类域
   （基础设施），既不经 Router 也不碰认知四表。
"""

from app.api.deps import get_router, get_session, new_ui_intent_id

__all__ = ["get_router", "get_session", "new_ui_intent_id"]
