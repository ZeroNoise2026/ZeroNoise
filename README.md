# 🔢 Embedding Service

> QuantAgent 微服务 — 共享向量化服务

将文本编码为 384 维向量（all-MiniLM-L6-v2），被 data-pipeline 和 copilot-template 共同调用。

## Endpoints

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/encode` | 批量文本 → 向量 |
| POST | `/api/encode-query` | 单条查询 → 向量 |
| GET | `/health` | 健康检查 |

## Quick Start

```bash
cp .env.example .env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8002 --reload
```

## API 示例

```bash
# 批量编码
curl -X POST http://localhost:8002/api/encode \
  -H "Content-Type: application/json" \
  -d '{"texts": ["Apple revenue grew 15%", "TSLA earnings miss"]}'

# 单条查询
curl -X POST http://localhost:8002/api/encode-query \
  -H "Content-Type: application/json" \
  -d '{"query": "AAPL最近财报怎么样"}'
```

## 关联服务

| Service | Repo | 如何调用本服务 |
|---------|------|--------------|
| data-pipeline | [data-pipeline](https://github.com/ZeroNoise2026/data-pipeline) | 摄入时 encode 文档 |
| copilot-template | [copilot-template](https://github.com/ZeroNoise2026/copilot-template) | 查询时 encode query |
