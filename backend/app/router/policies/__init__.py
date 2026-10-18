"""更新策略实现（M0 §7.3）—— **只对 Router 内部可见**（ARCH-02）。

| 模块 | 负责的 target 类 | 写的表 |
|---|---|---|
| `m1_policy` | `M1.<variable_id>` | `m1_baseline_version` + 移动 `m1_baseline_current` 指针 |
| `m2_policy` | `M2.preferences*` | `m2_preference` |
| `c_policy` | `C.evidence*` | `c_evidence` |
| `benchmark_advisory` | ——（不是策略，是 §5.4 建议） | 不写任何表 |

`base.py` 定义策略契约（`PolicyContext` / `PolicyOutcome` / `PolicyRefused`）。

**为什么单独成包**：策略是「怎么写」的知识，判定表是「该不该写」的知识。
混在一起时，加一条判定规则就有可能顺手改动写入逻辑。分开之后，
ARCH-02 让这个包对外不可见，写入路径只剩下 `Router` 一个入口。
"""
