#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库（RAG 问答助手）服务层。

对外暴露稳定服务函数，供 Streamlit 页面与 Agent 调用。所有函数显式接收
用户身份与筛选条件，权限在服务层执行（不在页面层）。

核心三函数（Agent 调用入口）：
    search_materials(query, user, *, top_k, filters)   -> 检索证据
    read_evidence(evidence_id, user, *, context_radius) -> 读取原文与上下文
    get_viewpoint_history(subject, user, *, cutoff_date, status) -> 历史观点
"""

from .auth import (  # noqa: F401
    KnowledgeAuthError,
    is_admin,
    is_authorized,
    require_admin,
    require_authorized,
    require_read,
)
from .commentary import build_commentary, list_commentaries, save_commentary  # noqa: F401
from .indexer import (  # noqa: F401
    delete_document,
    ingest_bytes,
    ingest_file,
    reindex_all,
    retry_document,
    scan_materials_dir,
    set_document_public,
)
from .retriever import (  # noqa: F401
    document_evidence,
    list_documents,
    read_evidence,
    search_materials,
)
from .storage import get_store  # noqa: F401
from .viewpoints import (  # noqa: F401
    add_viewpoint,
    delete_viewpoint,
    extract_viewpoints_from_document,
    get_viewpoint,
    get_viewpoint_history,
    set_viewpoint_status,
    update_viewpoint,
    viewpoint_with_evidence,
)


def stats(user: str | None = None) -> dict:
    """知识库统计。匿名只读时仅统计「已标记公开」的文档；管理员为全量。"""
    scope = require_read(user, "查看知识库统计")
    from . import config  # noqa: PLC0415
    store = get_store()
    if scope == "public":
        docs = [d for d in store.list_documents() if d.permission_scope == "public"]
        s = {
            "documents": len(docs),
            "parse_failed": 0,
            "chunks": 0,
            "viewpoints": 0,
            "date_min": None,
            "date_max": None,
            "date_unknown": 0,
            "latest_ready_at": None,
            "public_only": True,
        }
    else:
        s = dict(store.stats())
        s["public_only"] = False
    s["retrieval_mode"] = config.retrieval_mode()
    s["access_mode"] = config.access_mode()
    s["storage_backend"] = config.storage_backend()
    s["materials_dir"] = str(config.materials_dir())
    return s


__all__ = [
    "search_materials",
    "read_evidence",
    "get_viewpoint_history",
    "list_documents",
    "document_evidence",
    "ingest_file",
    "ingest_bytes",
    "delete_document",
    "retry_document",
    "scan_materials_dir",
    "reindex_all",
    "set_document_public",
    "add_viewpoint",
    "extract_viewpoints_from_document",
    "set_viewpoint_status",
    "update_viewpoint",
    "delete_viewpoint",
    "viewpoint_with_evidence",
    "build_commentary",
    "save_commentary",
    "list_commentaries",
    "stats",
    "is_admin",
    "is_authorized",
    "require_admin",
    "require_authorized",
    "require_read",
    "KnowledgeAuthError",
]
