"""附件：把用户上传的文本文件变成**可以入库、可以审计的正文**。

## 这个模块只做两件事

1. **解析**：`bytes` → 正文文本 + 一份"我是怎么解出来的"说明；
2. **落盘**：把原件存到 `var/uploads/`，返回一个相对路径。

它**不碰数据库、不碰 FastAPI、不引第三方库**。理由有三条：

- 解析是这个功能里唯一"会被真实世界的脏输入打脸"的地方（编码、伪装的文件、
  加密的 docx、老 .doc），所以它必须能被单独测到每一种输入；
- 数据库与上载路径的编排属于沙箱域（`app/session_domain`），
  解析器管不着事务，也不该管；
- 依赖只有 5 个（fastapi/uvicorn/pydantic/sqlalchemy/alembic），
  而 `.docx` 本身就是一个 zip —— `zipfile` + `ElementTree` 够用。

## 为什么 `.docx` 不引 `python-docx`

`.docx` 是 OOXML：一个 zip，正文在 `word/document.xml`，段落是 `w:p`、
文本段是 `w:t`、表格是 `w:tbl`。取"段落 + 表格"这件事标准库完全够。
不引依赖的代价是**保真度有边界**，而那些边界必须写出来（见 `note` 字段）：

| 取 | 不取 |
|---|---|
| 段落文本（`w:p` / `w:t`） | 字体、字号、颜色、样式名 |
| 表格（按行列拍平成 `\\| a \\| b \\|`） | 单元格合并（按原样重复）、嵌套表格的内容 |
| 制表符与换行（`w:tab` / `w:br`） | 图片、批注、脚注、修订痕迹、页眉页脚 |

**这些"不取"不是缺陷，是必须让用户知道的事** —— 所以它们进 `note`，
而 `note` 会跟着附件一起进上下文与界面。

## 为什么编码要兜底到 GB18030

中文用户的 `.txt` 很可能是 GBK。**读成乱码比报错更坏**：乱码会一路进上下文、
进模型的判断，而没人会发现；报错至少会让人换一个文件。
所以顺序是 `utf-8-sig`（带 BOM 的 UTF-8）→ `gb18030`（GBK 的超集），
两个都不行就明确说"这不是文本文件"。

反过来，**GB18030 几乎不拒绝任何字节序列**，所以必须先做一次"看起来像二进制吗"
的嗅探（NUL、控制字符比例），否则一个 .zip 会被"成功"解成一堆乱码。
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from app.core.errors import AttachmentRejected

# --------------------------------------------------------------------------- #
# 允许什么
# --------------------------------------------------------------------------- #

#: 扩展名 → 解析器种类。**只有这三类文本文件**（用户裁定）。
#:
#: `.markdown` 也认：它是 `.md` 的正式长写法，而拒绝它只会制造一次
#: "为什么这个 .md 能传、那个不能"的困惑。
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".txt": "plain",
    ".md": "plain",
    ".markdown": "plain",
    ".docx": "docx",
}

#: 明确认识但**不支持**的格式 → 给用户的一句话。
#:
#: 只说“不支持”；支持哪些格式由界面在选文件时给出，不在错误里重复。
KNOWN_UNSUPPORTED: dict[str, str] = {
    ".doc": "不支持这种文件。",
    ".pdf": "不支持这种文件。",
    ".rtf": "不支持这种文件。",
    ".odt": "不支持这种文件。",
}

#: 单文件上限 2 MB。
#:
#: 它不是"数据库存不下"（SQLite 的 Text 能存更大），而是**上下文装不下**：
#: 2 MB 的中文正文约 60 万字，远超任何窗口，而 read_attachment 是分段读的，
#: 一份超大文件只会让模型的每一次检索都很贵。拒绝比"传上来但没人读得完"诚实。
MAX_BYTES = 2 * 1024 * 1024

#: 提取正文的上限 20 万字符（约 6–10 万汉字）。超出就截断并**标记**。
MAX_CHARS = 200_000

#: 文件名显示长度上限。够长到能区分，短到不会撑破界面。
MAX_FILENAME = 120

#: 上下文里每条附件清单带的预览长度（字符）。
#:
#: 150 是量出来的：够看出"这是不是我要找的那份"（通常是一行标题或第一句），
#: 而 20 份 × 150 字 ≈ 3000 字，在 16384 的窗口里是可接受的常数开销。
PREVIEW_CHARS = 150


class AttachmentError(AttachmentRejected):
    """上传被拒绝或解析失败。

    `str(exc)` **就是给用户看的那句话** —— 它由界面直接显示，
    所以每一处抛出都写清"哪里不对 + 下一步做什么"，而不是抛一个类型名。

    **继承 `AttachmentRejected`（一个 `DomainError`）而不是 `ValueError`**：
    这个项目的规矩是「域层只抛 `DomainError` 子类，HTTP 状态码只在
    `app/core/errors.py` 出现一次」。如果它是 `ValueError`，
    最可能的结果是没人接住它 → 500 "服务器内部错误" ——
    而真相是"这个文件读不了，换个格式再传"，一句用户能自己解决的话
    被伪装成一次系统故障。
    """

    code = "attachment_rejected"


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedFile:
    """一次解析的结果。**没有"解析可能失败"这个状态** —— 失败就是抛异常。"""

    #: 原始文件名（已清洗，可直接显示）。
    filename: str
    #: 小写扩展名，含点（`.md`）。
    ext: str
    #: 解析器种类：`plain` / `docx`。
    kind: str
    #: 提取出的正文（可能已截断，见 `truncated`）。
    text: str
    #: 原始字节数。
    bytes_len: int
    #: `text` 的字符数。
    chars_len: int
    #: 是否因为超过 `MAX_CHARS` 被截断。
    truncated: bool
    #: 内容哈希（审计：同一份文件传两次看得出来）。
    sha256: str
    #: **解析过程的取舍说明**，会进上下文与界面。例如
    #: "原文件 512 字，按 UTF-8 读入" / "表格已按行列拍平（共 3 张）"。
    note: str


# --------------------------------------------------------------------------- #
# 文件名与扩展名
# --------------------------------------------------------------------------- #

_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")


def normalize_filename(raw: str | None) -> str:
    """清洗上传时带来的文件名。

    **只用于显示与落盘命名，不用于任何身份判断** —— 附件的身份是 `AT-0001`，
    由数据库分配。这样"文件名里带 `../`"最多只是显示难看，
    不可能变成一次路径穿越。

    三件事：去掉目录部分（`/` 与 `\\` 都算，攻击者不必用本平台的风格）、
    去掉控制字符、截长。全空就退回 `未命名.txt`。
    """
    if raw is None:
        return "未命名"
    # 两种分隔符都切：Windows 客户端可能发 `C:\\a\\b.md`，
    # 而 POSIX 风格的路径在 Windows 上也是一个合法的文件名。
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("", name).strip().strip(".")
    if not name:
        return "未命名"
    if len(name) > MAX_FILENAME:
        # 保留扩展名：截断把 `.md` 切掉会改变解析路径。
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILENAME - len(ext) - 1
        name = f"{stem[:keep]}.{ext}" if dot and keep > 0 else name[:MAX_FILENAME]
    return name


def extension_of(filename: str) -> str:
    """取小写扩展名（含点）。没有扩展名就不是我们收的东西。"""
    return Path(filename).suffix.lower()


def _check_extension(filename: str) -> tuple[str, str]:
    ext = extension_of(filename)
    if ext in ALLOWED_EXTENSIONS:
        return ext, ALLOWED_EXTENSIONS[ext]
    raise AttachmentError("不支持这种文件。")


# --------------------------------------------------------------------------- #
# 内容嗅探：**不要相信扩展名**
# --------------------------------------------------------------------------- #

#: OLE2 复合文档的魔数 —— `.doc` / `.xls` / `.ppt` 都是这个头。
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _looks_binary(raw: bytes) -> bool:
    """头 4 KB 里有 NUL、或控制字符占比过高 → 不是文本。

    这一步是必须的：GB18030 几乎能"成功"解码任意字节序列，
    没有它就等于**把二进制文件变成一段乱码正文**送进上下文。
    """
    head = raw[:4096]
    if b"\x00" in head:
        return True
    if not head:
        return False
    control = sum(
        1 for byte in head if byte < 0x20 and byte not in (0x09, 0x0A, 0x0D)
    )
    return control / len(head) > 0.10


def _sniff(raw: bytes, filename: str, kind: str) -> None:
    """按**内容**再确认一次，不一致就明确拒绝。"""
    if not raw.strip():
        raise AttachmentError("这个文件是空的。")
    if raw.startswith(_OLE2):
        raise AttachmentError("不支持这种文件。")
    if raw.startswith(b"%PDF"):
        raise AttachmentError("不支持这种文件。")
    if kind == "docx":
        if not raw.startswith(b"PK"):
            raise AttachmentError("不支持这种文件。")
    elif _looks_binary(raw):
        raise AttachmentError("不支持这种文件。")


# --------------------------------------------------------------------------- #
# 纯文本
# --------------------------------------------------------------------------- #


def _decode(raw: bytes, filename: str) -> tuple[str, str]:
    """解码。返回 `(正文, 用了什么编码)`。

    顺序：`utf-8-sig`（顺带吃掉 BOM；**留着 BOM 会让正文第一个字符是个看不见的
    `\\ufeff`**，模型读到它不会报错，只会在某些判断上莫名其妙）→ `gb18030`。
    """
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AttachmentError(
        f"「{filename}」既不是 UTF-8 也不是 GBK —— 请把它另存为 UTF-8 编码的 `.txt`。"
    )


def _parse_plain(raw: bytes, filename: str) -> tuple[str, str]:
    text, encoding = _decode(raw, filename)
    note = f"按 {encoding} 读入"
    return text, note


# --------------------------------------------------------------------------- #
# .docx
# --------------------------------------------------------------------------- #

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: 正文所在的那一个 part。Word 把正文、页眉、脚注、批注分成不同的 part，
#: 我们只取正文 —— 其余是"不取"清单里写明的取舍。
_DOCUMENT_PART = "word/document.xml"


def _paragraph_text(node: ElementTree.Element) -> str:
    """一个 `w:p` 的文本。

    必须**按文档顺序**遍历后代，而不是 `findall("w:t")` 只找直接子节点：
    文本可能被包在 `w:hyperlink`、`w:smartTag`、`w:ins`（修订插入）里，
    而那些都是合法且常见的。`w:tab` / `w:br` 也要还原成制表符与换行，
    否则"They<TAB>are"会粘成"The yare"。
    """
    parts: list[str] = []
    for child in node.iter():
        tag = child.tag
        if tag == f"{_W}t":
            parts.append(child.text or "")
        elif tag == f"{_W}tab":
            parts.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)


def _table_lines(table: ElementTree.Element) -> list[str]:
    """把一张表拍平成 Markdown 风格的 `| a | b |` 行。

    **合并单元格会在各自的行里重复出现**（`w:vMerge` 不展开）——
    这是"不取"清单里的一条：还原合并语义需要维护一个跨行的网格，
    而对"让模型读懂内容"这件事，重复一遍比缺一块安全。
    """
    lines: list[str] = []
    for row in table.findall(f"{_W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_W}tc"):
            inner = " ".join(
                _paragraph_text(p).strip() for p in cell.findall(f"{_W}p")
            ).strip()
            cells.append(inner.replace("|", "\\|"))
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def _document_blocks(body: ElementTree.Element) -> list[str]:
    """按**文档顺序**取块级内容：段落与表格交替出现的那份顺序。

    表格里的段落**不能**再被当普通段落收一遍（那会让表格内容出现两次，
    且散落在表格之外）。所以这里是"看到 `w:tbl` 就整块交给表格处理、
    不再往下走"。
    """
    blocks: list[str] = []
    for child in body:
        tag = child.tag
        if tag == f"{_W}p":
            blocks.append(_paragraph_text(child))
        elif tag == f"{_W}tbl":
            blocks.extend(_table_lines(child))
        elif tag == f"{_W}sdt":
            # 内容控件（`w:sdt`）是 Word 里"可复用块"的容器，
            # 它的内容在 `w:sdtContent` 里 —— 正文可能整个被它包住。
            content = child.find(f"{_W}sdtContent")
            if content is not None:
                blocks.extend(_document_blocks(content))
    return blocks


def _parse_docx(raw: bytes, filename: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if _DOCUMENT_PART not in names:
                raise AttachmentError(
                    f"「{filename}」是一个 zip，但里面没有 `{_DOCUMENT_PART}` —— "
                    "它可能不是 Word 文档（改过扩展名？）。"
                )
            xml_bytes = archive.read(_DOCUMENT_PART)
    except zipfile.BadZipFile as exc:
        raise AttachmentError(
            f"「{filename}」不是一个完整的 `.docx`（zip 读不开：{exc}）—— "
            "文件可能在传输中损坏了，请重新传一次。"
        ) from exc
    except AttachmentError:
        raise
    except (OSError, RuntimeError) as exc:
        # 加密的 `.docx` 会走到这里：`ZipFile.read` 抛的是
        # `RuntimeError("File ... is encrypted, password required")` ——
        # 它不是 OSError，所以必须一起接住，否则它会变成一个 500，
        # 而用户看到的是"服务器错误"而不是"去掉密码再传"。
        raise AttachmentError("文件读不开。") from exc

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise AttachmentError("文件已损坏。") from exc

    body = root.find(f"{_W}body")
    if body is None:
        raise AttachmentError("文件里没有正文。")

    blocks = _document_blocks(body)
    # 段落之间空一行：模型读起来有层次，而连续空行压成一个。
    text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(block.rstrip() for block in blocks))
    # `root.iter()` 返回的是迭代器，**没有 `len()`** —— 这里曾经写成
    # `len(root.iter(...))`，于是每一次 `.docx` 上传都抛 `TypeError`（→ 500）。
    # 抓它的是 `tests/test_attachments.py`，而不是某次真实上传：
    # 这正是"解析层必须被单独测到每一种输入"的理由。
    tables = sum(1 for _ in root.iter(f"{_W}tbl"))
    pieces = ["按 .docx 正文 XML 读入（取段落与表格）"]
    if tables:
        pieces.append(f"{tables} 张表已按行列拍平，合并单元格会在行里重复")
    pieces.append("不取字体/图片/批注/脚注/修订痕迹")
    return text.strip("\n"), "；".join(pieces)


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def uploads_dir() -> Path:
    """原件存放目录。`MOUXUE_UPLOADS_DIR` 可覆盖 —— **测试靠它隔离**，
    否则测试会往开发者的真实 `var/uploads/` 里写文件。
    """
    override = os.environ.get("MOUXUE_UPLOADS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "var" / "uploads"


def store_original(session_id: str, attachment_id: str, ext: str, raw: bytes) -> str:
    """把原件写到 `<uploads>/<session_id>/<attachment_id><ext>`，返回**相对 uploads 的路径**。

    **落盘先于入库**（调用方负责顺序）：万一数据库那一步失败，留下的是一个
    没人引用的文件；反过来就会是"库里记着有一份原件、磁盘上没有" ——
    后者是数据丢失，前者只是垃圾。两害相权，取垃圾。

    返回值刻意是**相对 `uploads_dir()` 的**，不是相对项目根的：
    `MOUXUE_UPLOADS_DIR` 一旦被覆盖（测试就是这么做的），
    相对项目根的路径立刻失效，而相对 uploads 的永远成立。

    目录名用 `session_id`、文件名用 `attachment_id`：都是我们自己生成的
    `[A-Z]+-\\d+` 形状，**任何一个都不会带上用户的文件名**。
    所以用户文件名里有什么都不可能变成路径。
    """
    directory = uploads_dir() / session_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{attachment_id}{ext}"
    target.write_bytes(raw)
    return f"{session_id}/{attachment_id}{ext}"


def remove_original(stored_path: str) -> bool:
    """删原件。返回是否真的删掉了。

    **删不掉不是错误**：库里那一行才是真相，文件只是副本。
    所以这里不抛异常，只回一个 bool 让调用方按需记一笔。
    """
    try:
        (uploads_dir() / stored_path).unlink(missing_ok=True)
        return True
    except OSError:  # pragma: no cover - 权限/占用等
        return False


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def parse(filename: str, raw: bytes) -> ParsedFile:
    """把一次上传变成 `ParsedFile`。**失败一律抛 `AttachmentError`。"""
    if len(raw) > MAX_BYTES:
        raise AttachmentError(f"文件不能超过 {MAX_BYTES // 1024 // 1024} MB。")

    clean = normalize_filename(filename)
    ext, kind = _check_extension(clean)
    _sniff(raw, clean, kind)

    text, how = _parse_plain(raw, clean) if kind == "plain" else _parse_docx(raw, clean)

    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
        how = f"{how}；**原文件更长，只存了前 {MAX_CHARS} 字**"

    if not text.strip():
        raise AttachmentError("文件里没有可读的文字。")

    return ParsedFile(
        filename=clean,
        ext=ext,
        kind=kind,
        text=text,
        bytes_len=len(raw),
        chars_len=len(text),
        truncated=truncated,
        sha256=hashlib.sha256(raw).hexdigest(),
        note=how,
    )


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """给上下文用的单行预览。

    空白全部压成单个空格：附件里的换行会破坏上下文的分节结构
    （渲染层是按行组装的），而且预览里也不需要保留排版。
    """
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


__all__ = [
    "ALLOWED_EXTENSIONS",
    "AttachmentError",
    "KNOWN_UNSUPPORTED",
    "MAX_BYTES",
    "MAX_CHARS",
    "PREVIEW_CHARS",
    "ParsedFile",
    "extension_of",
    "normalize_filename",
    "parse",
    "preview",
    "remove_original",
    "store_original",
    "uploads_dir",
]
