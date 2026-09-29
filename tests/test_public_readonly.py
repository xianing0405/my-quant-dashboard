#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""匿名只读查询 + 管理员维护（public_readonly 模式）的服务层权限测试。

验收点：
- 访客只能检索/读取「已标记公开」的资料；不能读取内部资料。
- 访客不能上传/删除/设置公开范围（写操作）。
- 管理员可维护；取消公开后立即从匿名检索范围移除。
- 新入库材料默认不进入匿名检索范围。
"""

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
from core.knowledge.auth import KnowledgeAuthError  # noqa: E402

ADMIN = "admin"
ANON = "anonymous"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ACCESS_MODE", "public_readonly")
    monkeypatch.setenv("ADMIN_PASSWORD", "testadmin")
    monkeypatch.setenv("KNOWLEDGE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("KNOWLEDGE_MATERIALS_DIR", str(tmp_path / "materials"))
    os.makedirs(tmp_path / "materials", exist_ok=True)
    storage.reset_store()
    yield tmp_path
    storage.reset_store()


def _ingest(env, name, text, public):
    f = env / "materials" / name
    f.write_text(text, encoding="utf-8")
    return kb.ingest_file(
        str(f), user=ADMIN, source_category="内部观点", publish_date="2026-09-10",
        permission_scope="public" if public else "internal",
    )


def test_anonymous_sees_public_not_internal(env):
    _ingest(env, "公开.txt", "碳化硅 需求上行", public=True)
    _ingest(env, "内部.txt", "内部机密 未公开观点", public=False)

    res = kb.search_materials("碳化硅", ANON, top_k=10)
    titles = {h["title"] for h in res["hits"]}
    assert "公开" in titles
    assert "内部" not in titles

    pub_hit = next(h for h in res["hits"] if "碳化硅" in h["text"])
    assert kb.read_evidence(pub_hit["evidence_id"], ANON) is not None

    priv_res = kb.search_materials("内部机密", ADMIN, top_k=5)
    priv_hit = priv_res["hits"][0]
    assert kb.read_evidence(priv_hit["evidence_id"], ANON) is None


def test_anonymous_cannot_write(env):
    f = env / "materials" / "x.txt"
    f.write_text("内容", encoding="utf-8")
    with pytest.raises(KnowledgeAuthError):
        kb.ingest_file(str(f), user=ANON)
    with pytest.raises(KnowledgeAuthError):
        kb.scan_materials_dir(user=ANON)
    with pytest.raises(KnowledgeAuthError):
        kb.delete_document("doc_x", user=ANON)
    with pytest.raises(KnowledgeAuthError):
        kb.set_document_public("doc_x", True, user=ANON)


def test_unpublish_removes_from_anonymous(env):
    pub = _ingest(env, "公开.txt", "光纤 需求", public=True)
    assert kb.search_materials("光纤", ANON, top_k=5)["hits"]
    kb.set_document_public(pub["document_id"], False, user=ADMIN)
    assert kb.search_materials("光纤", ANON, top_k=5)["hits"] == []


def test_admin_full_access_and_public_listing(env):
    _ingest(env, "公开.txt", "公开内容", public=True)
    _ingest(env, "内部.txt", "内部内容", public=False)
    assert len(kb.list_documents(ADMIN)) == 2
    anon_docs = kb.list_documents(ANON)
    assert len(anon_docs) == 1 and anon_docs[0]["permission_scope"] == "public"


def test_new_material_defaults_private(env):
    f = env / "materials" / "new.txt"
    f.write_text("新材料 默认不公开", encoding="utf-8")
    r = kb.ingest_file(str(f), user=ADMIN, source_category="内部观点")
    assert r["status"] == "可检索"
    assert kb.search_materials("新材料", ANON, top_k=5)["hits"] == []
