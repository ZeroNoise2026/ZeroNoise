# Copilot Instructions — embedding-service

## 项目全局

QuantAgent 是一个 RAG-powered 量化交易 AI Agent，拆成 4 个微服务：

| Repo | 职责 | Port | Owner |
|------|------|------|-------|
| data-fetchers | 金融数据采集 API | 8001 | Person A |
| **embedding-service** (本Repo) | 共享文本→向量服务 | 8002 | Person A |
| data-pipeline | 清洗→分块→向量化→写入 Supabase | - (GitHub Actions) | Person A |
| copilot-template | Orchestrator + Retriever + Streamlit UI | 8501 | Person B |

**调用链:**
```
data-pipeline → POST /api/encode (批量文档向量化)
copilot-template → POST /api/encode-query (用户查询向量化)
```

## 本 Repo 说明

- **定位**: 共享的向量化服务，被 Person A 和 Person B 的服务同时调用
- **框架**: FastAPI (Python 3.11)
- **模型**: all-MiniLM-L6-v2, 384 维, 本地运行, L2 normalized for cosine similarity
- **两个核心 Endpoint**:
  - `POST /api/encode` — 批量文本 → 向量 (data-pipeline 摄入时用)
  - `POST /api/encode-query` — 单条查询 → 向量 (copilot-template 查询时用)
- **模型在启动时加载一次**, 后续请求复用

## 技术约定

- 输出向量维度: 384 (固定，与 Supabase pgvector VECTOR(384) 对应)
- 所有向量 L2 normalized (`normalize_embeddings=True`)
- 配置从 `.env` 读取，通过 `app/config.py`
- 模型缓存在内存，不需要 GPU，CPU 跑 <50ms/query

## 当前进度

- [x] Repo 骨架搭建
- [x] FastAPI app + 3 endpoints (health, encode, encode-query)
- [x] Pydantic request/response models
- [x] Dockerfile
- [ ] 单元测试
- [ ] 模型预热 (startup event)
- [ ] 批量请求大小限制
- [ ] 如果将来换模型 (BGE-small 等) 的配置化支持

## 我的角色

我是 **Person A** — 负责数据管道。这个服务虽然我搭建，但 Person B 查询时也会调用。
