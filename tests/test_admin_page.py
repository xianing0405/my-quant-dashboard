#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理员已登录状态的知识库页面回归测试。

覆盖统计正常与统计异常两种情况，确认四个功能标签（资料管理/知识问答/历史观点/事件点评）
始终能渲染，模型用量统计失败不阻断页面。
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.dirname(HERE)
if WEBSITE not in sys.path:
    sys.path.insert(0, WEBSITE)

from streamlit.testing.v1 import AppTest  # noqa: E402
import core.knowledge.llm as kb_llm  # noqa: E402

KB = "📚 研究知识库"
TAB_NAMES = ["资料管理", "知识问答", "历史观点", "事件点评"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ACCESS_MODE", "public_readonly")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.setenv("KNOWLEDGE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("KNOWLEDGE_MATERIALS_DIR", str(tmp_path / "materials"))
    os.makedirs(tmp_path / "materials", exist_ok=True)
    from core.knowledge import storage
    storage.reset_store()
    yield
    storage.reset_store()


def _run_admin():
    at = AppTest.from_file("web_dashboard.py", default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value(KB).run()
    # 管理员已登录：直接置 session_state
    at.session_state["admin_authed"] = True
    at.run()
    return at


def _tab_labels(at):
    return [getattr(t, "label", "") for t in at.tabs]


def test_admin_four_tabs_normal(env, monkeypatch):
    monkeypatch.setattr(kb_llm, "usage_stats", lambda: {
        "calls": 3, "total_input_tokens": 100, "total_output_tokens": 50,
        "model": "m", "base_url": "u", "last_calls": [],
    })
    at = _run_admin()
    labels = _tab_labels(at)
    for name in TAB_NAMES:
        assert any(name in l for l in labels), f"缺少标签 {name}"


def test_admin_four_tabs_when_usage_missing(env, monkeypatch):
    monkeypatch.delattr(kb_llm, "usage_stats", raising=False)
    at = _run_admin()
    labels = _tab_labels(at)
    for name in TAB_NAMES:
        assert any(name in l for l in labels), f"缺少标签 {name}"
    assert any("用量统计暂不可用" in c.value for c in at.caption)


def test_admin_four_tabs_when_usage_raises(env, monkeypatch):
    def _boom():
        raise AttributeError("module has no attribute usage_stats")

    monkeypatch.setattr(kb_llm, "usage_stats", _boom)
    at = _run_admin()
    labels = _tab_labels(at)
    for name in TAB_NAMES:
        assert any(name in l for l in labels), f"缺少标签 {name}"
    assert any("用量统计暂不可用" in c.value for c in at.caption)
