# 内部知识库（core/knowledge/）

内部研究材料的「入库 → 检索原文 → 带引用问答 → 历史观点辅助点评」服务层，独立于
Streamlit 页面，供页面与 Agent 共同调用。

## 目录结构

| 路径 | 职责 |
| --- | --- |
| `config.py` | 统一配置（路径 / 检索模式 / 权限 / 存储后端），读环境变量或 Streamlit secrets |
| `models.py` | 数据模型与枚举（解析状态 / 来源类别 / 观点状态与关系） |
| `storage.py` | 持久化层（SQLite 后端 + 抽象接口，云端可替换） |
| `parsers.py` | 文件解析（PDF / DOCX / MD / TXT / RTF / 对话） |
| `indexer.py` | 入库管线（状态机 / 去重 / 版本 / 删除 / 扫描） |
| `retriever.py` | 检索服务（关键词 + 可选语义融合 + 筛选 + 上下文） |
| `viewpoints.py` | 历史观点记录与查询 |
| `commentary.py` | 基于历史材料的新事件点评 |
| `auth.py` | 服务端权限校验 |
| `llm.py` | 大模型调用封装（Anthropic 兼容 `/v1/messages`） |
| `cli.py` | 命令行运维入口 |
| `raw_docs/` | 正式入库资料目录（正文被 Git 忽略，见其中 README） |
| `store/` | SQLite 持久化（文档/片段/观点/点评，被 Git 忽略） |

## 核心服务函数（Agent 调用入口）

```python
from core import knowledge as kb

# 检索证据（显式传入用户身份与筛选条件，权限在服务层执行）
res = kb.search_materials("碳化硅 降息", user="local-user", top_k=5,
                          filters={"date_to": "2026-09-25", "source_categories": ["内部观点"]})

# 读取证据原文与前后文
ev = kb.read_evidence("ev_xxx", user="local-user", context_radius=2)

# 查询历史观点（按研究对象 / 截止日期 / 状态）
hist = kb.get_viewpoint_history("碳化硅", user="local-user", cutoff_date="2026-09-25")
```

其余函数见 `core/knowledge/__init__.py` 的 `__all__`。

## 配置项（环境变量或 Streamlit Secrets）

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `KNOWLEDGE_MATERIALS_DIR` | 资料目录 | `core/knowledge/raw_docs` |
| `KNOWLEDGE_STORE_DIR` | 持久化目录 | `core/knowledge/store` |
| `KNOWLEDGE_STORAGE_BACKEND` | 存储后端（第一版仅 `local`） | `local` |
| `KNOWLEDGE_ACCESS_MODE` | `open` / `password` / `disabled` | `disabled` |
| `KNOWLEDGE_ACCESS_PASSWORD` | password 模式口令 | — |
| `KNOWLEDGE_ALLOWED_USERS` | 白名单用户（逗号分隔） | — |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | 语义检索（可选） | 未配置 |
| `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_DEFAULT_HAIKU_MODEL` | 大模型接口 | 见 `llm.py` |

## 权限与持久化

- **权限**：未配置认证（`disabled`）时匿名用户无权访问内部资料；资料列表 / 搜索 / 原文 /
  观点 / 点评全部在服务层校验，不靠隐藏菜单。
- **持久化**：第一版为本地 SQLite（开发 / 单机模式）。Streamlit 社区云的文件系统是临时的，
  重启可能丢失；云上须把 `KNOWLEDGE_STORE_DIR` 指向挂载的持久化卷，或实现
  `StorageAdapter` 的远程后端（对象存储 + 托管数据库）。数据流与权限边界见交付说明。
- **隐私**：内部材料正文、解析片段与索引均在 `.gitignore` 排除范围内，绝不进入版本库。

## 命令行

```bash
KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli scan
KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli ingest 文件.pdf --source-category 内部观点
KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli search "碳化硅" --top-k 5
KNOWLEDGE_ACCESS_MODE=open python -m core.knowledge.cli stats
```

## 历史观点与事件点评的纪律

- AI 提取的观点默认「待核验」，需人工确认；外部研报不自动变成团队观点；AI 回答不自动
  变成老师认可的观点。
- 新观点不覆盖旧观点；修订 / 推翻生成新记录并保留旧记录，历史可追溯。
- 有截止日期的研究只使用当时已公开 / 已形成的材料；日期未知的材料单独提示。
- 事件点评同时检索支持与冲突证据，不只看多；新材料为用户粘贴时标注「来源未独立核验」。
- 生成的点评来源类别为「AI生成内容」，未经确认不得当作团队已确认观点循环引用。
