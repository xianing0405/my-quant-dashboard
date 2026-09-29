#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""材料解析层：把原始文件解析为带定位信息的文本块（Block）。

支持：
  - PDF（有文本层；无文本层判定为需要 OCR）
  - DOCX（段落 + 表格，标题层级）
  - Markdown / TXT / RTF（标题 + 段落）
  - 对话记录（TXT 约定格式 / JSON 约定格式，保留发言人、时间、问答关系）

定位信息原则：PDF 记录真实页码；非 PDF 用标题 / 段落序号 / 消息序号，
绝不编造页码。
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ParseError(RuntimeError):
    pass


class NeedsOcrError(RuntimeError):
    """PDF 无文本层，需要 OCR。"""


@dataclass
class Block:
    text: str
    page_number: int | None = None
    heading: str | None = None
    speaker: str | None = None
    ts: str | None = None
    role: str | None = None            # question / answer / statement / view
    position: str | None = None        # 标题 / 段落N / 表格 / 消息N
    is_table: bool = False
    note: str | None = None            # 解析提示（如「请核对表格」）


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _detect_text_encoding(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


# RTF（复用既有标准库解码逻辑，避免重复实现）
def _rtf_to_text(raw: bytes) -> str:
    from .local_rag import _rtf_to_text as _rtf_decode  # noqa: PLC0415
    return _rtf_decode(raw.decode("latin-1"))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def parse_pdf(raw: bytes) -> list[Block]:
    """解析带文本层的 PDF，返回按页的文本块；无文本层抛 NeedsOcrError。"""
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise ParseError("缺少依赖 pypdf，无法解析 PDF") from e

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"PDF 无法打开：{e}") from e

    blocks: list[Block] = []
    total_text = ""
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        total_text += text
        for para in _split_paragraphs(text):
            blocks.append(Block(text=para, page_number=idx, position=f"第{idx}页"))

    if len(total_text.strip()) < 20:
        raise NeedsOcrError("PDF 未提取到文本层（可能为扫描件），需要 OCR")
    return blocks


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def parse_docx(raw: bytes) -> list[Block]:
    try:
        from docx import Document as DocxDocument  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise ParseError("缺少依赖 python-docx，无法解析 DOCX") from e

    try:
        doc = DocxDocument(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"DOCX 无法打开：{e}") from e

    blocks: list[Block] = []
    current_heading: str | None = None
    para_no = 0

    def _style_is_heading(style_name: str) -> bool:
        n = (style_name or "").lower()
        return n.startswith("heading") or "标题" in n or n.startswith("title")

    # 按文档顺序遍历段落与表格（python-docx 需用 body 元素顺序）
    from docx.oxml.ns import qn  # noqa: PLC0415

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            from docx.text.paragraph import Paragraph  # noqa: PLC0415
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            if _style_is_heading(p.style.name or ""):
                current_heading = text
                blocks.append(Block(text=text, heading=current_heading, position="标题"))
            else:
                para_no += 1
                blocks.append(Block(text=text, heading=current_heading,
                                    position=f"段落{para_no}"))
        elif child.tag == qn("w:tbl"):
            from docx.table import Table  # noqa: PLC0415
            tbl = Table(child, doc)
            table_text = _table_to_text(tbl)
            if table_text:
                blocks.append(Block(text=table_text, heading=current_heading,
                                    position="表格", is_table=True,
                                    note="表格已转文本，请核对表头/单位/脚注"))

    if not blocks:
        raise ParseError("DOCX 未提取到任何文本内容")
    return blocks


