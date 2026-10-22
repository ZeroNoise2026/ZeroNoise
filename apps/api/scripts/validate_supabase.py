"""Quick Supabase validation script."""
import os
from dotenv import load_dotenv

load_dotenv()

from supabase import create_client

client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
print("=== Supabase Connection: OK ===\n")

# 1. Table row counts
print("--- Table Row Counts ---")
for t in ["documents", "earnings", "price_snapshot", "tracked_tickers"]:
    try:
        r = client.table(t).select("*", count="exact").limit(0).execute()
        print(f"  {t}: {r.count} rows")
    except Exception as e:
        print(f"  {t}: ERROR - {e}")

# 2. Documents by ticker
print("\n--- Documents by Ticker ---")
docs = client.table("documents").select("ticker").execute()
counts = {}
for d in docs.data:
    t = d["ticker"]
    counts[t] = counts.get(t, 0) + 1
for ticker in sorted(counts.keys()):
    print(f"  {ticker}: {counts[ticker]}")

# 3. Tracked tickers
print("\n--- Tracked Tickers ---")
tt = client.table("tracked_tickers").select("ticker, is_active, ticker_type").order("ticker").execute()
for row in tt.data:
    print(f"  {row['ticker']}  active={row['is_active']}  type={row.get('ticker_type', 'N/A')}")

# 4. Check match_documents RPC
print("\n--- match_documents RPC ---")
try:
    dummy = [0.01] * 384
    r = client.rpc("match_documents", {
        "query_embedding": dummy,
        "match_count": 1,
        "similarity_threshold": 0.0,
    }).execute()
    print(f"  Status: OK (returned {len(r.data)} rows)")
    if r.data:
        print(f"  Sample: ticker={r.data[0].get('ticker')}, doc_type={r.data[0].get('doc_type')}, sim={r.data[0].get('similarity')}")
except Exception as e:
    err = str(e)
    if "Could not find the function" in err or "does not exist" in err:
        print("  Status: NOT FOUND — need to run scripts/setup_match_documents.sql in Supabase SQL Editor")
    else:
        print(f"  Status: ERROR — {err}")

# 5. Sample document to check embedding presence
print("\n--- Sample Document (embedding check) ---")
sample = client.table("documents").select("id, ticker, doc_type, date, title").limit(3).execute()
for d in sample.data:
    print(f"  id={d['id'][:12]}... ticker={d['ticker']} type={d['doc_type']} date={d.get('date','N/A')} title={str(d.get('title',''))[:50]}")

print("\n=== Validation Complete ===")
