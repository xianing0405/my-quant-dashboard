#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索层无模型回归：中文→英文术语映射 + document_id 严格限定。"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.dirname(HERE)
if WEBSITE not in sys.path:
    sys.path.insert(0, WEBSITE)

from core import knowledge as kb  # noqa: E402
from core.knowledge import storage  # noqa: E402
from core.knowledge.retriever import _expand_query  # noqa: E402

ADMIN = "admin"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ACCESS_MODE", "public_readonly")
    monkeypatch.setenv("ADMIN_PASSWORD", "t")
    monkeypatch.setenv("KNOWLEDGE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("KNOWLEDGE_MATERIALS_DIR", str(tmp_path / "materials"))
    os.makedirs(tmp_path / "materials", exist_ok=True)
    storage.reset_store()
    yield tmp_path
    storage.reset_store()


def _ingest(env, name, text):
    f = env / "materials" / name
    f.write_text(text, encoding="utf-8")
    return kb.ingest_file(str(f), user=ADMIN, source_category="外部研报")


def test_term_map_expands_chinese():
    q = _expand_query("ISM就业和价格")
    assert "employment" in q and "prices" in q


def test_document_id_filter_strict(env):
    a = _ingest(env, "UBS-A.txt", "employment prices ISM rose")
    _ingest(env, "README.md", "这是系统说明，不是研报")
    res = kb.search_materials("employment prices", ADMIN, top_k=5,
                              filters={"document_ids": [a["document_id"]]})
    assert res["hits"] and all(h["document_id"] == a["document_id"] for h in res["hits"])
