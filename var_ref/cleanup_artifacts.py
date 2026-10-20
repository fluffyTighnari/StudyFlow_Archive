"""清掉这一路攒下来的**产物**：日志、探针输出、旧 dev 库、重复的 CSS、空目录。

规则（三条，写在这里是为了下次不用重新想）：

1. **日志与一次性输出删**（`*.log` / `probe_*` / `_*.txt` / `sse_*.err`）——
   它们是某一次命令的回声，结论已经写进文档；
2. **旧的 dev 库删，正在用的留**——`ui_preview.db`（预览库，里面有一场脚本会话）、
   `ui_fake.db`（文档里点过名）留着；其余的探索库（10-17 到 10-19 的
   `livesse*` / `livedemo*` / `ui*` / `invariant_check` / `e2e_v3`）删掉，
   `tools/seedui --db <路径> --reset` 随时能再造一个；
3. **有出处价值的留**：DSH 运行时产物（`*.js` / `*.pretty.css` / `*.cssNN.txt` / `icons.txt`）、
   令牌源（`platform-light.css` / `alias-*.css` / `semantic-light.css`）、
   补丁脚本、对账脚本、DOM 快照、`model_trials.md`、`websearch_corpus.json`、
   `secrets/`（钥匙）、`uploads/`（资料）。

**不删**：`_backup_*` 目录（那是未提交代码的唯一副本）、`frontend/`（git 里 HEAD 的状态）、
任何被 `.md` 引用过的东西。
"""

from __future__ import annotations

import pathlib
import shutil

ROOT = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow")
VAR = ROOT / "backend" / "var"
REF = ROOT / "var_ref"

removed: list[tuple[str, int]] = []


def drop(path: pathlib.Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path)
    else:
        size = path.stat().st_size
        path.unlink()
    removed.append((str(path.relative_to(ROOT)), size))
    name = str(path.relative_to(ROOT))
    print(f"  删除 {name:<44} {size:>9,} B")


print("== backend/var：日志与一次性输出 ==")
for name in (
    "sse_ui.log", "sse_ui.err", "sse_real.log",
    "livesse_ollama.log", "livesse_ollama2.log",
    "probe_results.json", "probe_stdout.txt", "probe_log.txt",
    "probe_repeat_results.json", "probe_repeat_stdout.txt", "probe_repeat_log.txt",
    "probe_final_results.json", "probe_final_stdout.txt", "probe_final_log.txt",
    "_req2.txt", "_req2b.txt", "_smoke.txt", "_evidence.txt",
    "_stream_STREAM__qwen35_9b__think-True.txt",
    "_stream_STREAM__qwen36_27b__think-False.txt",
    "commitmsg.txt",
):
    drop(VAR / name)

print("== backend/var：旧的 dev 库（正在用的两个留着）==")
for name in (
    "mouxue.db", "shot.db", "ui_m1.db", "ui.db", "ui_r27.db", "ui_real.db",
    "livesse.db", "livesse2.db", "livesse_ollama.db", "livesse_ollama2.db",
    "livedemo_9b.db", "livedemo_change.db", "livedemo_v3.db",
    "invariant_check.db", "e2e_v3.db",
):
    drop(VAR / name)

print("== backend/var：空目录 ==")
for name in ("probe_raw", "probe_raw_repeat", "probe_final_raw", "bridge", "__pycache__"):
    drop(VAR / name)

print("== var_ref：日志 / 重复的 CSS / 没跑过的脚本 / 缓存 ==")
for name in (
    "pytest_final.txt", "pytest_final_v.txt", "pytest_full.txt", "pytest_out.txt",
    "check_v3_out.txt",
    "index-DPX2bQLO.css",      # 与 index.pretty.css 同源（一份压缩、一份展开）
    "vendor-BNsW4eBh.css",     # 同上
    "patch_app_v2a.py",        # 这一份从未跑过（当时改用 edit 工具做的同一件事）
    "__pycache__",
):
    drop(REF / name)

total = sum(size for _, size in removed)
print(f"\n共删除 {len(removed)} 项，释放 {total / 1024 / 1024:.2f} MB")

print("\n== 保留下来的（以及理由）==")
print("  backend/var/ui_preview.db   预览库（一场脚本会话在里面）")
print("  backend/var/ui_fake.db      文档里点名过的演示库")
print("  backend/var/secrets/        模型密钥（不进库、不进日志）")
print("  backend/var/uploads/        会话资料")
print("  backend/var/model_trials.md 本地模型选型记录（被文档引用）")
print("  backend/var/websearch_corpus.json  离线搜索语料（被文档引用）")
print("  var_ref/*.js / *pretty.css / *.cssNN.txt / icons.txt  复刻界面的原始依据")
print("  var_ref/{platform-light,alias-light,alias-dark,semantic-light}.css  令牌生成器的输入")
print("  var_ref/patch_*.py / check_*.py / v3_*_dom.html      改动的痕迹与验收规格")
