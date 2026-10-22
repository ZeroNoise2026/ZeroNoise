"""
question/router.py
Two-layer router: L1 keyword matching (free) -> L2 LLM fallback (when L1 misses)

L1: Regex ticker extraction + keyword intent matching — zero cost, <1ms
L2: LLM classification — ~100 tokens, ~500ms, only when L1 score=0 or no tickers found
"""

import json
import re
import logging
from typing import Optional

from question.models import Intent, QueryMode, Recency, RouterResult

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Company aliases -> ticker mapping
# ═══════════════════════════════════════════

COMPANY_ALIASES: dict[str, str] = {
    "Apple": "AAPL", "Amazon": "AMZN", "Google": "GOOGL",
    "Alphabet": "GOOGL", "Meta": "META", "Facebook": "META",
    "Microsoft": "MSFT", "Nvidia": "NVDA", "Tesla": "TSLA",
    "S&P": "SPY", "Nasdaq": "QQQ",
    # Commonly referenced by description, not ticker
    "ON Semiconductor": "ON", "ServiceNow": "NOW", "Allstate": "ALL",
    "Ford": "F", "Visa": "V", "AT&T": "T", "US Steel": "X",
    "Alibaba": "BABA", "JPMorgan": "JPM", "Goldman": "GS",
    "iPhone maker": "AAPL", "search giant": "GOOGL",
    # Cloud providers — implicit ticker mapping
    "AWS": "AMZN", "Azure": "MSFT", "GCP": "GOOGL",
}

# Phrases that map to multiple tickers (one-to-many)
PHRASE_TO_TICKERS: dict[str, list[str]] = {
    "cloud provider": ["AMZN", "MSFT", "GOOGL"],
    "cloud computing": ["AMZN", "MSFT", "GOOGL"],
    "big tech": ["AAPL", "AMZN", "GOOGL", "META", "MSFT"],
    "magnificent seven": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "mag 7": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "faang": ["META", "AAPL", "AMZN", "NVDA", "GOOGL"],
}

# ═══════════════════════════════════════════
# Intent keyword table
# ═══════════════════════════════════════════

INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.NEWS_SUMMARY: [
        "news", "headline", "what happened",
        "recent news", "latest news", "announcement", "development",
    ],
    Intent.EARNINGS_ANALYSIS: [
        "earnings", "revenue", "income", "profit", "quarterly",
        "EPS", "net income", "gross margin", "financial",
        "latest earnings", "earnings report",
    ],
    Intent.PRICE_QUERY: [
        "price", "quote", "market cap", "valuation",
        "how much", "stock price", "PE", "P/E ratio",
    ],
    Intent.TRADE_SUGGESTION: [
        "buy", "sell", "hold", "recommend", "should I",
        "worth buying", "investment", "trade", "position",
    ],
    Intent.COMPARISON: [
        "compare", "versus", "vs", "better", "difference",
        "which one", "head to head",
    ],
    Intent.GENERAL_ANALYSIS: [
        "overview", "general", "how is", "how are",
        "tell me about", "what about", "outlook",
        "doing financially", "update on", "analysis of",
    ],
}

# Tie-breaking priority: higher = wins when scores are equal
# COMPARISON and TRADE are more specific intents — should win ties
INTENT_PRIORITY: dict[Intent, int] = {
    Intent.NEWS_SUMMARY:      1,
    Intent.EARNINGS_ANALYSIS: 2,
    Intent.PRICE_QUERY:       2,
    Intent.TRADE_SUGGESTION:  3,
    Intent.COMPARISON:        4,
    Intent.GENERAL_ANALYSIS:  0,
}

