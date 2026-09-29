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

from .auth import KnowledgeAuthError, is_authorized, require_authorized  # noqa: F401
from .commentary import build_commentary, list_commentaries, save_commentary  # noqa: F401
from .indexer import (  # noqa: F401
    delete_document,
    ingest_bytes,
    ingest_file,
    reindex_all,
    retry_document,
    scan_materials_dir,
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
    """知识库统计（文档数 / 解析失败数 / 日期范围 / 最近入库 / 检索模式）。"""
    if user is not None:
        require_authorized(user, "查看知识库统计")
    from . import config  # noqa: PLC0415
    s = dict(get_store().stats())
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
    "is_authorized",
    "require_authorized",
    "KnowledgeAuthError",
]
