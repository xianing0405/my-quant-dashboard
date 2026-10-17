#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 SQLite 知识库同步到 Supabase Postgres（文档/片段/观点/点评）。

用法（在 Website/ 目录下）：
  python scripts/sync_to_supabase.py --check        # 只核对两端数量，不写入
  python scripts/sync_to_supabase.py --limit 5      # 先同步 5 份验证检索/引用/权限
  python scripts/sync_to_supabase.py                # 同步全部（幂等，按 file_hash 去重，不覆盖已有记录）

配置从 scripts/.env.local 读取（该文件被 .gitignore 忽略，不入库）：
  POSTGRES_DSN=postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres

不读取/打印密钥；密钥只在脚本进程内用于连接 Postgres。
原始 PDF 二进制不在此脚本范围内（路线 A 不上传 Storage）。
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


def _counts(store) -> dict:
    try:
        st = store.stats()
        return {"documents": st.get("documents"), "chunks": st.get("chunks")}
    except Exception:  # noqa: BLE001
        return {"documents": None, "chunks": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只同步前 N 份文档（0=全部）")
    ap.add_argument("--check", action="store_true", help="只核对两端数量，不写入")
    args = ap.parse_args()

    for k, v in _load_env().items():
        if v and k not in os.environ:
            os.environ[k] = v

    from core.knowledge.storage import LocalSqliteStorage
    from core.knowledge.storage_postgres import PostgresStorage

    local = LocalSqliteStorage(
        os.environ.get("LOCAL_DB")
        or str(WEBSITE / "core" / "knowledge" / "store" / "knowledge.db")
    )

    try:
        remote = PostgresStorage()
    except Exception as e:  # noqa: BLE001
        print(f"无法连接云端 Postgres（或表未建）：{e}")
        print("请先在 Supabase → SQL Editor 运行 sql/schema.sql，")
        print("并在 scripts/.env.local 填好 POSTGRES_DSN 后重试。")
        sys.exit(1)

    local_counts = _counts(local)
    remote_before = _counts(remote)
    print(f"[本地 SQLite]   文档 {local_counts['documents']} / 片段 {local_counts['chunks']}")
    print(f"[云端 Postgres] 迁移前：文档 {remote_before['documents']} / 片段 {remote_before['chunks']}")

    if args.check:
        print("--check：仅核对两端数量，未写入。")
        return

    docs = local.list_documents()
    if args.limit:
        docs = docs[: args.limit]
    remote_hashes = {d.file_hash for d in remote.list_documents()}

    synced = skipped = 0
    errors: list[tuple[str, str]] = []
    for doc in docs:
        if doc.file_hash in remote_hashes:
            skipped += 1
            continue
        try:
            remote.upsert_document(doc)
            remote.add_chunks(local.list_chunks(doc.document_id))
            synced += 1
        except Exception as e:  # noqa: BLE001
            errors.append((doc.title, str(e)))

    for vp in local.list_viewpoints():
        remote.add_viewpoint(vp)
    for cm in local.list_commentaries():
        remote.add_commentary(cm["commentary_id"], cm)

    remote_after = _counts(remote)
    print(f"[云端 Postgres] 迁移后：文档 {remote_after['documents']} / 片段 {remote_after['chunks']}")
    print(f"迁移结果：新增文档 {synced} / 去重跳过 {skipped} / 错误 {len(errors)}")
    for title, err in errors[:20]:
        print(f"  - 失败《{title[:60]}》：{err}")
    if len(errors) > 20:
        print(f"  … 其余 {len(errors) - 20} 条错误未列出")
    print("观点与点评已幂等写入。")
    print("提示：原始 PDF 未上传 Storage（路线 A 不涉及）；依赖原文件的重解析功能在云端当前不可用。")


if __name__ == "__main__":
    main()
