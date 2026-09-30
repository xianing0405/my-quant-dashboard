#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""材料入库管线：解析 → 切分 → 元数据 → 持久化，含状态机与去重 / 版本 / 删除。

状态：待解析 → 处理中 → 可检索 / 解析失败 / 需要OCR。
失败可重试；重复上传（按文件哈希）不重复建索引；同标题新内容视为新版本。
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import config, parsers, storage
from .auth import require_admin
from .models import (
    PARSE_FAILED,
    PARSE_NEEDS_OCR,
    PARSE_PROCESSING,
    PARSE_READY,
    SOURCE_OTHER,
    Document,
    Evidence,
    new_id,
)
from .parsers import Block, NeedsOcrError, ParseError

_TARGET_CHUNK = 800
_MAX_CHUNK = 1600

# 轻量关键词标注（诚实说明：这是关键词建议，可人工修正，非语义实体识别）
_TOPIC_KEYWORDS = [
    "碳化硅", "光纤", "半导体", "存储", "面板", "锂电", "光伏", "风电", "储能",
    "军工", "医药", "消费", "地产", "银行", "券商", "保险", "有色", "钢铁", "煤炭",
    "宏观", "利率", "汇率", "通胀", "流动性", "财政", "货币政策",
]
_COMPANY_HINTS = re.compile(r"(?:公司|集团|股份|银行|证券|汽车|电子|科技|医药|能源|矿业|通信)(?:股份有限公司|有限公司|集团)?")
_DATE_IN_NAME = re.compile(r"(20\d{2})[-_.年]?(\d{1,2})?[-_.月]?(\d{1,2})?日?")


def _suggest_tags(text: str) -> tuple[list[str], list[str]]:
    """关键词建议标签：命中内置主题词表则给出，否则返回空（可人工补）。"""
    topics = [k for k in _TOPIC_KEYWORDS if k in text]
    companies = sorted(set(_COMPANY_HINTS.findall(text)))
    return companies[:10], topics[:10]


def _date_from_name(name: str) -> str | None:
    m = _DATE_IN_NAME.search(name)
    if not m:
        return None
    y = m.group(1)
    mo = (m.group(2) or "01").zfill(2)
    d = (m.group(3) or "01").zfill(2)
    return f"{y}-{mo}-{d}"


def _safe_filename(name: str) -> str:
    """清洗文件名，避免路径穿越 / 特殊字符。"""
    name = Path(name).name
    name = re.sub(r"[^\w一-鿿.\-]", "_", name)
    return name or "document"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 切分：Block → Evidence
# ---------------------------------------------------------------------------
def _split_long(text: str, limit: int = _MAX_CHUNK) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for sent in re.split(r"(?<=[。！？!?\n])", text):
        if len(buf) + len(sent) > limit and buf:
            parts.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        parts.append(buf)
    return parts


def _chunkify(blocks: list[Block], document_id: str) -> list[Evidence]:
    """把 Block 合并/切分成证据片段，保留页码 / 标题 / 发言人 / 位置。"""
    chunks: list[Evidence] = []
    seq = 0
    prev_question_id: str | None = None

    def push(text: str, blk: Block, heading: str | None) -> None:
        nonlocal seq, prev_question_id
        seq += 1
        ev = Evidence(
            evidence_id=f"ev_{document_id[4:]}_{seq:04d}",
            document_id=document_id,
            seq=seq,
            text=text.strip(),
            page_number=blk.page_number,
            position=blk.position,
            heading=blk.heading or heading,
            speaker=blk.speaker,
            ts=blk.ts,
            role=blk.role,
            parent_evidence_id=prev_question_id if blk.role in ("answer", "statement", "view") else None,
            is_ai_generated=False,
        )
        if blk.role == "question":
            prev_question_id = ev.evidence_id
        chunks.append(ev)

    buf = ""
    buf_heading: str | None = None
    buf_page: int | None = None
    buf_pos: str | None = None

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            push(buf, Block(text=buf, page_number=buf_page, heading=buf_heading, position=buf_pos), buf_heading)
        buf = ""

    for blk in blocks:
        # 对话消息：原子片段，不合并
        if blk.speaker:
            flush()
            for piece in _split_long(blk.text):
                push(piece, blk, blk.heading)
            continue
        # 标题行：作为切分边界（保留为独立小片段）
        if blk.position == "标题":
            flush()
            push(blk.text, blk, blk.heading)
            continue
        # 跨页不合并（保证页码准确）
        if buf and blk.page_number is not None and buf_page is not None and blk.page_number != buf_page:
            flush()
        if not buf:
            buf_heading = blk.heading
            buf_page = blk.page_number
            buf_pos = blk.position
        buf += ("\n" if buf else "") + blk.text
        if len(buf) >= _TARGET_CHUNK:
            flush()
    flush()
    return chunks


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def _store_document(doc: Document) -> None:
    store = storage.get_store()
    store.upsert_document(doc)


