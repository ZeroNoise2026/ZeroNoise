"""
pipeline/chunker.py
Text chunking strategies:
  - News: no splitting (headline+summary is typically < 300 chars)
  - Earnings/EDGAR long text: 512 chars + 64 chars overlap, split at sentence boundaries

Design considerations (Scale):
  - 512 chars ≈ 128 tokens, all-MiniLM-L6-v2 max 256 tokens, safe margin
  - overlap 64 chars preserves cross-chunk context, avoids semantic gaps
  - Sentence boundary priority: avoids cutting in the middle of words, improves embedding quality
  - Minimum chunk 50 chars: chunks too short (e.g. headers/footers) carry no information, discard them
"""

from typing import List

CHUNK_SIZE    = 512   # max characters per chunk
CHUNK_OVERLAP = 64    # overlapping characters between adjacent chunks
MIN_CHUNK_LEN = 50    # chunks shorter than this are discarded

# Sentence boundary markers (used to find split points)
_SENTENCE_ENDS = ("。", ". ", "! ", "? ", ".\n", "!\n", "?\n", "\n\n")


def _find_sentence_boundary(text: str, start: int, end: int) -> int:
    """Find the nearest sentence boundary searching backward from end within [start, end].
    Returns the boundary position if found, otherwise returns end (hard cut).
    """
    best = -1
    for sep in _SENTENCE_ENDS:
        pos = text.rfind(sep, start + CHUNK_SIZE // 2, end)
        if pos > best:
            best = pos + len(sep)  # cut after the separator
    return best if best > start else end


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """Split arbitrary long text into overlapping chunks, preferring sentence boundaries.

    Args:
        text: plain text already cleaned by the cleaner
        chunk_size: max characters per chunk, default 512
        overlap: overlapping characters between adjacent chunks, default 64

    Returns:
        List[str], each chunk >= MIN_CHUNK_LEN characters
    """
    text = text.strip()
    if not text:
        return []

    # Short text doesn't need splitting
    if len(text) <= chunk_size:
        return [text] if len(text) >= MIN_CHUNK_LEN else []

    chunks: List[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            # Last chunk: take the remaining text
            chunk = text[start:].strip()
        else:
            # Try to split at a sentence boundary
            cut = _find_sentence_boundary(text, start, end)
            chunk = text[start:cut].strip()
            end = cut  # next iteration starts from cut minus overlap

        if len(chunk) >= MIN_CHUNK_LEN:
            chunks.append(chunk)

        # Next chunk starts at (end - overlap) to preserve context
        next_start = end - overlap
        if next_start <= start:
            # Prevent infinite loop (edge case: no boundary found in the entire segment)
            next_start = start + chunk_size
        start = next_start

    return chunks


def chunk_news(text: str) -> List[str]:
    """News items: no splitting, the entire item becomes one chunk.

    News headline+summary is typically 100-400 chars, well below 512.
    Splitting would break semantic integrity.
    """
    text = text.strip()
    return [text] if len(text) >= MIN_CHUNK_LEN else []


def chunk_filing(text: str) -> List[str]:
    """SEC filing full text: split into 512 chars + 64 overlap chunks.

    Filing full text can reach hundreds of thousands of characters and must be chunked for vectorization.
    """
    return chunk_text(text)


def chunk_xbrl_facts(ticker: str, facts_us_gaap: dict) -> List[str]:
    """Convert EDGAR XBRL facts into natural language description chunks for vectorization.

    XBRL is structured data that needs to be converted to text for RAG.
    Each core financial metric generates a one-sentence description,
    and several sentences are packed into one chunk.

    Args:
        ticker: stock ticker symbol
        facts_us_gaap: the us-gaap dict returned by edgar_client.get_company_facts()

    Returns:
        List[str], each chunk describes several quarters of financial data
    """
    # Only extract core metrics valuable for RAG
    METRICS = {
        "NetIncomeLoss":                          "Net Income",
        "Revenues":                               "Revenue",
        "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
        "EarningsPerShareBasic":                  "EPS (Basic)",
        "EarningsPerShareDiluted":                "EPS (Diluted)",
        "GrossProfit":                            "Gross Profit",
        "OperatingIncomeLoss":                    "Operating Income",
        "CashAndCashEquivalentsAtCarryingValue":  "Cash",
        "LongTermDebt":                           "Long-term Debt",
    }

    sentences: List[str] = []
    for xbrl_key, human_label in METRICS.items():
        if xbrl_key not in facts_us_gaap:
            continue
        units = facts_us_gaap[xbrl_key].get("units", {})
        values = units.get("USD", units.get("shares", []))
        # Only take the most recent 8 quarters (10-Q + 10-K)
        quarterly = [v for v in values if v.get("form") in ("10-Q", "10-K")][-8:]
        for v in quarterly:
            end_date = v.get("end", "")
            val      = v.get("val", 0)
            form     = v.get("form", "")
            sentences.append(
                f"{ticker} {human_label} as of {end_date} ({form}): {val:,}"
            )

    # Pack every 10 sentences into one chunk (~400 chars), maintaining semantic relevance
    chunks: List[str] = []
    for i in range(0, len(sentences), 10):
        chunk = "\n".join(sentences[i : i + 10])
        if len(chunk) >= MIN_CHUNK_LEN:
            chunks.append(chunk)

    return chunks
