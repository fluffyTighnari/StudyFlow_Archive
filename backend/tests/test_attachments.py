"""附件解析层 —— **真实世界的脏输入**。

这一组测试的存在理由：解析是这个功能里唯一会被"用户实际传上来的东西"打脸的地方。
编码、伪装的文件、加密的 docx、老 .doc、空文件、超大文件 ——
每一种都必须有一条测试，而且**测的是"说了什么"，不只是"报没报错"**。

因为这里的失败信息**就是给用户看的那句话**：
用户看到"服务器内部错误"和看到"这是一个 `.doc`，请另存为 `.docx` 再传"，
差别是能不能自己解决。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.attachments import (
    MAX_BYTES,
    MAX_CHARS,
    AttachmentError,
    extension_of,
    normalize_filename,
    parse,
    preview,
)

# --------------------------------------------------------------------------- #
# 造文件的小工具（不依赖 Word，也不往磁盘写东西）
# --------------------------------------------------------------------------- #

_W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    # `r:id` 要用到 `r` 命名空间。真实的 Word 文件一定声明了它 ——
    # 第一版 fixture 忘了，于是"文件损坏"，而那是**测试自己的**问题。
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def docx_bytes(*blocks: str) -> bytes:
    """按最小可解析的形状造一个 `.docx`。

    `blocks` 里以 `<tbl>` 开头的按表格字面量塞进去，其余当一个段落。
    刻意**不用 python-docx 造**：测试要能跑在"只有标准库"的环境里，
    而且这样造出来的字节就是我们解析器要面对的那一层原始 XML。
    """
    body = []
    for block in blocks:
        if block.startswith("<w:tbl>"):
            body.append(block)
        else:
            body.append(f'<w:p><w:r><w:t xml:space="preserve">{block}</w:t></w:r></w:p>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_W_NS}><w:body>{''.join(body)}</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 编码：**读成乱码比报错更坏**
# --------------------------------------------------------------------------- #


def test_utf8_with_bom_loses_the_bom() -> None:
    """带 BOM 的 UTF-8 很常见（Windows 记事本默认）。

    留着 BOM 的后果很隐蔽：正文第一个字符是个看不见的 `\\ufeff`，
    **模型读到它不会报错**，只会在某些判断上莫名其妙
    （比如它引用文件名/第一行时多一个怪字符，或者"以什么开头"的判断失效）。
    """
    parsed = parse("经历.txt", "\ufeff我讨厌看网课".encode("utf-8"))
    assert parsed.text == "我讨厌看网课"
    assert not parsed.text.startswith("\ufeff")
    assert "utf-8-sig" in parsed.note


def test_gbk_text_is_read_not_mangled() -> None:
    """中文用户的 `.txt` 很可能是 GBK。

    **读成乱码比报错更坏**：乱码会一路进上下文、进模型的判断，
    而没有任何人会发现；报错至少会让人换一个文件。
    """
    parsed = parse("计划.txt", "每天最多学 30 分钟".encode("gbk"))
    assert parsed.text == "每天最多学 30 分钟"
    assert "gb18030" in parsed.note


def test_binary_disguised_as_txt_is_rejected() -> None:
    """一个改了扩展名的二进制文件不能"解码成功成一段乱码"。

    GB18030 几乎不拒绝任何字节序列，所以没有嗅探就等于把二进制
    变成乱码正文送进上下文。这里断言的是**真的被拒了**，
    而不只是"没崩"。
    """
    with pytest.raises(AttachmentError) as excinfo:
        parse("照片.txt", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4)
    assert "二进制" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 伪装的文件：按**内容**判断，不信扩展名
# --------------------------------------------------------------------------- #


def test_old_doc_renamed_to_docx_is_caught_by_content() -> None:
    """老 `.doc` 改扩展名成 `.docx` —— 内容说了算。

    只看扩展名的话，这个文件会被交给 zip 解析器，然后报一句
    "zip 读不开" —— 而真相是"这是老格式，去另存一下"。
    """
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(AttachmentError) as excinfo:
        parse("报告.docx", ole2)
    assert "OLE2" in str(excinfo.value)
    assert "另存为" in str(excinfo.value)


def test_old_doc_extension_is_rejected_with_a_next_step() -> None:
    """`.doc` 直接拒绝，而且**必须告诉用户下一步做什么**。"""
    with pytest.raises(AttachmentError) as excinfo:
        parse("报告.doc", b"anything")
    message = str(excinfo.value)
    assert "另存为" in message
    assert ".docx" in message


def test_pdf_renamed_to_txt_is_caught() -> None:
    with pytest.raises(AttachmentError) as excinfo:
        parse("论文.txt", b"%PDF-1.7\n" + b"x" * 100)
    assert "PDF" in str(excinfo.value)


def test_unknown_extension_lists_what_is_allowed() -> None:
    with pytest.raises(AttachmentError) as excinfo:
        parse("数据.xlsx", b"whatever")
    message = str(excinfo.value)
    assert ".txt" in message and ".md" in message and ".docx" in message


def test_empty_file_is_rejected() -> None:
    with pytest.raises(AttachmentError) as excinfo:
        parse("空的.md", b"   \n  ")
    assert "空文件" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# .docx
# --------------------------------------------------------------------------- #


def test_docx_paragraphs_in_document_order() -> None:
    parsed = parse("经历.docx", docx_bytes("第一段", "第二段", "第三段"))
    assert parsed.kind == "docx"
    assert parsed.text.split("\n\n") == ["第一段", "第二段", "第三段"]


def test_docx_table_is_flattened_to_rows() -> None:
    """表格按行列拍平 —— 这是"取什么"的一半，另一半（不取什么）也要能看见。"""
    table = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>科目</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>时长</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>英语</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>30 分钟</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    parsed = parse("安排.docx", docx_bytes("先看这张表：", table, "表后面还有一句"))
    assert "| 科目 | 时长 |" in parsed.text
    assert "| 英语 | 30 分钟 |" in parsed.text
    # 表格内容**不许重复出现**（既在表里、又当普通段落再出现一次）。
    assert parsed.text.count("英语") == 1
    assert "1 张表已按行列拍平" in parsed.note


def test_docx_text_in_hyperlinks_and_tabs_is_kept() -> None:
    """文本常被包在 `w:hyperlink` / `w:smartTag` 里，而制表符要还原。

    `findall("w:t")` 只找直接子节点，会**静默漏掉**这些 ——
    漏掉的表现是"文件读出来少了几段"，而没人会想到是解析器的问题。
    """
    paragraph = (
        "<w:p>"
        '<w:hyperlink r:id="r1"><w:r><w:t>链接文字</w:t></w:r></w:hyperlink>'
        "<w:r><w:t>甲</w:t><w:tab/><w:t>乙</w:t></w:r>"
        "</w:p>"
    )
    parsed = parse("混合.docx", docx_bytes(paragraph))
    assert "链接文字" in parsed.text
    assert "甲\t乙" in parsed.text


def test_docx_note_says_what_is_not_taken() -> None:
    """"不取什么"必须让用户与模型都看得见 —— 它是保真度的边界，不是缺陷。"""
    parsed = parse("简单.docx", docx_bytes("一句话"))
    assert "不取" in parsed.note


def test_docx_that_is_not_a_zip_says_so() -> None:
    with pytest.raises(AttachmentError) as excinfo:
        parse("假的.docx", b"this is plain text, not a zip at all")
    assert ".docx" in str(excinfo.value)


def test_zip_without_document_part_says_so() -> None:
    """一个 zip，但没有 `word/document.xml` —— 不是 Word 文档。"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(AttachmentError) as excinfo:
        parse("别的.docx", buffer.getvalue())
    assert "word/document.xml" in str(excinfo.value)


