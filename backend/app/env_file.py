r"""`.env` 读取。

## 为什么自己写而不是引 python-dotenv

要求只有四条，而每一条都要能说清：
① **`键=值` 与 `键:值` 都认**（见下）；② 环境变量优先于文件；
③ 只有明确写进文件的键才会被设置；④ 不抛异常、不打印值。

## 为什么必须认冒号

这个项目里真实发生过：`backend/../.env` 里写的是 `DEEPSEEK_API_KEY:sk-…`
（YAML 风格）。标准 dotenv 只认 `=`，于是那个文件**静默地什么都没提供** ——
表现出来是"我明明配了密钥，为什么说没有"。一个把用户配好的东西读丢的加载器，
比没有加载器更坏：它让人去查别的地方。

## 优先级（与所有人的直觉一致）

**真实环境变量 > `.env` 文件**。所以这个模块**只在键不存在时**设置它。
理由：`.env` 是"本地的默认值"，而 `$env:MOUXUE_LLM_MODEL=...` 是"这一次我就要这样"。
反过来的话，容器里注入的环境变量会被一个陈旧的 `.env` 悄悄覆盖，
而现象是"我改了环境变量但没生效"。
"""

from __future__ import annotations

import os
import pathlib
from typing import Iterable

#: 文件里可能出现的键名前缀。**只读取我们认识的键** ——
#: 一个通用的"把文件里所有键都塞进 os.environ"会让 `.env` 里的
#: 任何东西（比如别人的 `PATH`）影响整个进程。
KNOWN_PREFIXES: tuple[str, ...] = ("MOUXUE_", "DEEPSEEK_", "OPENAI_", "OLLAMA_")


def default_env_paths() -> list[pathlib.Path]:
    """查找顺序：`backend/.env` → 仓库根 `.env`。

    两个都读（后面的只补前面没有的键），因为一个人可能把密钥放在仓库根
    （与其他项目一起），而把无关的本地开关放在 backend 下。
    """
    here = pathlib.Path(__file__).resolve()  # app/config.py
    backend = here.parents[1]  # backend/
    root = backend.parent  # personal_learning_flow/
    return [backend / ".env", root / ".env"]


def parse_env_text(text: str) -> dict[str, str]:
    """解析 `.env` 文本。**只返回认识的键**，不认识的原样忽略。

    支持的写法（都是真实存在的）：
    - `KEY=value`、`KEY:value`（冒号后可有空格）
    - `export KEY=value`
    - `KEY="value"`、`KEY='value'`
    - `#` 开头的整行注释
    - 值里的 `#` **不当作注释**（密钥里可能有 `#`，截断它是灾难性的）

    不支持（也不会假装支持）：多行值、变量插值、`${…}` 展开。
    遇到不认识的写法**不报错**（`.env` 里常有别人的东西），
    但也不会让那一行的键名进入环境。
    """
    parsed: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key: str
        value: str
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue

        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if not key.startswith(KNOWN_PREFIXES):
            continue
        # 成对的引号才剥掉；不成对的留着（宁可原样也不要静默改一个密钥）
        for quote in ("'", '"'):
            if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
                value = value[1:-1]
                break
        parsed[key] = value

    return parsed


def load_env_file(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    # `utf-8-sig`：Windows 上用记事本存过的文件会带 BOM，
    # 而带 BOM 时第一个键名会变成 `\ufeffMOUXUE_...` —— 于是"文件在、却一个键都没读到"。
    return parse_env_text(path.read_text(encoding="utf-8-sig"))


def load_env(
    paths: Iterable[pathlib.Path] | None = None, *, override: bool = False
) -> dict[str, str]:
    """把 `.env` 里的键装进 `os.environ`，返回**这次实际生效**的那些键。

    返回值的用途是让启动时能打印一行"已从 .env 读到 N 个键（不含值）" ——
    没有这一行的话，"密钥到底有没有被读到"只能靠猜。
    """
    paths = list(paths) if paths is not None else default_env_paths()
    loaded: dict[str, str] = {}
    for path in paths:
        for key, value in load_env_file(path).items():
            if key in loaded and not override:
                continue  # 先读到的文件优先
            if key in os.environ and not override:
                continue  # **真实环境变量优先**
            os.environ[key] = value
            loaded[key] = value
    return loaded