# Intent -> retrieval mode mapping
INTENT_TO_MODE: dict[Intent, QueryMode] = {
    Intent.NEWS_SUMMARY:      QueryMode.SEMANTIC,     # news -> semantic search
    Intent.EARNINGS_ANALYSIS: QueryMode.HYBRID,        # earnings -> semantic + structured
    Intent.PRICE_QUERY:       QueryMode.STRUCTURED,    # price -> pure structured
    Intent.TRADE_SUGGESTION:  QueryMode.HYBRID,        # suggestion -> need all data
    Intent.COMPARISON:        QueryMode.HYBRID,        # comparison -> need all data
    Intent.GENERAL_ANALYSIS:  QueryMode.HYBRID,        # fallback -> a bit of everything
}

# ═══════════════════════════════════════════
# Recency keyword table (L1)
# ═══════════════════════════════════════════
RECENCY_KEYWORDS: dict[Recency, list[str]] = {
    Recency.HIGH: [
        # English
        "latest", "today", "yesterday", "recent", "recently",
        "now", "current", "this week", "this morning", "breaking",
        "just announced", "any news", "what's new",
        # Chinese keywords (kept as data for matching Chinese user input)
        "最新", "今天", "昨天", "最近", "现在", "当前", "本周",
    ],
    Recency.NONE: [
        # Historical / time-bounded queries — do NOT bias to recent
        "last year", "history", "historical", "historically",
        "during covid", "during 2020", "during 2021", "during 2022",
        "in 2020", "in 2021", "in 2022", "in 2023", "in 2024",
        "all-time", "all time", "ever", "since", "back in",
        # Chinese keywords (kept as data for matching Chinese user input)
        "去年", "历史", "过去", "以前", "当年",
    ],
    # Recency.LOW reserved for future use (educational / explanation queries)
}

# Intent -> default recency level (used when L1 recency keywords miss).
# News-leaning intents default to MEDIUM-leaning-HIGH; historical lookups stay MEDIUM.
INTENT_TO_DEFAULT_RECENCY: dict[Intent, Recency] = {
    Intent.NEWS_SUMMARY:      Recency.HIGH,
    Intent.EARNINGS_ANALYSIS: Recency.MEDIUM,
    Intent.PRICE_QUERY:       Recency.HIGH,
    Intent.TRADE_SUGGESTION:  Recency.MEDIUM,
    Intent.COMPARISON:        Recency.MEDIUM,
    Intent.GENERAL_ANALYSIS:  Recency.MEDIUM,
}

# Common English words to exclude from ticker extraction
# Known single-letter tickers — DO NOT put these in COMMON_WORDS
# F=Ford, V=Visa, C=Citigroup, X=US Steel, T=AT&T
SINGLE_LETTER_TICKERS = {"F", "V", "C", "X", "T"}

# Ambiguous tickers that are also very common English words.
# These are ONLY blocked from regex extraction; L2 LLM can still return them.
# Real tickers removed (query them via L2): ALL, NOW, HAS, IPO, etc.
COMMON_WORDS = {
    # Single letters (excluding real tickers in SINGLE_LETTER_TICKERS)
    "I", "A", "B", "D", "E", "G", "H", "K",
    "M", "N", "O", "P", "Q", "R", "S", "U", "W", "Y",
    # 2-letter common words (ON=ON Semi, BE=Bloom Energy — too common as English)
    "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO",
    "HE", "IF", "IN", "IS", "IT", "ME", "MY", "NO",
    "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    # 3-letter common words (removed real tickers: ALL, NOW, HAS, ANY, CAN, NEW, OUT, OWN, OLD)
    "THE", "AND", "FOR", "NOT", "ARE", "BUT", "WAS",
    "HER", "HIS", "HOW", "ITS", "MAY", "OUR", "SAY",
    "SHE", "TOO", "USE", "WHO", "WHY", "YET", "GOT",
    "LET", "PUT", "RUN", "SET", "TRY", "WAY", "VS",
    # 4+ letter common words
    "THIS", "THAT", "WITH", "FROM", "WILL", "WHAT",
    "BEEN", "HAVE", "JUST", "MORE", "ALSO", "THAN",
    # Finance terms (not tickers)
    "BUY", "SELL", "HOLD", "EPS", "CEO", "CFO",
    "ETF", "GDP", "API", "USA", "IPO", "FAANG",
}