def test_docx_with_no_readable_text_says_so() -> None:
    """全是空段落的 docx：没有内容可读，不该变成一条空资料。"""
    with pytest.raises(AttachmentError) as excinfo:
        parse("空文档.docx", docx_bytes("", ""))
    assert "没有可读的文字" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 上限与截断：**必须显式标记**
# --------------------------------------------------------------------------- #


def test_oversized_file_is_rejected_with_both_numbers() -> None:
    with pytest.raises(AttachmentError) as excinfo:
        parse("巨大.txt", b"a" * (MAX_BYTES + 1))
    message = str(excinfo.value)
    assert "MB" in message and "上限" in message


def test_long_text_is_truncated_and_marked() -> None:
    """截断**必须**落进 `truncated` 与 `note`。

    不然模型会把半份材料当成完整的 —— 这与"prompt 装不下"是同一类失败：
    它以为看到了全部，然后基于一个不完整的事实下结论。
    """
    parsed = parse("很长.md", ("字" * (MAX_CHARS + 500)).encode())
    assert parsed.truncated is True
    assert parsed.chars_len == MAX_CHARS
    assert "只存了前" in parsed.note


def test_short_text_is_not_marked_truncated() -> None:
    parsed = parse("短.md", "只有一句话".encode())
    assert parsed.truncated is False
    assert "只存了前" not in parsed.note


# --------------------------------------------------------------------------- #
# 文件名
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("经历.md", "经历.md"),
        ("C:\\Users\\me\\经历.md", "经历.md"),
        ("/tmp/uploads/经历.md", "经历.md"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("  .hidden.md  ", "hidden.md"),
        ("", "未命名"),
        (None, "未命名"),
    ],
)
def test_filename_is_sanitized(raw: str | None, expected: str) -> None:
    """文件名只是显示用 —— **但"只是显示用"不等于可以不洗**。

    `../../etc/passwd.txt` 洗成 `passwd.txt` 的意义不在于防住路径穿越
    （落盘名是 `AT-0001.md`，用户文件名根本进不了路径），
    而在于界面上不该出现一段看不懂的路径，也别让人以为自己穿越成功了。
    """
    assert normalize_filename(raw) == expected


def test_long_filename_keeps_its_extension() -> None:
    """截断**不能把扩展名切掉** —— 那会改变解析路径（进而改变行为）。"""
    name = normalize_filename("字" * 300 + ".md")
    assert name.endswith(".md")
    assert len(name) <= 120


def test_extension_of_is_case_insensitive() -> None:
    assert extension_of("经历.MD") == ".md"
    assert extension_of("报告.DocX") == ".docx"


# --------------------------------------------------------------------------- #
# 预览（界面与模型看到的是同一个函数产出的）
# --------------------------------------------------------------------------- #


def test_preview_collapses_whitespace_and_truncates() -> None:
    """预览必须是单行：渲染层是**按行**组装上下文的，多行预览会破坏分节结构。"""
    assert preview("第一行\n\n第二行\t第三行") == "第一行 第二行 第三行"
    long = preview("字" * 400)
    assert long.endswith("…")
    assert len(long) == 151


def test_parse_records_digest_and_sizes() -> None:
    raw = "同样的内容".encode()
    parsed = parse("a.md", raw)
    assert parsed.sha256 == parse("b.md", raw).sha256
    assert parsed.bytes_len == len(raw)
    assert parsed.chars_len == len("同样的内容")