def ingest_file(
    path: str,
    *,
    user: str | None = None,
    source_category: str = SOURCE_OTHER,
    publish_date: str | None = None,
    author: str | None = None,
    organization: str | None = None,
    companies: list[str] | None = None,
    industry_topics: list[str] | None = None,
    permission_scope: str = "internal",
) -> dict[str, Any]:
    """把单个文件入库。返回 {status, message, document_id, version, deduped}。"""
    require_admin(user, "入库资料")
    store = storage.get_store()
    path = str(path)
    raw = _read(path)
    file_hash = sha256_bytes(raw)
    suffix = Path(path).suffix.lower()
    file_type = suffix.lstrip(".")

    # 去重：相同内容不重复建索引
    existing = store.find_by_hash(file_hash)
    if existing:
        return {"status": "duplicate", "deduped": True,
                "message": f"内容与已入库文件《{existing.title}》（v{existing.version}）相同，未重复入库",
                "document_id": existing.document_id, "version": existing.version}

    name = _safe_filename(Path(path).name)
    title = Path(name).stem
    version = store.max_version(title) + 1
    previous = _latest_document_by_title(title)

    # 发布日期：显式传入优先，否则尝试从文件名推断（记录 provenance）
    if publish_date is None:
        inferred = _date_from_name(name)
    else:
        inferred = None
    publish = publish_date or inferred
    provenance = f"原始文件：{Path(path).name}"
    if inferred and publish_date is None:
        provenance += "；发布日期由文件名推断，待人工核对"

    doc = Document(
        document_id=new_id("doc"),
        title=title,
        file_type=file_type,
        file_hash=file_hash,
        version=version,
        publish_date=publish,
        uploaded_at=storage.now_iso(),
        author=author,
        organization=organization,
        source_category=source_category,
        companies=list(companies or []),
        industry_topics=list(industry_topics or []),
        permission_scope=permission_scope,
        parse_status=PARSE_PROCESSING,
        original_path=str(Path(path).resolve()),
        provenance=provenance,
        previous_version_id=previous.document_id if previous else None,
    )
    _store_document(doc)

    # 解析
    try:
        blocks = parsers.parse_file(path, file_type=file_type)
        if not companies or not industry_topics:
            full_text = "\n".join(b.text for b in blocks)
            auto_c, auto_t = _suggest_tags(full_text)
            doc.companies = list(companies or auto_c)
            doc.industry_topics = list(industry_topics or auto_t)
        chunks = _chunkify(blocks, doc.document_id)
        store.add_chunks(chunks)
        doc.parse_status = PARSE_READY
        doc.error_message = None
        _store_document(doc)
        return {"status": PARSE_READY, "deduped": False, "message": "已入库，可检索",
                "document_id": doc.document_id, "version": version,
                "chunks": len(chunks)}
    except NeedsOcrError as e:
        doc.parse_status = PARSE_NEEDS_OCR
        doc.error_message = str(e)
        _store_document(doc)
        return {"status": PARSE_NEEDS_OCR, "deduped": False,
                "message": f"需要 OCR：{e}", "document_id": doc.document_id, "version": version}
    except ParseError as e:
        doc.parse_status = PARSE_FAILED
        doc.error_message = str(e)
        _store_document(doc)
        return {"status": PARSE_FAILED, "deduped": False,
                "message": f"解析失败：{e}", "document_id": doc.document_id, "version": version}
    except Exception as e:  # noqa: BLE001
        doc.parse_status = PARSE_FAILED
        doc.error_message = f"未知错误：{e}"
        _store_document(doc)
        return {"status": PARSE_FAILED, "deduped": False,
                "message": f"解析失败：{e}", "document_id": doc.document_id, "version": version}


