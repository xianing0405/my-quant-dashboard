#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Postgres 存储后端（Supabase）：文档/片段/观点/点评存 Postgres，原文件存 Storage。

与 LocalSqliteStorage 实现同一 StorageAdapter 接口；JSON 字段以字符串（json.dumps）
存 text 列，读取时 json.loads，与 SQLite 版行为一致。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import config
from .models import Document, Evidence, Viewpoint
from .storage import StorageAdapter, StorageError


def _conn():
    dsn = config.postgres_dsn()
    if not dsn:
        raise StorageError("未配置 POSTGRES_DSN")
    return psycopg.connect(dsn, row_factory=dict_row)


class PostgresStorage(StorageAdapter):
    def __init__(self):
        # 连接测试：构造时验证表存在（失败抛 StorageError，由上层捕获）
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1 from documents limit 1")

    # ---- documents ----
    def upsert_document(self, doc: Document) -> None:
        d = doc.to_dict()
        d["companies"] = json.dumps(d["companies"], ensure_ascii=False)
        d["industry_topics"] = json.dumps(d["industry_topics"], ensure_ascii=False)
        cols = list(d.keys())
        sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "document_id")
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"insert into documents ({', '.join(cols)}) values ({', '.join('%s' for _ in cols)}) "
                    f"on conflict (document_id) do update set {sets}",
                    [d[c] for c in cols],
                )

    def _row_to_document(self, row: dict) -> Document:
        d = dict(row)
        d["companies"] = json.loads(d.get("companies") or "[]")
        d["industry_topics"] = json.loads(d.get("industry_topics") or "[]")
        return Document.from_dict(d)

    def get_document(self, document_id: str) -> Document | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from documents where document_id=%s", (document_id,))
                row = cur.fetchone()
        return self._row_to_document(row) if row else None

    def list_documents(self) -> list[Document]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from documents order by coalesce(uploaded_at, created_at) desc")
                rows = cur.fetchall()
        return [self._row_to_document(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from documents where document_id=%s", (document_id,))

    def find_by_hash(self, file_hash: str) -> Document | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from documents where file_hash=%s limit 1", (file_hash,))
                row = cur.fetchone()
        return self._row_to_document(row) if row else None

    def max_version(self, title: str) -> int:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select coalesce(max(version),0) as v from documents where title=%s", (title,))
                return int(cur.fetchone()["v"])

    # ---- chunks ----
    def add_chunks(self, chunks: list[Evidence]) -> None:
        cols = ["evidence_id", "document_id", "seq", "text", "page_number", "position", "heading",
                "speaker", "ts", "role", "parent_evidence_id", "is_ai_generated"]
        rows = []
        for ch in chunks:
            d = ch.to_dict()
            d["is_ai_generated"] = 1 if d["is_ai_generated"] else 0
            rows.append([d[c] for c in cols])
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"insert into chunks ({', '.join(cols)}) values ({', '.join('%s' for _ in cols)}) "
                    "on conflict (evidence_id) do update set text=excluded.text, page_number=excluded.page_number, "
                    "position=excluded.position, heading=excluded.heading, speaker=excluded.speaker, ts=excluded.ts, "
                    "role=excluded.role, parent_evidence_id=excluded.parent_evidence_id",
                    rows,
                )

    def _row_to_evidence(self, row: dict) -> Evidence:
        d = dict(row)
        d["is_ai_generated"] = bool(d.get("is_ai_generated"))
        return Evidence(**{k: v for k, v in d.items() if k in Evidence.__dataclass_fields__})

    def list_chunks(self, document_id: str) -> list[Evidence]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from chunks where document_id=%s order by seq", (document_id,))
                rows = cur.fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def all_chunks(self) -> list[Evidence]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from chunks order by document_id, seq")
                rows = cur.fetchall()
        return [self._row_to_evidence(r) for r in rows]

    # ---- viewpoints ----
    def add_viewpoint(self, vp: Viewpoint) -> None:
        d = vp.to_dict()
        d["evidence_ids"] = json.dumps(d["evidence_ids"], ensure_ascii=False)
        d["relationships"] = json.dumps(d["relationships"], ensure_ascii=False)
        d["is_ai_generated"] = 1 if d["is_ai_generated"] else 0
        cols = list(d.keys())
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"insert into viewpoints ({', '.join(cols)}) values ({', '.join('%s' for _ in cols)}) "
                    "on conflict (viewpoint_id) do update set "
                    + ", ".join(f"{c}=excluded.{c}" for c in cols if c != "viewpoint_id"),
                    [d[c] for c in cols],
                )

    def _row_to_viewpoint(self, row: dict) -> Viewpoint:
        d = dict(row)
        d["evidence_ids"] = json.loads(d.get("evidence_ids") or "[]")
        d["relationships"] = json.loads(d.get("relationships") or "[]")
        d["is_ai_generated"] = bool(d.get("is_ai_generated"))
        return Viewpoint.from_dict(d)

    def get_viewpoint(self, viewpoint_id: str) -> Viewpoint | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from viewpoints where viewpoint_id=%s", (viewpoint_id,))
                row = cur.fetchone()
        return self._row_to_viewpoint(row) if row else None

    def list_viewpoints(self) -> list[Viewpoint]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from viewpoints order by coalesce(viewpoint_date, created_at, '') desc")
                rows = cur.fetchall()
        return [self._row_to_viewpoint(r) for r in rows]

    def update_viewpoint(self, vp: Viewpoint) -> None:
        self.add_viewpoint(vp)

    def delete_viewpoint(self, viewpoint_id: str) -> None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from viewpoints where viewpoint_id=%s", (viewpoint_id,))

    # ---- commentaries ----
    def add_commentary(self, commentary_id: str, payload: dict) -> None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into commentaries (commentary_id, event_text, subject, cutoff_date, output_markdown, source_category, created_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s) on conflict (commentary_id) do update set output_markdown=excluded.output_markdown",
                    (commentary_id, payload.get("event_text"), payload.get("subject"),
                     payload.get("cutoff_date"), payload.get("output_markdown"),
                     payload.get("source_category", "AI生成内容"), payload.get("created_at")),
                )

    def list_commentaries(self) -> list[dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from commentaries order by created_at desc")
                return [dict(r) for r in cur.fetchall()]

    # ---- stats ----
    def stats(self) -> dict:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) c from documents")
                n_docs = cur.fetchone()["c"]
                cur.execute("select count(*) c from documents where parse_status in ('解析失败','需要OCR')")
                n_failed = cur.fetchone()["c"]
                cur.execute("select count(*) c from chunks")
                n_chunks = cur.fetchone()["c"]
                cur.execute("select count(*) c from viewpoints")
                n_vp = cur.fetchone()["c"]
                cur.execute("select min(publish_date) a, max(publish_date) b from documents where publish_date is not null")
                r = cur.fetchone()
                cur.execute("select count(*) c from documents where publish_date is null or publish_date=''")
                n_unknown = cur.fetchone()["c"]
                cur.execute("select max(uploaded_at) m from documents where parse_status='可检索'")
                latest = cur.fetchone()["m"]
        return {
            "documents": n_docs, "parse_failed": n_failed, "chunks": n_chunks, "viewpoints": n_vp,
            "date_min": r["a"] if r else None, "date_max": r["b"] if r else None,
            "date_unknown": n_unknown, "latest_ready_at": latest,
        }
