"""Fuzzy text matching used to map agent queries onto OCR/UI element text.

``click("Continue")`` must also match ``continue``, ``Continue →`` and
``CONTINUE?`` on screen. Matching is dependency-free (``difflib`` + token
logic) so it always works, even in minimal installations.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from typing import TypeVar

_STRIP_CHARS = str.maketrans({c: " " for c in "→»›«‹•·…|:;,!?"})
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_T = TypeVar("_T")


def normalize_text(text: str | None) -> str:
    """Lowercase, strip decorative characters/punctuation, collapse spaces."""
    if not text:
        return ""
    cleaned = text.casefold().translate(_STRIP_CHARS)
    cleaned = _NON_ALNUM.sub(" ", cleaned)
    return " ".join(cleaned.split())


def fuzzy_score(query: str, candidate: str) -> float:
    """Score in [0, 1]: how well ``candidate`` matches ``query``."""
    q = normalize_text(query)
    c = normalize_text(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    best = difflib.SequenceMatcher(None, q, c).ratio()
    if q in c:
        best = max(best, 0.78 + 0.17 * min(1.0, len(q) / len(c)))
    elif c in q:
        best = max(best, 0.72)
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    if q_tokens and q_tokens <= c_tokens:
        # every query token appears in the candidate ("Continue" in "Continue →")
        best = max(best, 0.88)
    intersection = len(q_tokens & c_tokens)
    if intersection:
        union = len(q_tokens | c_tokens)
        best = max(best, intersection / union)
    return min(1.0, best)


def rank_matches(
    query: str,
    candidates: Sequence[tuple[str, _T]],
    threshold: float = 0.55,
    limit: int = 10,
) -> list[tuple[_T, float]]:
    """Return the best ``limit`` candidates scoring >= ``threshold``, sorted."""
    scored: list[tuple[_T, float]] = []
    for text, payload in candidates:
        score = fuzzy_score(query, text)
        if score >= threshold:
            scored.append((payload, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[: max(0, limit)]


def best_candidate_text(texts: Iterable[str], query: str, threshold: float) -> tuple[str, float] | None:
    """Convenience: best matching raw text or None."""
    ranked = rank_matches(query, [(text, text) for text in texts], threshold=threshold, limit=1)
    if not ranked:
        return None
    payload, score = ranked[0]
    return payload, score
