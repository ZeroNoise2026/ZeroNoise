import numpy as np
from store.db import upsert_documents

fake_doc = {
    "id": "test_001",
    "content": "Apple reports record Q1 earnings",
    "ticker": "AAPL",
    "date": "2026-03-15",
    "source": "finnhub",
    "doc_type": "news",
    "section": None,
    "title": "Apple Q1 Earnings"
}
fake_embedding = np.random.rand(1, 384).astype(np.float32)

count = upsert_documents([fake_doc], fake_embedding)
print(f"Inserted: {count}")