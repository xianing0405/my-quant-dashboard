#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 SQLite 知识库同步到 Supabase Postgres（文档/片段/观点/点评）。

用法（在 Website/ 目录下）：
  python scripts/sync_to_supabase.py --limit 5     # 先同步 5 份验证检索/引用/权限
  python scripts/sync_to_supabase.py               # 同步全部（幂等，按 file_hash 去重）

配置从 scripts/.env.local 读取（该文件被 .gitignore 忽略，不入库）：
  POSTGRES_DSN=postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres

不读取/打印密钥；密钥只在脚本进程内用于连接 Postgres。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBSITE = HERE.parent
sys.path.insert(0, str(WEBSITE))


def _load_env() -> dict:
    env: dict = {}
    p = HERE / ".env.local"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只同步前 N 份文档（0=全部）")
    args = ap.parse_args()

    for k, v in _load_env().items():
        if v and k not in os.environ:
            os.environ[k] = v

    from core.knowledge.storage import LocalSqliteStorage
    from core.knowledge.storage_postgres import PostgresStorage

    local = LocalSqliteStorage(os.environ.get("LOCAL_DB") or str(WEBSITE / "core" / "knowledge" / "store" / "knowledge.db"))
    remote = PostgresStorage()

    docs = local.list_documents()
    if args.limit:
        docs = docs[: args.limit]
    remote_hashes = {d.file_hash for d in remote.list_documents()}

    synced = skipped = 0
    for doc in docs:
        if doc.file_hash in remote_hashes:
            skipped += 1
            continue
        remote.upsert_document(doc)
        remote.add_chunks(local.list_chunks(doc.document_id))
        synced += 1

    for vp in local.list_viewpoints():
        remote.add_viewpoint(vp)
    for cm in local.list_commentaries():
        remote.add_commentary(cm["commentary_id"], cm)

    print(f"同步完成：文档 {synced} 新增 / {skipped} 去重跳过；观点与点评已幂等写入。")
    print("原始 PDF 文件上传到 Storage 为独立步骤（本轮先验证检索/引用/权限）。")


if __name__ == "__main__":
    main()
