#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库持久化层。

第一版实现 `LocalSqliteStorage`（本地 SQLite，开发 / 单机模式）。上层通过
`StorageAdapter` 接口访问，云端需要可靠持久化时替换后端即可（对象存储 + 托管
数据库），服务层与页面层无需改动。

注意：Streamlit 社区云的文件系统是临时的，重启 / 重新部署可能丢失。local 后端
仅适合本地开发；云上必须将 KNOWLEDGE_STORE_DIR 指向挂载的持久化卷或替换后端。
"""

from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from . import config
from .models import Document, Evidence, Viewpoint


class StorageError(RuntimeError):
    pass


class StorageAdapter(ABC):
    """持久化存储接口（文档 / 片段 / 观点 / 点评 / 统计）。"""

    @abstractmethod
    def upsert_document(self, doc: Document) -> None: ...

    @abstractmethod
    def get_document(self, document_id: str) -> Document | None: ...

    @abstractmethod
    def list_documents(self) -> list[Document]: ...

    @abstractmethod
    def delete_document(self, document_id: str) -> None: ...

    @abstractmethod
    def find_by_hash(self, file_hash: str) -> Document | None: ...

    @abstractmethod
    def max_version(self, title: str) -> int: ...

    @abstractmethod
    def add_chunks(self, chunks: list[Evidence]) -> None: ...

    @abstractmethod
    def list_chunks(self, document_id: str) -> list[Evidence]: ...

    @abstractmethod
    def all_chunks(self) -> list[Evidence]: ...

    @abstractmethod
    def add_viewpoint(self, vp: Viewpoint) -> None: ...

    @abstractmethod
    def get_viewpoint(self, viewpoint_id: str) -> Viewpoint | None: ...

    @abstractmethod
    def list_viewpoints(self) -> list[Viewpoint]: ...

    @abstractmethod
    def update_viewpoint(self, vp: Viewpoint) -> None: ...

    @abstractmethod
    def delete_viewpoint(self, viewpoint_id: str) -> None: ...

    @abstractmethod
    def add_commentary(self, commentary_id: str, payload: dict) -> None: ...

    @abstractmethod
    def list_commentaries(self) -> list[dict]: ...

    @abstractmethod
    def stats(self) -> dict: ...


# ---------------------------------------------------------------------------
# JSON 字段编解码
# ---------------------------------------------------------------------------
def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _load(s: str | None, default: Any) -> Any:
    if s is None:
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 本地 SQLite 后端
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    publish_date TEXT,
    uploaded_at TEXT,
    author TEXT,
    organization TEXT,
    source_category TEXT,
    companies TEXT,
    industry_topics TEXT,
    permission_scope TEXT NOT NULL DEFAULT 'internal',
    parse_status TEXT NOT NULL,
    error_message TEXT,
    original_path TEXT,
    provenance TEXT,
    previous_version_id TEXT,
    ai_generated_note TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_docs_title ON documents(title);

CREATE TABLE IF NOT EXISTS chunks (
    evidence_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    page_number INTEGER,
    position TEXT,
    heading TEXT,
    speaker TEXT,
    ts TEXT,
    role TEXT,
    parent_evidence_id TEXT,
    is_ai_generated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, seq);

CREATE TABLE IF NOT EXISTS viewpoints (
    viewpoint_id TEXT PRIMARY KEY,
    research_subject TEXT,
    proposer TEXT,
    source_category TEXT,
    viewpoint_date TEXT,
    core_judgment TEXT,
    supporting_evidence TEXT,
    conditions TEXT,
    risks TEXT,
    evidence_ids TEXT,
    status TEXT,
    relationships TEXT,
    document_id TEXT,
    is_ai_generated INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_vp_subject ON viewpoints(research_subject);

CREATE TABLE IF NOT EXISTS commentaries (
    commentary_id TEXT PRIMARY KEY,
    event_text TEXT,
    subject TEXT,
    cutoff_date TEXT,
    output_markdown TEXT,
    source_category TEXT NOT NULL DEFAULT 'AI生成内容',
    created_at TEXT
);
"""


