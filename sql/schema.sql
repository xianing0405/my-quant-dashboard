-- 内部知识库 Postgres 后端建表脚本（可重复执行，非破坏性）
-- 在 Supabase → SQL Editor 里运行本文件即可；重复运行不丢数据。
--
-- 权限：所有表开启 RLS 且不加任何 anon/authenticated 策略，
--       因此通过 REST(PostgREST) + anon key 无法读取这些表；
--       应用通过 service_role（绕过 RLS）或直连 Postgres 访问。

create table if not exists documents (
    document_id text primary key,
    title text not null,
    file_type text not null,
    file_hash text not null,
    version integer not null default 1,
    publish_date text,
    uploaded_at text,
    author text,
    organization text,
    source_category text,
    companies text not null default '[]',
    industry_topics text not null default '[]',
    permission_scope text not null default 'internal',
    parse_status text not null,
    error_message text,
    original_path text,
    provenance text,
    previous_version_id text,
    ai_generated_note text,
    created_at text
);
create index if not exists idx_documents_hash on documents(file_hash);
create index if not exists idx_documents_title on documents(title);

create table if not exists chunks (
    evidence_id text primary key,
    document_id text not null references documents(document_id) on delete cascade,
    seq integer not null,
    text text not null,
    page_number integer,
    position text,
    heading text,
    speaker text,
    ts text,
    role text,
    parent_evidence_id text,
    is_ai_generated integer not null default 0
);
create index if not exists idx_chunks_doc on chunks(document_id, seq);

create table if not exists viewpoints (
    viewpoint_id text primary key,
    research_subject text,
    proposer text,
    source_category text,
    viewpoint_date text,
    core_judgment text,
    supporting_evidence text,
    conditions text,
    risks text,
    evidence_ids text not null default '[]',
    status text,
    relationships text not null default '[]',
    document_id text,
    is_ai_generated integer not null default 1,
    created_at text,
    updated_at text
);
create index if not exists idx_viewpoints_subject on viewpoints(research_subject);

create table if not exists commentaries (
    commentary_id text primary key,
    event_text text,
    subject text,
    cutoff_date text,
    output_markdown text,
    source_category text not null default 'AI生成内容',
    created_at text
);

-- 开启行级安全，且不创建任何 anon/authenticated 策略 => 匿名客户端不可读
alter table documents enable row level security;
alter table chunks enable row level security;
alter table viewpoints enable row level security;
alter table commentaries enable row level security;