# ═══════════════════════════════════════════
# Quarter extraction (P0-A fix)
# ═══════════════════════════════════════════

# Patterns covering:
#   "Q3 2025", "Q3/2025", "Q3-2025", "Q3,2025"
#   "2025 Q3", "2025Q3"
#   "third quarter 2025", "3rd quarter of 2025"
#   "Q3 25" (2-digit year → treat as 20xx)
_QUARTER_PATTERNS = [
    re.compile(r"\bQ([1-4])\s*[-/,\s]+\s*(\d{4})\b", re.IGNORECASE),
    re.compile(r"\bQ([1-4])\s+(\d{2})\b", re.IGNORECASE),          # Q3 25
    re.compile(r"\b(\d{4})\s*Q([1-4])\b", re.IGNORECASE),          # 2025 Q3 / 2025Q3 — group order (year, q)!
    re.compile(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?(\d{4})\b", re.IGNORECASE),
]
_WORD_TO_Q = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def _extract_quarter(query: str) -> Optional[str]:
    """Extract quarter mention from query → 'Q3 2025' format. Returns None if not found."""
    for idx, pat in enumerate(_QUARTER_PATTERNS):
        m = pat.search(query)
        if not m:
            continue
        g1, g2 = m.group(1), m.group(2)
        # Pattern 2 (year Q): group order is (year, q) — normalize
        if idx == 2:
            year_s, q_s = g1, g2
        elif idx == 3:
            q_s, year_s = str(_WORD_TO_Q.get(g1.lower(), 0)), g2
            if q_s == "0":
                continue
        else:
            q_s, year_s = g1, g2
        # Normalize 2-digit year
        if len(year_s) == 2:
            year_s = "20" + year_s
        try:
            q_i = int(q_s)
            y_i = int(year_s)
            if 1 <= q_i <= 4 and 2000 <= y_i <= 2099:
                return f"Q{q_i} {y_i}"
        except ValueError:
            continue
    return None


class QueryRouter:
    """L1 keyword router, with L2 LLM fallback when keywords miss."""

    # ── L2 LLM classification prompt (kept minimal for cost) ──
    _CLASSIFY_SYSTEM = (
        "You are a financial query classifier and query expander. "
        "Given a user question, return ONLY a JSON object with four fields:\n"
        '  "intent": one of ["news_summary","earnings_analysis","price_query","trade_suggestion","comparison","general_analysis"]\n'
        '  "tickers": a list of US stock ticker symbols mentioned or implied (e.g. ["AAPL","MSFT"]). '
        "Use standard ticker symbols. Return [] if no specific company is mentioned.\n"
        '  "recency": one of ["high","medium","low","none"] — how fresh the data should be.\n'
        "    - high: user wants latest/recent/today/yesterday/breaking news.\n"
        "    - medium: default for typical questions.\n"
        "    - low: explanation or educational query, freshness barely matters.\n"
        "    - none: historical query (e.g. 'during COVID', 'last year', 'in 2020', 'all-time high').\n"
        '  "expanded_queries": a list of 3-4 alternative search queries covering '
        "different CONCRETE topical angles of the user's question, used for multi-vector retrieval. "
        "Each query 4-8 English words, MUST mention the ticker(s) explicitly. "
        "STRICT RULES:\n"
        "    - Each query MUST be a SPECIFIC topic/event, not a paraphrase. "
        "Forbidden generic words: 'latest', 'news', 'headlines', 'update', 'recent', 'today'. "
        "Forbidden generic phrases: 'X news', 'X update', 'X stock movement'.\n"
        "    - Cover diverse angles such as: product/supply chain (e.g. 'Apple iPhone China shipments'), "
        "analyst actions (e.g. 'AAPL price target upgrade Goldman'), "
        "regulatory/legal (e.g. 'Apple antitrust lawsuit DOJ'), "
        "earnings/guidance (e.g. 'Apple Q2 revenue forecast'), "
        "executive/insider moves (e.g. 'Apple CEO insider sale').\n"
        "    - Return [] when intent is price_query or recency is 'none' (historical), "
        "or when no ticker is identified.\n"
        "Return ONLY valid JSON, no explanation."
    )

    _CLASSIFY_EXAMPLES = [
        {"role": "user", "content": "How is the iPhone maker doing financially?"},
        {"role": "assistant", "content": '{"intent":"general_analysis","tickers":["AAPL"],"recency":"medium","expanded_queries":["AAPL revenue and earnings growth","Apple iPhone sales performance","AAPL profit margin and guidance"]}'},
        {"role": "user", "content": "Which cloud provider has better margins, AWS or Azure?"},
        {"role": "assistant", "content": '{"intent":"comparison","tickers":["AMZN","MSFT"],"recency":"medium","expanded_queries":["AWS operating margin growth","Azure cloud profitability Microsoft","AMZN MSFT cloud segment revenue"]}'},
        {"role": "user", "content": "What's the latest news on AAPL?"},
        {"role": "assistant", "content": '{"intent":"news_summary","tickers":["AAPL"],"recency":"high","expanded_queries":["Apple iPhone China shipments demand","AAPL analyst price target Goldman BofA","Apple antitrust lawsuit DOJ ruling","Apple App Store services revenue"]}'},
        {"role": "user", "content": "How did AAPL perform during the 2020 COVID crash?"},
        {"role": "assistant", "content": '{"intent":"general_analysis","tickers":["AAPL"],"recency":"none","expanded_queries":[]}'},
        {"role": "user", "content": "What did Apple announce yesterday?"},
        {"role": "assistant", "content": '{"intent":"news_summary","tickers":["AAPL"],"recency":"high","expanded_queries":["Apple new product launch reveal","AAPL executive guidance statement","Apple supply chain manufacturing announcement","Apple acquisition partnership deal"]}'},
    ]

    # Structured-output schema for the L2 call — guarantees parseable JSON.
    _CLASSIFY_SCHEMA = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": [
                "news_summary", "earnings_analysis", "price_query",
                "trade_suggestion", "comparison", "general_analysis",
            ]},
            "tickers": {"type": "array", "items": {"type": "string"}},
            "recency": {"type": "string", "enum": ["high", "medium", "low", "none"]},
            "expanded_queries": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intent", "tickers", "recency", "expanded_queries"],
        "additionalProperties": False,
    }

    def route(
        self,
        query: str,
        explicit_tickers: Optional[list[str]] = None,
        context_tickers: Optional[list[str]] = None,
    ) -> RouterResult:
        tickers = self._extract_tickers(query)
        if explicit_tickers:
            tickers = list(set(tickers + [t.upper() for t in explicit_tickers]))

        # Extract explicit quarter mention ("Q3 2025" etc.) — used by structured
        # earnings / comparison to pin the latest-quarter row instead of defaulting
        # to rows[0].
        quarter = _extract_quarter(query)

        intent, score = self._detect_intent(query)

        # L1 recency detection — keyword scan; falls back to intent default if nothing matched
        recency_l1 = self._detect_recency(query)
        recency = recency_l1 if recency_l1 is not None else INTENT_TO_DEFAULT_RECENCY.get(intent, Recency.MEDIUM)

        expanded_queries: list[str] = []

        # Decide whether to call L2 LLM:
        #   (a) L1 missed (score=0 or no ticker), OR
        #   (b) recency=high and we have at least one ticker → kick L2 to also expand the query
        # (b) lets us reuse the existing LLM call for query-expansion at zero extra cost.
        l1_complete = (score > 0 and (len(tickers) > 0 or explicit_tickers))
        wants_expansion = (recency == Recency.HIGH) and (len(tickers) > 0 or explicit_tickers)
        needs_l2 = (not l1_complete) or wants_expansion

        if needs_l2:
            reason = "L1 incomplete" if not l1_complete else "query expansion (recency=high)"
            logger.info(
                f"{reason} (score={score}, tickers={tickers}) "
                f"for: \"{query[:60]}\" -> calling L2 LLM classify"
            )
            l2_intent, l2_tickers, l2_recency, l2_expanded = self._llm_classify(query)

            if l2_intent is not None and score == 0:
                intent = l2_intent
                # Re-derive default recency from new intent if L1 keyword didn't match either
                if recency_l1 is None and l2_recency is None:
                    recency = INTENT_TO_DEFAULT_RECENCY.get(intent, Recency.MEDIUM)
            # Only use L2 tickers if L1 found none — never let L2 inject
            # extra tickers when L1 already identified the relevant ones.
            if l2_tickers and len(tickers) == 0:
                tickers = l2_tickers
            elif l2_tickers and len(tickers) > 0:
                logger.info(f"L2 returned extra tickers {l2_tickers}, keeping L1 tickers {tickers}")
            # L2 recency overrides only when L1 didn't match a recency keyword
            if l2_recency is not None and recency_l1 is None:
                recency = l2_recency
            # Take expanded queries only when we actually want them — prevents
            # wasted multi-vector retrieval on historical / non-news queries.
            if l2_expanded and recency == Recency.HIGH:
                expanded_queries = l2_expanded

        mode = INTENT_TO_MODE.get(intent, QueryMode.HYBRID)

        # ── Ticker-less fallback (Plan D) ───────────────────────────
        # If no ticker was extracted (query-level OR via explicit_tickers),
        # branch on user's watchlist (context_tickers):
        #   0 → keep tickers=[], let downstream go generic / market-default.
        #   1 → auto-bind that ticker; LLM narrative will note the assumption.
        #   >1 → raise clarification flag; orchestrator will emit chips instead
        #        of calling the LLM.
        needs_clarification = False
        clarification_options: list[str] = []
        auto_bound_ticker: Optional[str] = None
        if len(tickers) == 0 and context_tickers:
            ctx = [t.upper() for t in context_tickers if t]
            ctx = sorted(set(ctx))
            if len(ctx) == 1:
                tickers = ctx
                auto_bound_ticker = ctx[0]
                logger.info(f"Ticker-less query — auto-bound to {ctx[0]} from watchlist")
            elif len(ctx) >= 2:
                needs_clarification = True
                clarification_options = ctx
                logger.info(f"Ticker-less query — clarification needed, options={ctx}")

        logger.info(
            f"Router result: intent={intent.value}, mode={mode.value}, "
            f"recency={recency.value}, tickers={sorted(tickers)}, "
            f"quarter={quarter}, "
            f"expanded={len(expanded_queries)} queries, "
            f"clarify={needs_clarification}, auto_bound={auto_bound_ticker}"
        )
        return RouterResult(
            tickers=sorted(tickers),
            intent=intent,
            mode=mode,
            recency=recency,
            quarter=quarter,
            expanded_queries=expanded_queries,
            needs_clarification=needs_clarification,
            clarification_options=clarification_options,
            auto_bound_ticker=auto_bound_ticker,
        )

    def _llm_classify(self, query: str) -> tuple[Optional[Intent], list[str], Optional[Recency], list[str]]:
        """L2 fallback: ask the LLM to classify intent + extract tickers + detect recency + expand queries.

        Returns (intent_or_None, tickers_list, recency_or_None, expanded_queries).
        On any failure, returns (None, [], None, []) — the caller keeps L1 defaults.
        Cost: ~350 tokens total (system+examples+query+response w/ expansion), ~700ms.
        """
        from common.llm import chat, has_api_key
        from config import LLM_MODEL_CLASSIFY

        if not has_api_key():
            logger.warning("ANTHROPIC_API_KEY not set — L2 LLM fallback disabled")
            return None, [], None, []

        messages = [
            {"role": "system", "content": self._CLASSIFY_SYSTEM},
            *self._CLASSIFY_EXAMPLES,
            {"role": "user", "content": query},
        ]

        try:
            raw, usage = chat(
                messages=messages,
                model=LLM_MODEL_CLASSIFY,
                effort="low",        # classification — keep router fast
                output_schema=self._CLASSIFY_SCHEMA,
                max_tokens=2000,     # headroom for thinking + expanded_queries array
                max_retries=1,       # don't retry — keep router fast
                timeout=10.0,
            )
            raw = raw.strip()
            logger.info(
                f"L2 LLM classify: tokens={usage['total_tokens']}, raw={raw[:200]}"
            )
            return self._parse_classify_response(raw)

        except Exception as e:
            logger.warning(f"L2 LLM classify failed, falling back to L1 defaults: {e}")
            return None, [], None, []

    def _parse_classify_response(self, raw: str) -> tuple[Optional[Intent], list[str], Optional[Recency], list[str]]:
        """Parse the LLM's JSON response into (Intent, tickers, Recency, expanded_queries).

        Handles: plain JSON, markdown-fenced JSON, partial garbage.
        """
        from config import EXPANSION_MAX_QUERIES

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to repair truncated JSON (e.g. '{"intent":"price_query","tick')
            data = self._repair_partial_json(cleaned)
            if data is None:
                logger.warning(f"L2 response not valid JSON (unrepairable): {raw[:120]}")
                return None, [], None, []
            logger.info(f"L2 partial JSON repaired: {data}")

        # Parse intent
        intent = None
        raw_intent = data.get("intent", "")
        try:
            intent = Intent(raw_intent)
        except ValueError:
            logger.warning(f"L2 returned unknown intent: {raw_intent}")

        # Parse tickers
        raw_tickers = data.get("tickers", [])
        tickers = []
        if isinstance(raw_tickers, list):
            for t in raw_tickers:
                if isinstance(t, str) and 1 <= len(t) <= 5 and t.isalpha():
                    tickers.append(t.upper())

        # Parse recency (optional — may be missing in older repaired payloads)
        recency: Optional[Recency] = None
        raw_recency = data.get("recency")
        if isinstance(raw_recency, str):
            try:
                recency = Recency(raw_recency.lower().strip())
            except ValueError:
                logger.warning(f"L2 returned unknown recency: {raw_recency}")

        # Parse expanded_queries (optional, may be empty/missing)
        # Defensive: drop generic paraphrases that defeat the purpose of multi-vector
        # retrieval (e.g. "AAPL latest news", "Apple recent headlines").
        GENERIC_TOKENS = {
            "latest", "news", "headlines", "headline", "update", "updates",
            "recent", "today", "yesterday", "breaking",
        }
        expanded: list[str] = []
        raw_exp = data.get("expanded_queries", [])
        if isinstance(raw_exp, list):
            for q in raw_exp:
                if not isinstance(q, str):
                    continue
                q = q.strip()
                if not (4 <= len(q) <= 100):
                    continue
                # Reject if query is dominated by generic tokens (>=2 hits in <=4 words)
                words = [w.lower().strip(".,!?") for w in q.split()]
                generic_hits = sum(1 for w in words if w in GENERIC_TOKENS)
                if generic_hits >= 2 or (len(words) <= 4 and generic_hits >= 1):
                    logger.info(f"Dropping generic expanded query: {q!r}")
                    continue
                expanded.append(q)
        expanded = expanded[:EXPANSION_MAX_QUERIES]

        return intent, tickers, recency, expanded

    @staticmethod
    def _repair_partial_json(raw: str) -> Optional[dict]:
        """Try to extract intent and tickers from a truncated JSON string.
        
        Handles cases like: '{"intent":"price_query","tick' (cut off at max_tokens)
        """
        result = {}
        # Extract intent
        intent_match = re.search(r'"intent"\s*:\s*"([a-z_]+)"', raw)
        if intent_match:
            result["intent"] = intent_match.group(1)
        # Extract tickers array (may be partial)
        tickers_match = re.search(r'"tickers"\s*:\s*\[([^\]]*)\]?', raw)
        if tickers_match:
            ticker_values = re.findall(r'"([A-Z]{1,5})"', tickers_match.group(1))
            result["tickers"] = ticker_values
        return result if result else None

    def _extract_tickers(self, query: str) -> list[str]:
        """Extract ticker symbols from query text.
        
        Sources:
          1. $-prefixed symbols (e.g. $F, $AAPL) — always accepted
          2. Regex: uppercase 1-5 letter words, filtered by COMMON_WORDS
             - Single-letter matches only accepted if in SINGLE_LETTER_TICKERS
               AND query is short (likely just the ticker)
          3. Company alias mapping (e.g. "Apple" -> AAPL)
        """
        tickers: set[str] = set()

        # Source 1: $-prefixed tickers (always trusted)
        dollar_matches = re.findall(r"\$([A-Z]{1,5})\b", query)
        for match in dollar_matches:
            tickers.add(match)

        # Source 2: regex match uppercase letter groups
        regex_matches = re.findall(r"\b([A-Z]{1,5})\b", query)
        for match in regex_matches:
            if match in COMMON_WORDS:
                continue
            # Single-letter: only accept known tickers in reasonably short queries
            if len(match) == 1:
                if match in SINGLE_LETTER_TICKERS and len(query.split()) <= 12:
                    tickers.add(match)
                continue
            tickers.add(match)

        # Source 3: company alias matching (case-insensitive)
        query_lower = query.lower()
        for alias, ticker in COMPANY_ALIASES.items():
            if alias.lower() in query_lower:
                tickers.add(ticker)

        # Source 4: phrase-to-tickers (one phrase -> multiple tickers)
        for phrase, phrase_tickers in PHRASE_TO_TICKERS.items():
            if phrase in query_lower:
                tickers.update(phrase_tickers)

        return list(tickers)

    def _detect_intent(self, query: str) -> tuple[Intent, int]:
        """Detect user intent via keyword matching.
        
        Returns (intent, score). score=0 means L1 completely missed.
        Uses priority tie-breaking: when two intents have the same score,
        the more specific intent (higher priority) wins.
        
        Short keywords (<=3 chars, e.g. "PE", "EPS", "vs") use word-boundary
        matching to avoid false substring hits (e.g. "happened" matching "PE").
        """
        query_lower = query.lower()
        best_intent = Intent.GENERAL_ANALYSIS
        best_score = 0
        best_priority = 0
        for intent, keywords in INTENT_KEYWORDS.items():
            score = 0
            for kw in keywords:
                kw_lower = kw.lower()
                if len(kw) <= 3:
                    # Short keywords: require word boundary to avoid false substring matches
                    if re.search(r'\b' + re.escape(kw_lower) + r'\b', query_lower):
                        score += 1
                else:
                    if kw_lower in query_lower:
                        score += 1
            priority = INTENT_PRIORITY.get(intent, 0)
            if score > best_score or (score == best_score and score > 0 and priority > best_priority):
                best_score = score
                best_intent = intent
                best_priority = priority
        return best_intent, best_score

    def _detect_recency(self, query: str) -> Optional[Recency]:
        """Detect recency intent via keyword matching (L1).

        Returns the matched Recency level, or None if no keyword matched
        (caller should then fall back to intent-derived default).

        NONE wins over HIGH if both match, since 'last year' + 'latest' usually
        means 'most recent in last year' — historical bias is the safer default.
        """
        query_lower = query.lower()
        # Check NONE first (historical wins)
        for kw in RECENCY_KEYWORDS.get(Recency.NONE, []):
            if kw in query_lower:
                return Recency.NONE
        for kw in RECENCY_KEYWORDS.get(Recency.HIGH, []):
            if kw in query_lower:
                return Recency.HIGH
        return None


# Module-level singleton
router = QueryRouter()
