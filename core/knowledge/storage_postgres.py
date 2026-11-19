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
    return psycopg.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=10,
        options="-c statement_timeout=30000",
    )


def _no_nul(v: Any) -> Any:
    """Postgres text 不接受 NUL(0x00) 字节；写库前剥离（PDF 提取可能带入）。"""
    if isinstance(v, str):
        return v.replace("\x00", "")
    return v


def _jsonb(v: Any, default: Any) -> Any:
    """JSONB 读取：psycopg 3 返回已解析的 list/dict；兼容 TEXT 后端返回的 JSON 字符串。"""
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return default


class PostgresStorage(StorageAdapter):
    def __init__(self):
        # 连接测试：构造时验证表存在（失败抛 StorageError，由上层捕获）
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1 from documents limit 1")

    def ensure_research_tables(self) -> None:
        """幂等创建研报方法三张表（与 sql/schema.sql 一致；增量、不影响既有表）。"""
        ddl = """
        CREATE TABLE IF NOT EXISTS research_methods (
            method_id TEXT PRIMARY KEY,
            method_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            institution TEXT,
            source_url TEXT,
            report_title TEXT,
            evidence JSONB,
            params JSONB,
            unconfirmed JSONB,
            method_card_md TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS research_indicators (
            method_id TEXT NOT NULL,
            date TEXT NOT NULL,
            scheme TEXT NOT NULL,
            X DOUBLE PRECISION,
            Y DOUBLE PRECISION,
            A_raw DOUBLE PRECISION,
            B_raw DOUBLE PRECISION,
            quadrant TEXT,
            history JSONB,
            data_source TEXT,
            computed_at TEXT,
            PRIMARY KEY (method_id, scheme, date)
        );
        CREATE TABLE IF NOT EXISTS research_results (
            result_id TEXT PRIMARY KEY,
            method_id TEXT NOT NULL,
            method_version TEXT,
            input_date TEXT,
            mode TEXT,
            evidence_ids JSONB,
            evidence_snapshot JSONB,
            input_snapshot JSONB,
            review_note TEXT,
            output_markdown TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rmethod_results ON research_results(method_id, input_date);
        ALTER TABLE research_results ADD COLUMN IF NOT EXISTS evidence_snapshot JSONB;
        ALTER TABLE research_results ADD COLUMN IF NOT EXISTS input_snapshot JSONB;
        ALTER TABLE research_results ADD COLUMN IF NOT EXISTS review_note TEXT;
        """
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
        # 幂等迁移：research_indicators 唯一键 (method_id, date) -> (method_id, scheme, date)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = 'research_indicators'::regclass AND i.indisprimary
                    ORDER BY a.attnum
                """)
                pk_cols = [r["attname"] for r in cur.fetchall()]
                if pk_cols != ["method_id", "scheme", "date"]:
                    cur.execute("ALTER TABLE research_indicators DROP CONSTRAINT IF EXISTS research_indicators_pkey")
                    cur.execute("ALTER TABLE research_indicators ADD PRIMARY KEY (method_id, scheme, date)")

    # ---- documents ----
    def upsert_document(self, doc: Document) -> None:
        d = {k: _no_nul(v) for k, v in doc.to_dict().items()}
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
            d = {k: _no_nul(v) for k, v in ch.to_dict().items()}
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

    def get_chunk(self, evidence_id: str) -> Evidence | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from chunks where evidence_id=%s", (evidence_id,))
                row = cur.fetchone()
        return self._row_to_evidence(row) if row else None

    # ---- viewpoints ----
    def add_viewpoint(self, vp: Viewpoint) -> None:
        d = {k: _no_nul(v) for k, v in vp.to_dict().items()}
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
                    (_no_nul(commentary_id), _no_nul(payload.get("event_text")), _no_nul(payload.get("subject")),
                     _no_nul(payload.get("cutoff_date")), _no_nul(payload.get("output_markdown")),
                     _no_nul(payload.get("source_category", "AI生成内容")), _no_nul(payload.get("created_at"))),
                )

    def list_commentaries(self) -> list[dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from commentaries order by created_at desc")
                return [dict(r) for r in cur.fetchall()]

    # ---- research methods（研报方法：方法卡/证据/指标/结果；幂等，与 SQLite 行为一致）----
    def upsert_method(self, payload: dict) -> None:
        d = {k: _no_nul(v) for k, v in payload.items()}
        d["evidence"] = json.dumps(d.get("evidence", []), ensure_ascii=False)
        d["params"] = json.dumps(d.get("params", {}), ensure_ascii=False)
        d["unconfirmed"] = json.dumps(d.get("unconfirmed", []), ensure_ascii=False)
        cols = ["method_id", "method_key", "name", "version", "institution", "source_url",
                "report_title", "evidence", "params", "unconfirmed", "method_card_md", "created_at", "updated_at"]
        sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "method_id")
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"insert into research_methods ({', '.join(cols)}) values ({', '.join('%s' for _ in cols)}) "
                    f"on conflict (method_id) do update set {sets}",
                    [d.get(c) for c in cols],
                )

    def _row_to_method(self, row: dict) -> dict:
        d = dict(row)
        d["evidence"] = _jsonb(d.get("evidence"), [])
        d["params"] = _jsonb(d.get("params"), {})
        d["unconfirmed"] = _jsonb(d.get("unconfirmed"), [])
        return d

    def get_method_by_key(self, method_key: str) -> dict | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from research_methods where method_key=%s", (method_key,))
                row = cur.fetchone()
        return self._row_to_method(row) if row else None

    def get_method(self, method_id: str) -> dict | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from research_methods where method_id=%s", (method_id,))
                row = cur.fetchone()
        return self._row_to_method(row) if row else None

    def list_methods(self) -> list[dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from research_methods order by name, version")
                return [self._row_to_method(r) for r in cur.fetchall()]

    def upsert_indicator(self, method_id: str, date: str, payload: dict) -> None:
        d = {k: _no_nul(v) for k, v in payload.items()}
        d["history"] = json.dumps(d.get("history", []), ensure_ascii=False)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into research_indicators (method_id, date, scheme, X, Y, A_raw, B_raw, quadrant, history, data_source, computed_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "on conflict (method_id, scheme, date) do update set X=excluded.X, Y=excluded.Y, "
                    "A_raw=excluded.A_raw, B_raw=excluded.B_raw, quadrant=excluded.quadrant, history=excluded.history, "
                    "data_source=excluded.data_source, computed_at=excluded.computed_at",
                    [method_id, date, d.get("scheme"), d.get("X"), d.get("Y"), d.get("A_raw"), d.get("B_raw"),
                     d.get("quadrant"), d["history"], d.get("data_source"), d.get("computed_at")],
                )

    def _row_to_indicator(self, row: dict) -> dict:
        d = dict(row)
        d["history"] = _jsonb(d.get("history"), [])
        # Postgres 未加引号标识符折叠为小写，映射回与 SQLite 一致的大写键
        for lower, upper in (("x", "X"), ("y", "Y"), ("a_raw", "A_raw"), ("b_raw", "B_raw")):
            if upper not in d and lower in d:
                d[upper] = d[lower]
        return d

    def list_indicators(self, method_id: str, scheme: str | None = None) -> list[dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                if scheme:
                    cur.execute("select * from research_indicators where method_id=%s and scheme=%s order by date", (method_id, scheme))
                else:
                    cur.execute("select * from research_indicators where method_id=%s order by scheme, date", (method_id,))
                return [self._row_to_indicator(r) for r in cur.fetchall()]

    def latest_indicator_date(self, method_id: str) -> str | None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select max(date) d from research_indicators where method_id=%s", (method_id,))
                row = cur.fetchone()
        return row["d"] if row and row["d"] else None

    def save_result(self, result_id: str, payload: dict) -> None:
        d = {k: _no_nul(v) for k, v in payload.items()}
        d["evidence_ids"] = json.dumps(d.get("evidence_ids", []), ensure_ascii=False)
        d["evidence_snapshot"] = json.dumps(d.get("evidence_snapshot", []), ensure_ascii=False)
        d["input_snapshot"] = json.dumps(d.get("input_snapshot", {}), ensure_ascii=False)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into research_results (result_id, method_id, method_version, input_date, mode, evidence_ids, evidence_snapshot, input_snapshot, output_markdown, model, input_tokens, output_tokens, created_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (result_id) do update set output_markdown=excluded.output_markdown",
                    [result_id, d.get("method_id"), d.get("method_version"), d.get("input_date"), d.get("mode"),
                     d["evidence_ids"], d["evidence_snapshot"], d["input_snapshot"], d.get("output_markdown"), d.get("model"),
                     d.get("input_tokens"), d.get("output_tokens"), d.get("created_at")],
                )

    def list_results(self, method_id: str) -> list[dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select * from research_results where method_id=%s order by created_at desc", (method_id,))
                out = []
                for r in cur.fetchall():
                    dd = dict(r)
                    dd["evidence_ids"] = _jsonb(dd.get("evidence_ids"), [])
                    dd["evidence_snapshot"] = _jsonb(dd.get("evidence_snapshot"), [])
                    dd["input_snapshot"] = _jsonb(dd.get("input_snapshot"), {})
                    out.append(dd)
                return out

    def set_review_note(self, result_id: str, note: str) -> None:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("update research_results set review_note=%s where result_id=%s", (_no_nul(note), result_id))

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
