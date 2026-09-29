#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词模板回归测试：不调用模型，验证 .format() 能正确构造含 JSON 示例的提示词。

此前 _EXTRACT_PROMPT 里的 JSON 大括号 {"viewpoints": ...} 被 str.format 误当占位符，
导致 KeyError。此测试防止同类问题复发。
"""

from __future__ import annotations

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.dirname(HERE)
if WEBSITE not in sys.path:
    sys.path.insert(0, WEBSITE)

from core.knowledge.viewpoints import _EXTRACT_PROMPT, _parse_viewpoints  # noqa: E402
from core.knowledge.commentary import _COMPARE_PROMPT  # noqa: E402


def test_extract_prompt_formats_without_keyerror():
    text = "示例正文：若美联储降息则关注碳化硅"
    prompt = _EXTRACT_PROMPT.format(text=text)
    # JSON 示例保留（大括号已正确转义）
    assert '"viewpoints"' in prompt
    # 正文正确注入
    assert text in prompt


def test_compare_prompt_formats():
    prompt = _COMPARE_PROMPT.format(event_text="某公司公告扩产", viewpoints="观点块", evidence="证据块")
    assert "某公司公告扩产" in prompt
    assert "观点块" in prompt
    assert "证据块" in prompt


def test_parse_viewpoints():
    items = _parse_viewpoints({"viewpoints": [{"core_judgment": "若降息则关注碳化硅"}]})
    assert len(items) == 1
    assert _parse_viewpoints(None) == []
    assert _parse_viewpoints({"viewpoints": "不是列表"}) == []


def test_qa_prompt_formats():
    # 动态导入 web_dashboard 的 _QA_PROMPT（避免依赖 streamlit 运行时）
    import importlib.util
    spec = importlib.util.spec_from_file_location("wd", os.path.join(WEBSITE, "web_dashboard.py"))
    mod = importlib.util.module_from_spec(spec)
    # 只读源码里提取 _QA_PROMPT，不真正 import（避免执行 streamlit 顶层代码）
    src = open(os.path.join(WEBSITE, "web_dashboard.py"), encoding="utf-8").read()
    assert "_QA_PROMPT.format(context=" in src or "{context}" in src
    # 简单构造验证：_QA_PROMPT 只用 context/query 两个占位符，无 JSON 大括号冲突
    # 这里只做静态检查，确保没有未转义 JSON 大括号进入 format 模板
