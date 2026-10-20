"""把 PowerShell `Set-Content` 不小心写进去的 BOM 去掉（架构检查会硬失败）。"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow")

fixed: list[str] = []
for path in list(ROOT.glob("backend/**/*.py")) + list(
    ROOT.glob("frontend/app/src/**/*.ts*")
):
    if "node_modules" in path.parts or ".venv" in path.parts:
        continue
    blob = path.read_bytes()
    if blob.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(blob[3:])
        fixed.append(str(path.relative_to(ROOT)))

print("去掉 BOM 的文件：", fixed if fixed else "无")
