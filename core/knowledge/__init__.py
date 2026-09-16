"""内部知识库（RAG 检索端）。

提供团队内部历史研报、会议纪要等材料的本地检索，供市场点评与策略问答时引用。
"""

from .local_rag import search_internal_knowledge  # noqa: F401

__all__ = ["search_internal_knowledge"]