class LocalSqliteStorage(StorageAdapter):
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- documents ----
    def upsert_document(self, doc: Document) -> None:
        d = doc.to_dict()
        d["companies"] = _dump(d["companies"])
        d["industry_topics"] = _dump(d["industry_topics"])
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO documents
                   (document_id, title, file_type, file_hash, version, publish_date,
                    uploaded_at, author, organization, source_category, companies,
                    industry_topics, permission_scope, parse_status, error_message,
                    original_path, provenance, previous_version_id, ai_generated_note, created_at)
                   VALUES
                   (:document_id, :title, :file_type, :file_hash, :version, :publish_date,
                    :uploaded_at, :author, :organization, :source_category, :companies,
                    :industry_topics, :permission_scope, :parse_status, :error_message,
                    :original_path, :provenance, :previous_version_id, :ai_generated_note, :created_at)""",
                d,
            )

    def _row_to_document(self, row: sqlite3.Row) -> Document:
        d = dict(row)
        d["companies"] = _load(d.get("companies"), [])
        d["industry_topics"] = _load(d.get("industry_topics"), [])
        return Document.from_dict(d)

    def get_document(self, document_id: str) -> Document | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        return self._row_to_document(row) if row else None

    def list_documents(self) -> list[Document]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM documents ORDER BY COALESCE(uploaded_at, created_at) DESC").fetchall()
        return [self._row_to_document(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            c.execute("DELETE FROM documents WHERE document_id=?", (document_id,))

    def find_by_hash(self, file_hash: str) -> Document | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM documents WHERE file_hash=? LIMIT 1", (file_hash,)).fetchone()
        return self._row_to_document(row) if row else None

    def max_version(self, title: str) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(MAX(version), 0) FROM documents WHERE title=?", (title,)).fetchone()
        return int(row[0])

    # ---- chunks ----
    def add_chunks(self, chunks: list[Evidence]) -> None:
        rows = []
        for ch in chunks:
            d = ch.to_dict()
            d["is_ai_generated"] = 1 if d["is_ai_generated"] else 0
            rows.append(d)
        with self._conn() as c:
            c.executemany(
                """INSERT OR REPLACE INTO chunks
                   (evidence_id, document_id, seq, text, page_number, position, heading,
                    speaker, ts, role, parent_evidence_id, is_ai_generated)
                   VALUES
                   (:evidence_id, :document_id, :seq, :text, :page_number, :position, :heading,
                    :speaker, :ts, :role, :parent_evidence_id, :is_ai_generated)""",
                rows,
            )

    def _row_to_evidence(self, row: sqlite3.Row) -> Evidence:
        d = dict(row)
        d["is_ai_generated"] = bool(d.get("is_ai_generated"))
        return Evidence(**{k: v for k, v in d.items() if k in Evidence.__dataclass_fields__})

    def list_chunks(self, document_id: str) -> list[Evidence]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM chunks WHERE document_id=? ORDER BY seq", (document_id,)).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def all_chunks(self) -> list[Evidence]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM chunks ORDER BY document_id, seq").fetchall()
        return [self._row_to_evidence(r) for r in rows]

    # ---- viewpoints ----
    def add_viewpoint(self, vp: Viewpoint) -> None:
        d = vp.to_dict()
        d["evidence_ids"] = _dump(d["evidence_ids"])
        d["relationships"] = _dump(d["relationships"])
        d["is_ai_generated"] = 1 if d["is_ai_generated"] else 0
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO viewpoints
                   (viewpoint_id, research_subject, proposer, source_category, viewpoint_date,
                    core_judgment, supporting_evidence, conditions, risks, evidence_ids, status,
                    relationships, document_id, is_ai_generated, created_at, updated_at)
                   VALUES
                   (:viewpoint_id, :research_subject, :proposer, :source_category, :viewpoint_date,
                    :core_judgment, :supporting_evidence, :conditions, :risks, :evidence_ids, :status,
                    :relationships, :document_id, :is_ai_generated, :created_at, :updated_at)""",
                d,
            )

    def _row_to_viewpoint(self, row: sqlite3.Row) -> Viewpoint:
        d = dict(row)
        d["evidence_ids"] = _load(d.get("evidence_ids"), [])
        d["relationships"] = _load(d.get("relationships"), [])
        d["is_ai_generated"] = bool(d.get("is_ai_generated"))
        return Viewpoint.from_dict(d)

    def get_viewpoint(self, viewpoint_id: str) -> Viewpoint | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM viewpoints WHERE viewpoint_id=?", (viewpoint_id,)).fetchone()
        return self._row_to_viewpoint(row) if row else None

    def list_viewpoints(self) -> list[Viewpoint]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM viewpoints ORDER BY COALESCE(viewpoint_date, created_at, '') DESC").fetchall()
        return [self._row_to_viewpoint(r) for r in rows]

    def update_viewpoint(self, vp: Viewpoint) -> None:
        self.add_viewpoint(vp)

    def delete_viewpoint(self, viewpoint_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM viewpoints WHERE viewpoint_id=?", (viewpoint_id,))

    # ---- commentaries ----
    def add_commentary(self, commentary_id: str, payload: dict) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO commentaries
                   (commentary_id, event_text, subject, cutoff_date, output_markdown,
                    source_category, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (commentary_id, payload.get("event_text"), payload.get("subject"),
                 payload.get("cutoff_date"), payload.get("output_markdown"),
                 payload.get("source_category", "AI生成内容"), payload.get("created_at")),
            )

    def list_commentaries(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM commentaries ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ---- stats ----
    def stats(self) -> dict:
        with self._conn() as c:
            n_docs = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_failed = c.execute(
                "SELECT COUNT(*) FROM documents WHERE parse_status IN (?,?)",
                ("解析失败", "需要OCR"),
            ).fetchone()[0]
            n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            n_viewpoints = c.execute("SELECT COUNT(*) FROM viewpoints").fetchone()[0]
            date_range = c.execute(
                "SELECT MIN(publish_date), MAX(publish_date) FROM documents WHERE publish_date IS NOT NULL"
            ).fetchone()
            n_unknown_date = c.execute(
                "SELECT COUNT(*) FROM documents WHERE publish_date IS NULL OR publish_date=''"
            ).fetchone()[0]
            latest = c.execute(
                "SELECT MAX(uploaded_at) FROM documents WHERE parse_status='可检索'"
            ).fetchone()[0]
        return {
            "documents": n_docs,
            "parse_failed": n_failed,
            "chunks": n_chunks,
            "viewpoints": n_viewpoints,
            "date_min": date_range[0] if date_range else None,
            "date_max": date_range[1] if date_range else None,
            "date_unknown": n_unknown_date,
            "latest_ready_at": latest,
        }


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
_cache: StorageAdapter | None = None


def get_store() -> StorageAdapter:
    """返回当前配置的存储后端（进程内单例）。"""
    global _cache
    if _cache is not None:
        return _cache

    backend = config.storage_backend()
    if backend == "local":
        _cache = LocalSqliteStorage(str(config.store_db_path()))
        return _cache
    raise StorageError(
        f"未支持的存储后端 {backend!r}。第一版仅实现 'local'（本地 SQLite）。"
        "云端部署请实现 StorageAdapter 的远程后端（对象存储 + 托管数据库），"
        "并在 config.storage_backend() 中返回对应后端名。"
    )


def reset_store() -> None:
    """测试辅助：清空单例缓存。"""
    global _cache
    _cache = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
