#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量导入（递归文件夹 + ZIP）的服务层测试。"""

from __future__ import annotations

import io
import os
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.dirname(HERE)
if WEBSITE not in sys.path:
    sys.path.insert(0, WEBSITE)

from core import knowledge as kb  # noqa: E402
from core.knowledge import storage  # noqa: E402
from core.knowledge.auth import KnowledgeAuthError  # noqa: E402

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


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_recursive_scan_with_category(env):
    root = env / "materials" / "主题"
    _write(root / "05-科技" / "报告A.txt", "碳化硅 需求上行")
    _write(root / "报告B.txt", "光纤 景气")
    r = kb.scan_folder_recursive(str(root), user=ADMIN)
    assert r["scanned"] == 2 and r["ingested"] == 2
    docs = {d["title"]: d for d in kb.list_documents(ADMIN)}
    assert "05-科技" in docs["报告A"]["industry_topics"]
    # 重复执行不重复入库
    r2 = kb.scan_folder_recursive(str(root), user=ADMIN)
    assert r2["duplicate"] == 2 and r2["ingested"] == 0


def test_zip_import(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("07-消费/研报C.txt", "消费 复苏")
    r = kb.import_zip_bytes(buf.getvalue(), user=ADMIN)
    assert r.get("ok") and r["ingested"] == 1


def test_zip_blocks_traversal(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.pdf", "x")
    r = kb.import_zip_bytes(buf.getvalue(), user=ADMIN)
    assert not r.get("ok") and "非法路径" in r.get("error", "")


def test_zip_requires_admin(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "x")
    with pytest.raises(KnowledgeAuthError):
        kb.import_zip_bytes(buf.getvalue(), user="anonymous")