def ingest_bytes(
    filename: str,
    data: bytes,
    *,
    user: str | None = None,
    source_category: str = SOURCE_OTHER,
    publish_date: str | None = None,
    author: str | None = None,
    organization: str | None = None,
    companies: list[str] | None = None,
    industry_topics: list[str] | None = None,
    permission_scope: str = "internal",
) -> dict[str, Any]:
    """Streamlit 上传入口：先把字节保存到资料目录，再走 ingest_file。"""
    require_admin(user, "上传资料")
    materials = config.materials_dir()
    materials.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(filename)
    dest = materials / name
    # 同名不同内容时避免覆盖：加哈希后缀
    if dest.exists():
        stem, suf = Path(name).stem, Path(name).suffix
        dest = materials / f"{stem}_{sha256_bytes(data)[:8]}{suf}"
    dest.write_bytes(data)
    return ingest_file(
        str(dest),
        user=user,
        source_category=source_category,
        publish_date=publish_date,
        author=author,
        organization=organization,
        companies=companies,
        industry_topics=industry_topics,
        permission_scope=permission_scope,
    )


def delete_document(document_id: str, *, user: str | None = None) -> None:
    """删除文档并同步清理其可检索片段与派生观点记录。"""
    require_admin(user, "删除资料")
    store = storage.get_store()
    doc = store.get_document(document_id)
    if doc is None:
        return
    for vp in store.list_viewpoints():
        if vp.document_id == document_id:
            store.delete_viewpoint(vp.viewpoint_id)
    store.delete_document(document_id)


def cleanup_system_docs(*, user: str | None = None) -> dict:
    """清理已入库的系统说明文档（README 等），保留磁盘原文件与真实研报。"""
    require_admin(user, "清理系统说明索引")
    store = storage.get_store()
    removed: list[str] = []
    for d in store.list_documents():
        if _is_instructional(d.title):
            store.delete_document(d.document_id)
            removed.append(d.title)
    return {"removed": removed}


def set_document_public(document_id: str, public: bool, *, user: str | None = None) -> None:
    """管理员开关：是否允许网站访客匿名查询该文档（permission_scope）。

    关闭公开后，该文档立即退出匿名检索范围（检索与读取证据处均检查该标记）。
    """
    require_admin(user, "设置资料公开范围")
    store = storage.get_store()
    doc = store.get_document(document_id)
    if doc is None:
        return
    doc.permission_scope = "public" if public else "internal"
    store.upsert_document(doc)


def retry_document(document_id: str, *, user: str | None = None) -> dict[str, Any]:
    """解析失败 / 需要 OCR 的文档重试（重新走解析管线）。"""
    require_admin(user, "重试解析资料")
    store = storage.get_store()
    doc = store.get_document(document_id)
    if doc is None:
        return {"status": "missing", "message": "文档不存在"}
    if not doc.original_path or not Path(doc.original_path).is_file():
        return {"status": PARSE_FAILED, "message": "原始文件缺失，无法重试"}
    # 先清理旧片段，再重新解析
    store.delete_document(document_id)
    return ingest_file(
        doc.original_path,
        user=user,
        source_category=doc.source_category,
        publish_date=doc.publish_date,
        author=doc.author,
        organization=doc.organization,
        companies=doc.companies,
        industry_topics=doc.industry_topics,
        permission_scope=doc.permission_scope,
    )


