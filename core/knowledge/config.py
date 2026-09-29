#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库统一配置。

所有路径与运行时开关集中在此，从环境变量（优先）或 Streamlit secrets 读取，
不硬编码任何密钥。服务层与页面层共用本模块，保证权限与检索模式口径一致。
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 环境变量读取（优先环境变量，其次 Streamlit secrets）
# ---------------------------------------------------------------------------
def _secrets() -> dict:
    """尽力读取 Streamlit secrets（非 Streamlit 环境下返回空字典）。"""
    try:
        import streamlit as st  # noqa: PLC0415
        return dict(st.secrets or {})
    except Exception:  # noqa: BLE001
        return {}


def get(name: str, default: str | None = None) -> str | None:
    """读取配置项：环境变量 > Streamlit secrets > 默认值。"""
    val = os.environ.get(name)
    if val is None or val == "":
        val = _secrets().get(name)
    if val is None or val == "":
        return default
    return str(val)


def get_bool(name: str, default: bool = False) -> bool:
    val = get(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 路径配置（可通过环境变量覆盖，方便部署到持久化存储）
# ---------------------------------------------------------------------------
def materials_dir() -> Path:
    """正式入库资料目录：存放原始内部材料（只保留原文件，不存 AI 摘要）。"""
    custom = get("KNOWLEDGE_MATERIALS_DIR")
    if custom:
        return Path(custom).expanduser()
    return HERE / "raw_docs"


def store_dir() -> Path:
    """元数据 / 片段 / 观点 / 点评的持久化目录（含 SQLite 库）。"""
    custom = get("KNOWLEDGE_STORE_DIR")
    if custom:
        return Path(custom).expanduser()
    return HERE / "store"


def store_db_path() -> Path:
    return store_dir() / "knowledge.db"


# ---------------------------------------------------------------------------
# 检索模式
# ---------------------------------------------------------------------------
def embedding_configured() -> bool:
    """是否配置了 embedding 服务（用于语义检索）。三项缺一不可。"""
    return bool(
        get("EMBEDDING_API_KEY")
        and get("EMBEDDING_BASE_URL")
        and get("EMBEDDING_MODEL")
    )


def retrieval_mode() -> str:
    """当前检索模式标签。"""
    return "关键词+语义融合" if embedding_configured() else "关键词检索"


# ---------------------------------------------------------------------------
# 权限配置
# ---------------------------------------------------------------------------
# disabled（默认）：知识库不对匿名用户开放。
# public_readonly（公网推荐）：访客可匿名查询「已标记公开」的资料，写操作仅管理员。
# password：需输入 KNOWLEDGE_ACCESS_PASSWORD 才能查看；写操作仍仅管理员。
# open：本地开发用，匿名即可查看全部（写操作仍仅管理员，不建议公网使用）。
def access_mode() -> str:
    mode = (get("KNOWLEDGE_ACCESS_MODE") or "disabled").strip().lower()
    return mode if mode in ("open", "password", "public_readonly", "disabled") else "disabled"


def public_readonly_enabled() -> bool:
    """是否启用「匿名只读查询公开资料」模式。"""
    return access_mode() == "public_readonly"


def admin_password() -> str | None:
    """管理口令（与数据更新共用 ADMIN_PASSWORD），写操作与维护需以此登录。"""
    return get("ADMIN_PASSWORD")


def access_password() -> str | None:
    return get("KNOWLEDGE_ACCESS_PASSWORD")


def allowed_users() -> set[str]:
    raw = get("KNOWLEDGE_ALLOWED_USERS") or ""
    return {u.strip() for u in raw.split(",") if u.strip()}


# ---------------------------------------------------------------------------
# 存储后端（持久化适配）
# ---------------------------------------------------------------------------
# 第一版仅实现 "local"（本地 SQLite，开发/单机模式）。云上需换成持久化存储
# （对象存储 / 托管数据库）时，在此返回对应后端名，由 storage 层适配。
def storage_backend() -> str:
    return (get("KNOWLEDGE_STORAGE_BACKEND") or "local").strip().lower()


# 供页面顶部展示使用
def summary() -> dict:
    return {
        "materials_dir": str(materials_dir()),
        "store_dir": str(store_dir()),
        "retrieval_mode": retrieval_mode(),
        "embedding_configured": embedding_configured(),
        "access_mode": access_mode(),
        "storage_backend": storage_backend(),
    }
