"""
Embedding Service — FastAPI Application
Shared embedding service used by both the ingestion job (encode docs) and
the API's question flow (encode user queries).

Endpoints:
  POST /api/encode        — batch encode texts → vectors
  POST /api/encode-query  — single query → vector
  GET  /health
"""

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from app.config import EMBEDDING_MODEL, EMBEDDING_DIM

app = FastAPI(
    title="Embedding Service",
    description="Shared text → vector service (all-MiniLM-L6-v2, 384 dim)",
    version="0.1.0",
)

# Load model once at startup
model = SentenceTransformer(EMBEDDING_MODEL)


# ─── Request / Response Models ──────────────────────────────


class EncodeRequest(BaseModel):
    texts: list[str]


class EncodeResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int


class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    embedding: list[float]
    dimension: int


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "embedding-service"
    model: str = EMBEDDING_MODEL
    dimension: int = EMBEDDING_DIM


# ─── Endpoints ──────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@app.post("/api/encode", response_model=EncodeResponse)
def encode_texts(req: EncodeRequest):
    """Batch encode texts into vectors."""
    embeddings = model.encode(
        req.texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return EncodeResponse(
        embeddings=embeddings.tolist(),
        dimension=EMBEDDING_DIM,
    )


@app.post("/api/encode-query", response_model=QueryResponse)
def encode_query(req: QueryRequest):
    """Encode a single query into a vector."""
    embedding = model.encode(
        [req.query],
        normalize_embeddings=True,
    )
    return QueryResponse(
        embedding=embedding[0].tolist(),
        dimension=EMBEDDING_DIM,
    )
