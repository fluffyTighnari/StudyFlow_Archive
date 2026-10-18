"""`.env` 读取的测试。

这一层的价值全在"读丢了一个键"那种失败上 —— 它不报错、不崩溃，
只是静默地什么都不提供，于是人去查别的地方。所以这里的重点不是
"能读到"，而是**各种真实存在过的写法都能读到**，以及
**优先级与直觉一致**。
"""

from __future__ import annotations

import os
import pathlib

import pytest

from app.env_file import KNOWN_PREFIXES, load_env, parse_env_text


def test_colon_syntax_is_read() -> None:
    """**这个项目真实踩过的坑**：仓库根的 `.env` 里写的是

        DEEPSEEK_API_KEY:sk-…

    （YAML 风格）。标准 dotenv 只认 `=`，于是那个文件静默地什么都没提供，
    而表现出来是"我明明配了密钥，为什么说没有"。
    """
    parsed = parse_env_text("DEEPSEEK_API_KEY:sk-abc123\nDEEPSEEK_BASE_URL: https://x/v1\n")
    assert parsed == {
        "DEEPSEEK_API_KEY": "sk-abc123",
        "DEEPSEEK_BASE_URL": "https://x/v1",
    }


def test_equals_syntax_is_read() -> None:
    parsed = parse_env_text("MOUXUE_LLM_MODEL=deepseek-chat\n")
    assert parsed == {"MOUXUE_LLM_MODEL": "deepseek-chat"}


def test_export_prefix_quotes_comments_and_blank_lines() -> None:
    text = (
        "# 注释行\n"
        "\n"
        "export MOUXUE_LLM_PROVIDER=deepseek\n"
        'MOUXUE_LLM_MODEL="deepseek-chat"\n'
        "MOUXUE_LLM_API_KEY='sk-quoted'\n"
        "   DEEPSEEK_API_KEY =  sk-spaced  \n"
    )
    parsed = parse_env_text(text)
    assert parsed["MOUXUE_LLM_PROVIDER"] == "deepseek"
    assert parsed["MOUXUE_LLM_MODEL"] == "deepseek-chat"
    assert parsed["MOUXUE_LLM_API_KEY"] == "sk-quoted"
    assert parsed["DEEPSEEK_API_KEY"] == "sk-spaced"


def test_a_hash_inside_a_value_is_not_a_comment() -> None:
    """**密钥里可能有 `#`。** 把它当注释截掉，表现是"认证失败"，
    而人会去查密钥是不是过期了 —— 绝不会想到是我们把它的后半截吃了。
    """
    parsed = parse_env_text("MOUXUE_LLM_API_KEY=sk-a#b#c\n")
    assert parsed["MOUXUE_LLM_API_KEY"] == "sk-a#b#c"


def test_unknown_keys_are_ignored() -> None:
    """只收认识的键。

    一个"把文件里所有键都塞进 `os.environ`"的加载器，
    会因为 `.env` 里有一行别人的 `PATH=` 而改掉整个进程的行为。
    """
    parsed = parse_env_text("PATH=/evil\nHOME=/evil\nMOUXUE_LLM_MODEL=x\n")
    assert parsed == {"MOUXUE_LLM_MODEL": "x"}


def test_known_prefixes_cover_the_companies_named_keys() -> None:
    """人会把密钥按公司命名（`DEEPSEEK_API_KEY`）—— 那是最自然的写法，
    不该要求他改成我们的名字。"""
    for prefix in ("MOUXUE_", "DEEPSEEK_", "OPENAI_", "OLLAMA_"):
        assert prefix in KNOWN_PREFIXES


def test_a_line_without_a_separator_is_skipped() -> None:
    parsed = parse_env_text("这不是一个赋值\nMOUXUE_LLM_MODEL=ok\n")
    assert parsed == {"MOUXUE_LLM_MODEL": "ok"}


def test_bom_does_not_eat_the_first_key(tmp_path: pathlib.Path) -> None:
    """Windows 上用记事本存过的文件带 BOM。

    带 BOM 时第一个键名会变成 `\\ufeffMOUXUE_...` —— 于是**文件在，
    第一个键却读不到**，而失败的样子是"某一项配置没生效"，极难联想到编码。
    """
    path = tmp_path / ".env"
    path.write_bytes("MOUXUE_LLM_MODEL=x\n".encode("utf-8-sig"))
    from app.env_file import load_env_file

    assert load_env_file(path) == {"MOUXUE_LLM_MODEL": "x"}


def test_real_environment_wins_over_the_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`$env:X=y` 是"这一次我就要这样"，`.env` 是"本地的默认值"。

    反过来的话，容器里注入的环境变量会被一个陈旧的 `.env` 悄悄覆盖，
    而现象是"我改了环境变量但没生效"。
    """
    path = tmp_path / ".env"
    path.write_text("MOUXUE_LLM_MODEL=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MOUXUE_LLM_MODEL", "from_env")
    loaded = load_env([path])
    assert os.environ["MOUXUE_LLM_MODEL"] == "from_env"
    assert "MOUXUE_LLM_MODEL" not in loaded, "被环境变量挡住的键不该算作'已从文件读到'"


def test_the_first_file_wins(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """查找顺序 `backend/.env` → 仓库根 `.env`；先读到的优先。

    两个都读（后面的只补前面没有的键），因为一个人可能把密钥放在仓库根、
    把本地开关放在 backend 下。
    """
    first = tmp_path / "backend.env"
    second = tmp_path / "root.env"
    first.write_text("MOUXUE_LLM_MODEL=first\n", encoding="utf-8")
    second.write_text("MOUXUE_LLM_MODEL=second\nMOUXUE_LLM_PROVIDER=deepseek\n", encoding="utf-8")
    monkeypatch.delenv("MOUXUE_LLM_MODEL", raising=False)
    monkeypatch.delenv("MOUXUE_LLM_PROVIDER", raising=False)
    loaded = load_env([first, second])
    assert os.environ["MOUXUE_LLM_MODEL"] == "first"
    assert os.environ["MOUXUE_LLM_PROVIDER"] == "deepseek", "后面的文件要能补上前面没有的键"
    assert sorted(loaded) == ["MOUXUE_LLM_MODEL", "MOUXUE_LLM_PROVIDER"]


def test_a_missing_file_is_not_an_error(tmp_path: pathlib.Path) -> None:
    assert load_env([tmp_path / "nope.env"]) == {}


def test_skip_switch_makes_from_env_ignore_the_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MOUXUE_SKIP_ENV_FILE=1` 时 `Settings.from_env()` 不读文件。

    测试与 CI 靠它不受开发者本机 `.env` 的影响 ——
    这个项目的 `.env` 里有一把真密钥，读到它会让"这台机器绿、那台机器红"。
    """
    from app.config import Settings

    path = tmp_path / ".env"
    path.write_text("MOUXUE_LLM_MODEL=from_file\n", encoding="utf-8")
    monkeypatch.setattr("app.env_file.default_env_paths", lambda: [path])
    monkeypatch.delenv("MOUXUE_LLM_MODEL", raising=False)

    monkeypatch.setenv("MOUXUE_SKIP_ENV_FILE", "1")
    assert Settings.from_env().llm_model != "from_file"

    monkeypatch.setenv("MOUXUE_SKIP_ENV_FILE", "0")
    assert Settings.from_env().llm_model == "from_file"
