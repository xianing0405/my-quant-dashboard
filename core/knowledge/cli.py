#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库命令行入口（本地运维用）。

本地运行需授权访问（与页面同一套服务层权限）：
    KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli scan
    KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli ingest <文件> [选项]
    KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli search <查询> [--top-k 5]
    KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli stats
"""

from __future__ import annotations

import argparse
import sys

from . import config, stats as kb_stats
from .auth import is_authorized
from .indexer import ingest_file, scan_materials_dir
from .retriever import search_materials

USER = "local-user"


def _check() -> bool:
    if not is_authorized(USER):
        print("未授权：请设置 KNOWLEDGE_ACCESS_MODE=open（本地）或配置 password 模式后重试。")
        return False
    return True


def _cmd_scan() -> int:
    if not _check():
        return 1
    report = scan_materials_dir()
    print(f"扫描 {report['scanned']} 个文件，新入库 {report['ingested']} 个")
    for r in report.get("results", []):
        print(f"  [{r.get('status')}] {r['file']} — {r.get('message')}")
    for s in report.get("skipped", []):
        print(f"  跳过：{s}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    if not _check():
        return 1
    res = ingest_file(
        args.file,
        source_category=args.source_category,
        publish_date=args.publish_date,
        author=args.author,
        organization=args.organization,
    )
    print(f"[{res.get('status')}] {args.file} — {res.get('message')}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if not _check():
        return 1
    res = search_materials(args.query, USER, top_k=args.top_k)
    print(f"检索模式：{res['mode']}，命中 {len(res['hits'])} 条")
    for i, h in enumerate(res["hits"], 1):
        loc = f"p.{h['page_number']}" if h.get("page_number") else (h.get("position") or "—")
        print(f"[{i}] 《{h['title']}》v{h.get('document_version')} {h.get('publish_date') or '日期未知'} {loc}")
        print(f"    {h['text'][:120]}")
    return 0


def _cmd_stats() -> int:
    if not _check():
        return 1
    s = kb_stats(USER)
    for k in ("documents", "parse_failed", "chunks", "viewpoints",
              "date_min", "date_max", "date_unknown", "latest_ready_at", "retrieval_mode"):
        print(f"{k}: {s.get(k)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core.knowledge.cli", description="内部知识库运维")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="扫描资料目录并入库")
    sub.add_parser("stats", help="查看统计")

    p_ingest = sub.add_parser("ingest", help="入库单个文件")
    p_ingest.add_argument("file")
    p_ingest.add_argument("--source-category", default="其他")
    p_ingest.add_argument("--publish-date", default=None)
    p_ingest.add_argument("--author", default=None)
    p_ingest.add_argument("--organization", default=None)

    p_search = sub.add_parser("search", help="检索证据")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args(argv)
    if args.cmd == "scan":
        return _cmd_scan()
    if args.cmd == "stats":
        return _cmd_stats()
    if args.cmd == "ingest":
        return _cmd_ingest(args)
    if args.cmd == "search":
        return _cmd_search(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