def _table_to_text(tbl: Any) -> str:
    lines: list[str] = []
    try:
        rows = tbl.rows
    except Exception:  # noqa: BLE001
        return ""
    for i, row in enumerate(rows):
        cells = []
        for cell in row.cells:
            cells.append(" ".join(cell.text.split()))
        line = " | ".join(cells).strip()
        if line.strip("| "):
            lines.append(line)
        if i == 0 and rows:
            lines.append(" | ".join(["---"] * len(cells)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 文本 / Markdown / RTF
# ---------------------------------------------------------------------------
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def parse_text(raw: bytes, *, suffix: str) -> list[Block]:
    if suffix == ".rtf":
        text = _rtf_to_text(raw)
    else:
        text = _detect_text_encoding(raw)

    blocks: list[Block] = []
    current_heading: str | None = None
    para_no = 0
    for para in _split_paragraphs(text):
        m = _MD_HEADING_RE.match(para.strip())
        if m:
            current_heading = m.group(2).strip()
            blocks.append(Block(text=m.group(2).strip(), heading=current_heading, position="标题"))
            continue
        para_no += 1
        blocks.append(Block(text=para, heading=current_heading, position=f"段落{para_no}"))

    if not blocks:
        raise ParseError("未提取到任何文本内容")
    return blocks


def _split_paragraphs(text: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text or "")
    parts = re.split(r"\n\s*\n|(?=\n#{1,6}\s)", text)
    out = []
    for p in parts:
        p = p.strip()
        if p and p not in ("---",):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# 对话记录
# ---------------------------------------------------------------------------
# TXT 约定格式：
#   [2026-09-16 14:30] 张三（提问）：<内容>
#   张三（回答）：<内容>
#   张三：<内容>            （无时间 / 无角色，按内容推断）
_SPEAKER_RE = re.compile(
    r"^(?:\[([^\]]*)\]\s*)?([^\s：:（）()]{1,20})(?:（([^）]*)）)?\s*[:：]\s*(.*)$"
)
_ROLE_MAP = {"提问": "question", "问": "question", "问题": "question",
             "回答": "answer", "答": "answer", "回复": "answer",
             "观点": "view", "判断": "view"}


def parse_dialogue_txt(raw: bytes) -> list[Block]:
    text = _detect_text_encoding(raw)
    blocks: list[Block] = []
    msg_no = 0
    lines = text.splitlines()
    matched = 0
    total = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        total += 1
        m = _SPEAKER_RE.match(line)
        if not m:
            continue
        matched += 1
        ts, speaker, role_raw, content = m.groups()
        role = _ROLE_MAP.get((role_raw or "").strip(), "statement")
        if role == "statement" and (content or "").strip().endswith("?"):
            role = "question"
        msg_no += 1
        blocks.append(Block(text=content.strip(), speaker=speaker, ts=ts,
                            role=role, position=f"消息{msg_no}"))

    if not blocks:
        raise ParseError("未识别到对话记录（请按约定格式：`发言人：内容`，可带 `[时间]` 与 `（角色）`）")
    return blocks


def parse_dialogue_json(raw: bytes) -> list[Block]:
    text = _detect_text_encoding(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(f"JSON 无法解析：{e}") from e

    msgs = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(msgs, list):
        raise ParseError("对话 JSON 需包含 `messages` 列表")

    blocks: list[Block] = []
    for i, m in enumerate(msgs, 1):
        if not isinstance(m, dict):
            continue
        blocks.append(Block(
            text=str(m.get("text") or m.get("content") or "").strip(),
            speaker=m.get("speaker") or m.get("from") or m.get("name"),
            ts=str(m.get("time") or m.get("ts") or "").strip() or None,
            role=m.get("role") or "statement",
            position=f"消息{i}",
        ))
    if not blocks:
        raise ParseError("对话 JSON 的 messages 列表为空")
    return blocks


def _looks_like_dialogue(raw: bytes, suffix: str) -> bool:
    if suffix == ".json":
        text = _detect_text_encoding(raw)
        try:
            data = json.loads(text)
            return (isinstance(data, dict) and ("messages" in data or data.get("format") == "dialogue")) \
                or (isinstance(data, list) and data and isinstance(data[0], dict) and ("text" in data[0] or "content" in data[0]))
        except json.JSONDecodeError:
            return False
    # TXT：行中带发言人： 的比例较高即视为对话
    text = _detect_text_encoding(raw)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return False
    hits = sum(1 for ln in lines if _SPEAKER_RE.match(ln))
    return hits >= max(2, len(lines) * 0.5)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def parse_file(path: str, *, file_type: str | None = None) -> list[Block]:
    """按扩展名解析文件，返回 Block 列表；无法解析抛 ParseError / NeedsOcrError。"""
    suffix = Path(path).suffix.lower()
    file_type = (file_type or suffix.lstrip(".")).lower()
    raw = _read_bytes(path)

    if file_type in ("pdf",):
        return parse_pdf(raw)
    if file_type in ("docx",):
        return parse_docx(raw)
    if file_type in ("json",) or (suffix == ".json"):
        return parse_dialogue_json(raw)
    if file_type in ("txt", "md", "markdown", "rtf"):
        if _looks_like_dialogue(raw, suffix):
            return parse_dialogue_txt(raw)
        return parse_text(raw, suffix=suffix)

    raise ParseError(f"不支持的文件类型：{file_type}")
