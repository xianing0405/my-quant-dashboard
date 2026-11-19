#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把研报方法（方法卡 / 证据 / 指标 / 生成结果）迁移到目标后端。

与 sync_to_supabase.py 使用同一配置加载机制（scripts/.env.local），默认目标为 Postgres。

用法（在 Website/ 目录下）：
  python scripts/migrate_research_method.py --check             # 只核对两端，不写入
  python scripts/migrate_research_method.py --with-results      # 同时迁移本地已保存的生成结果
  python scripts/migrate_research_method.py --local             # 目标改为本地 SQLite

配置（scripts/.env.local，被 .gitignore 忽略，不入库）：
  POSTGRES_DSN=postgresql://...
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
    st = store.stats()
    return {
        "documents": st.get("documents"), "chunks": st.get("chunks"),
        "viewpoints": st.get("viewpoints"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只核对，不写入")
    ap.add_argument("--with-results", action="store_true", help="同时迁移本地已保存的生成结果")
    ap.add_argument("--local", action="store_true", help="目标改为本地 SQLite")
    args = ap.parse_args()

    for k, v in _load_env().items():
        if v and k not in os.environ:
            os.environ[k] = v

    from core.knowledge import research_method as rm
    from core.knowledge.storage import LocalSqliteStorage
    from core.knowledge.storage_postgres import PostgresStorage

    local = LocalSqliteStorage(str(WEBSITE / "core" / "knowledge" / "store" / "knowledge.db"))

    if args.local:
        store = local
        label = "本地 SQLite"
    else:
        if not os.environ.get("POSTGRES_DSN"):
            print("未找到 POSTGRES_DSN。唯一需要填写的文件：Website/scripts/.env.local，变量：POSTGRES_DSN")
            sys.exit(1)
        try:
            store = PostgresStorage()
            store.ensure_research_tables()
        except Exception as e:  # noqa: BLE001
            print(f"无法连接 Postgres 或建表失败：{type(e).__name__}")
            print("请先在 scripts/.env.local 填好 POSTGRES_DSN；research_* 表由本脚本幂等创建。")
            sys.exit(1)
        label = "云端 Postgres"

    # 既有资料数量（迁移前后对比，确认不受影响）
    before = _counts(store)
    print(f"[{label}] 迁移前：文档 {before['documents']} / 片段 {before['chunks']} / 观点 {before['viewpoints']}")

    if args.check:
        m = store.get_method_by_key(rm._METHOD_KEY)
        inds = store.list_indicators(rm._METHOD_ID)
        res = store.list_results(rm._METHOD_ID)
        print(f"[检查] 方法 {1 if m else 0} / 证据 {len(m.get('evidence', [])) if m else 0} / 指标 {len(inds)} / 结果 {len(res)}")
        return

    # 1. 方法 + 证据 + 指标（幂等）
    r = rm.migrate_research_method(store=store)
    print(f"[迁移] 方法 {r['method']} 条 / 指标 {r['indicators']} 条 / 错误 {r['errors']}")

    # 2. 生成结果（从本地 SQLite 读，幂等写入目标）
    n_res = 0
    if args.with_results:
        for rr in local.list_results(rm._METHOD_ID):
            store.save_result(rr["result_id"], rr)
            n_res += 1
        print(f"[迁移] 生成结果 {n_res} 条")

    # 3. 核验 + 既有资料对比
    m = store.get_method_by_key(rm._METHOD_KEY)
    inds = store.list_indicators(rm._METHOD_ID)
    after = _counts(store)
    print(f"[核验] 方法 {1 if m else 0} 条 / 证据 {len(m.get('evidence', [])) if m else 0} 条 / 指标 {len(inds)} 条"
          f" / 结果 {len(store.list_results(rm._METHOD_ID))} 条")
    print(f"[{label}] 迁移后：文档 {after['documents']} / 片段 {after['chunks']} / 观点 {after['viewpoints']}")
    if before != after:
        print(f"[警告] 既有资料数量发生变化：{before} -> {after}")
    else:
        print("[核验] 既有 documents/chunks/viewpoints 数量未受影响。")


if __name__ == "__main__":
    main()