def scan_materials_dir(*, user: str | None = None) -> dict[str, Any]:
    """扫描资料目录，把尚未入库的文件入库（仅顶层，不递归）。返回报告。"""
    require_admin(user, "扫描资料目录")
    materials = config.materials_dir()
    if not materials.is_dir():
        return {"scanned": 0, "ingested": 0, "skipped": [], "results": []}

    store = storage.get_store()
    known_hashes = {d.file_hash for d in store.list_documents()}
    supported = {ext for ext in ("pdf", "docx", "md", "markdown", "txt", "rtf", "json")}

    results = []
    ingested = 0
    skipped = []
    scanned = 0
    for p in sorted(materials.iterdir()):
        if not p.is_file():
            continue
        if _is_instructional(p.name):
            skipped.append(f"{p.name}（操作说明/索引，不进入检索）")
            continue
        if p.suffix.lower().lstrip(".") not in supported:
            skipped.append(f"{p.name}（不支持的类型）")
            continue
        scanned += 1
        try:
            h = sha256_bytes(_read(str(p)))
        except OSError:
            skipped.append(f"{p.name}（无法读取）")
            continue
        if h in known_hashes:
            skipped.append(f"{p.name}（已入库）")
            continue
        res = ingest_file(str(p), user=user)
        results.append({"file": p.name, **res})
        if res.get("status") in (PARSE_READY, PARSE_NEEDS_OCR, PARSE_FAILED):
            ingested += 1
    return {"scanned": scanned, "ingested": ingested, "skipped": skipped, "results": results}


SUPPORTED_EXTENSIONS = {"pdf", "docx", "md", "markdown", "txt", "rtf", "json"}


def _is_instructional(name: str) -> bool:
    """仅跳过明确的 README 系统说明文件；不误伤文件名含「说明/索引」的正式资料。"""
    return Path(name).stem.lower() == "readme"


def scan_folder_recursive(
    folder: str,
    *,
    user: str | None = None,
    root_category: str | None = None,
) -> dict[str, Any]:
    """递归批量导入一个文件夹（保留相对路径与子文件夹分类标签）。

    - 跳过隐藏文件 / 符号链接 / 不支持格式（不支持格式单独列出）。
    - 单个文件失败不中断整批；重复内容（按哈希）不重复入库。
    - root_category 作为整批主题分类标签；子文件夹名再追加为更细分类。
    - 不据此编造作者/机构/日期。
    返回 {scanned, ingested, duplicate, failed, ocr, unsupported, results}。
    """
    require_admin(user, "批量导入资料")
    root = Path(folder)
    empty = {"scanned": 0, "ingested": 0, "duplicate": 0, "failed": 0,
             "ocr": 0, "unsupported": [], "results": []}
    if not root.is_dir():
        return empty

    store = storage.get_store()
    known_hashes = {d.file_hash for d in store.list_documents()}

    scanned = ingested = duplicate = failed = ocr = 0
    unsupported: list[str] = []
    results: list[dict] = []

    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        if p.name.startswith("."):
            continue
        if _is_instructional(p.name):
            continue
        rel = p.relative_to(root)
        ext = p.suffix.lower().lstrip(".")
        if ext not in SUPPORTED_EXTENSIONS:
            unsupported.append(str(rel))
            continue
        scanned += 1
        try:
            h = sha256_bytes(_read(str(p)))
        except OSError:
            failed += 1
            results.append({"file": str(rel), "status": "失败", "message": "无法读取"})
            continue
        if h in known_hashes:
            duplicate += 1
            results.append({"file": str(rel), "status": "duplicate", "message": "已入库（按内容哈希）"})
            continue
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        tags: list[str] = []
        if root_category:
            tags.append(root_category)
        if category:
            tags.append(category)
        res = ingest_file(str(p), user=user, industry_topics=tags or None)
        results.append({"file": str(rel), **res})
        st = res.get("status")
        if st in (PARSE_READY, PARSE_NEEDS_OCR, PARSE_FAILED):
            known_hashes.add(h)
        if st == PARSE_READY:
            ingested += 1
        elif st == PARSE_NEEDS_OCR:
            ocr += 1
        elif st == "duplicate":
            duplicate += 1
        else:
            failed += 1

    return {"scanned": scanned, "ingested": ingested, "duplicate": duplicate,
            "failed": failed, "ocr": ocr, "unsupported": unsupported, "results": results}


