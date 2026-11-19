-- 研报方法（方法卡 / 指标 / 生成结果）—— Postgres 幂等建表（本文件供云端迁移使用，本地 SQLite 由 storage.py 自动建表）
-- 说明：documents/chunks/viewpoints/commentaries 等既有表结构以现有云端库为准，本文件只补充研报方法相关三张表。

CREATE TABLE IF NOT EXISTS research_methods (
    method_id TEXT PRIMARY KEY,
    method_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    institution TEXT,
    source_url TEXT,
    report_title TEXT,
    evidence JSONB,
    params JSONB,
    unconfirmed JSONB,
    method_card_md TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS research_indicators (
    method_id TEXT NOT NULL,
    date TEXT NOT NULL,
    scheme TEXT,
    X DOUBLE PRECISION,
    Y DOUBLE PRECISION,
    A_raw DOUBLE PRECISION,
    B_raw DOUBLE PRECISION,
    quadrant TEXT,
    history JSONB,
    data_source TEXT,
    computed_at TEXT,
    PRIMARY KEY (method_id, date)
);

CREATE TABLE IF NOT EXISTS research_results (
    result_id TEXT PRIMARY KEY,
    method_id TEXT NOT NULL,
    method_version TEXT,
    input_date TEXT,
    mode TEXT,
    evidence_ids JSONB,
    evidence_snapshot JSONB,
    output_markdown TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rmethod_results ON research_results(method_id, input_date);

-- 权限：与既有 documents/chunks/viewpoints/commentaries 一致 —— 启用 RLS（默认拒绝非超级用户访问），不开放匿名/普通客户端
ALTER TABLE research_methods ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_indicators ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_results ENABLE ROW LEVEL SECURITY;
