#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库服务端权限校验。

权限在服务层执行，而非仅靠前端隐藏菜单。所有内部资料功能（列表 / 搜索 /
原文 / 下载 / 观点 / 点评）调用前必须先通过本模块的 `require_authorized`。

未配置认证（默认 disabled）时，匿名用户一律拒绝访问内部资料。
"""

from __future__ import annotations

import hashlib
import hmac

from . import config


class KnowledgeAuthError(PermissionError):
    """内部资料访问被拒绝。"""


def _default_user() -> str:
    return "anonymous"


def is_authorized(user: str | None) -> bool:
    """判断指定用户是否有权访问内部知识库。"""
    mode = config.access_mode()

    if mode == "open":
        return True
    if mode == "disabled":
        return False

    # password 模式：仅白名单用户或有正确密码的会话可通过
    user = (user or "").strip()
    if user and user in config.allowed_users():
        return True
    if user and user.startswith("authed:"):
        return _verify_session_token(user)
    return False


def verify_password(password: str) -> bool:
    """校验访问密码（constant-time 比较）。"""
    expected = config.access_password()
    if not expected:
        return False
    return hmac.compare_digest(str(password), expected)


def issue_session_token() -> str:
    """密码通过后签发会话令牌（不落盘，仅本进程有效）。"""
    password = config.access_password() or ""
    digest = hashlib.sha256(f"{password}".encode("utf-8")).hexdigest()[:16]
    return f"authed:{digest}"


def _verify_session_token(user: str) -> bool:
    expected = issue_session_token()
    return hmac.compare_digest(user, expected)


def require_authorized(user: str | None, action: str = "访问内部知识库") -> None:
    """服务层权限门：未授权抛 KnowledgeAuthError。"""
    if not is_authorized(user):
        raise KnowledgeAuthError(f"未授权：无权{action}（未配置认证或身份无效）")


# ---------------------------------------------------------------------------
# 匿名只读查询 + 管理员维护（public_readonly 模式）
# ---------------------------------------------------------------------------
ADMIN_USER = "admin"


def is_admin(user: str | None) -> bool:
    """是否管理员（页面层用 ADMIN_PASSWORD 登录后以 'admin' 身份传入）。"""
    return (user or "").strip() == ADMIN_USER


def read_scope(user: str | None) -> str | None:
    """返回读取范围：'all'（管理员/已登录）、'public'（匿名只读公开资料）、None（拒绝）。"""
    if is_admin(user):
        return "all"
    if is_authorized(user):
        return "all"
    if config.public_readonly_enabled():
        return "public"
    return None


def require_read(user: str | None, action: str = "查询知识库") -> str:
    """读取权限门：返回读取范围（'all' 或 'public'），否则抛 KnowledgeAuthError。"""
    scope = read_scope(user)
    if scope is None:
        raise KnowledgeAuthError(f"未授权：无权{action}（知识库未开放或身份无效）")
    return scope


def require_admin(user: str | None, action: str = "维护知识库资料") -> None:
    """写操作/维护权限门：仅管理员可执行，否则抛 KnowledgeAuthError。"""
    if not is_admin(user):
        raise KnowledgeAuthError(f"未授权：{action}仅管理员可执行")