def import_zip_bytes(
    data: bytes,
    *,
    user: str | None = None,
    max_files: int = 5000,
    max_total_bytes: int = 8 * 1024 ** 3,
) -> dict[str, Any]:
    """管理员 ZIP 批量导入：解压到资料目录（保留结构）后递归入库。

    安全检查：路径越界（绝对路径 / ..）、文件数上限、解压总大小上限。
    返回与 scan_folder_recursive 一致的报告，外加 ok / error。
    """
    require_admin(user, "ZIP 批量导入")
    materials = config.materials_dir()
    materials.mkdir(parents=True, exist_ok=True)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是有效的 ZIP 文件"}

    entries = [e for e in zf.infolist() if not e.is_dir()]
    if len(entries) > max_files:
        zf.close()
        return {"ok": False, "error": f"ZIP 内文件数 {len(entries)} 超过上限 {max_files}"}
    total = sum(e.file_size for e in entries)
    if total > max_total_bytes:
        zf.close()
        return {"ok": False, "error": f"解压后总大小 {total / 1e9:.1f} GB 超过上限 {max_total_bytes / 1e9:.0f} GB"}

    extracted_dirs: set[str] = set()
    try:
        for e in entries:
            norm = Path(e.filename)
            if norm.is_absolute() or ".." in norm.parts:
                return {"ok": False, "error": f"ZIP 包含非法路径（越界）：{e.filename}"}
            if norm.suffix.lower().lstrip(".") not in SUPPORTED_EXTENSIONS:
                continue
            dest = materials / norm
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(e) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted_dirs.add(norm.parts[0] if len(norm.parts) > 1 else ".")
    finally:
        zf.close()

    if not extracted_dirs:
        return {"ok": False, "error": "ZIP 内没有可导入的支持格式文件（pdf/docx/md/txt/rtf/json）"}

    report = {"scanned": 0, "ingested": 0, "duplicate": 0, "failed": 0,
              "ocr": 0, "unsupported": [], "results": []}
    for d in sorted(extracted_dirs):
        target = materials if d == "." else materials / d
        r = scan_folder_recursive(str(target), user=user)
        for k in ("scanned", "ingested", "duplicate", "failed", "ocr"):
            report[k] += r.get(k, 0)
        report["unsupported"] += r.get("unsupported", [])
        report["results"] += r.get("results", [])
    report["ok"] = True
    return report


def reindex_all(*, user: str | None = None) -> dict[str, Any]:
    """重建全部索引（清空片段后按现有文档重解析）。用于结构变更后的迁移。"""
    require_admin(user, "重建索引")
    store = storage.get_store()
    docs = store.list_documents()
    store.delete_document("__none__")  # no-op，保证连接初始化
    # 逐文档重解析
    report = []
    for d in docs:
        if d.original_path and Path(d.original_path).is_file():
            store.delete_document(d.document_id)
            report.append({d.title: ingest_file(
                d.original_path, user=user, source_category=d.source_category,
                publish_date=d.publish_date, author=d.author,
                organization=d.organization, companies=d.companies,
                industry_topics=d.industry_topics, permission_scope=d.permission_scope)})
    return {"reindexed": len(report), "report": report}


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _latest_document_by_title(title: str) -> Document | None:
    docs = storage.get_store().list_documents()
    for d in docs:
        if d.title == title and d.version == storage.get_store().max_version(title):
            return d
    return None
