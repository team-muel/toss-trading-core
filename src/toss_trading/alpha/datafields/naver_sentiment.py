"""Naver Search HUB attention / sentiment datafield (research-only).

This is the first *domestic* decision-data source for the Korea (``KOR``) leg of
the research universe.  The documented external stack (Massive, FRED, SEC EDGAR)
is entirely US-centric, so KOSPI/KOSDAQ names have no fundamental, event, or
attention feed beyond raw Toss prices.  The Naver Search HUB fills that gap for
research only: it produces an *attention* datafield (news volume) and a coarse
*sentiment* polarity datafield from Korean-language headlines.

Safety and contract notes:

- This is a decision input, never execution truth.  It cannot overwrite Toss
  account/holding/order state, and it does not place orders.
- Credentials are read from the environment (``NAVER_HUB_KEY_ID`` /
  ``NAVER_HUB_KEY``), never committed and never returned in a snapshot.
- The HTTP fetcher is injectable so tests and backtests run fully offline; the
  default fetcher uses ``urllib`` (standard library only).
- Provider-reported and locally-observed timestamps are kept separate and a
  ``stale`` flag lets dependent alphas be disabled when the feed is degraded.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import log1p

HUB_BASE_URL = "https://naverapihub.apigw.ntruss.com/search/v1"
DEFAULT_TIMEOUT = 5.0

# Coarse Korean market-move lexicon.  Deliberately small and auditable; this is a
# research starting point, not a production NLP model.
POSITIVE_TERMS = (
    "급등", "상승", "강세", "호재", "신고가", "돌파", "최대", "흑자", "수주",
    "확대", "개선", "반등", "기대", "성장", "호실적",
)
NEGATIVE_TERMS = (
    "급락", "하락", "약세", "악재", "신저가", "하한가", "적자", "감소", "부진",
    "우려", "손실", "쇼크", "리콜", "하향", "충격",
)

# fetcher(search_type, query, display, sort) -> parsed JSON dict
Fetcher = Callable[[str, str, int, str], Mapping[str, object]]


class NaverHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatafieldSnapshot:
    """One cross-section of a datafield plus provenance for the stale gate."""

    field_name: str
    source: str
    as_of: str
    values: dict[str, float]
    missing: tuple[str, ...] = ()
    stale: bool = False
    raw: dict[str, object] = field(default_factory=dict)


def _default_fetcher(search_type: str, query: str, display: int, sort: str) -> dict[str, object]:
    key_id = os.environ.get("NAVER_HUB_KEY_ID")
    key = os.environ.get("NAVER_HUB_KEY")
    if not key_id or not key:
        raise NaverHubError("NAVER_HUB_KEY_ID / NAVER_HUB_KEY are not set")
    params = urllib.parse.urlencode(
        {"query": query, "display": display, "sort": sort, "format": "json"}
    )
    url = f"{HUB_BASE_URL}/{search_type}?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "X-NCP-APIGW-API-KEY-ID": key_id,
            "X-NCP-APIGW-API-KEY": key,
        },
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:  # noqa: S310
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise NaverHubError(f"{search_type}: expected JSON object response")
    return parsed


def _strip_markup(text: str) -> str:
    out = []
    inside = False
    for char in text:
        if char == "<":
            inside = True
        elif char == ">":
            inside = False
        elif not inside:
            out.append(char)
    return (
        "".join(out)
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _polarity(text: str) -> int:
    cleaned = _strip_markup(text)
    score = 0
    for term in POSITIVE_TERMS:
        if term in cleaned:
            score += 1
    for term in NEGATIVE_TERMS:
        if term in cleaned:
            score -= 1
    return score


def attention_datafield(
    queries: Mapping[str, str],
    *,
    as_of: str,
    fetcher: Fetcher | None = None,
    search_type: str = "news",
) -> DatafieldSnapshot:
    """Attention = ``log1p`` of the reported result total per symbol.

    ``queries`` maps symbol -> Korean search phrase (usually the company name).
    """
    fetch = fetcher or _default_fetcher
    values: dict[str, float] = {}
    missing: list[str] = []
    raw: dict[str, object] = {}
    for symbol, query in queries.items():
        try:
            body = fetch(search_type, query, 1, "date")
            total = body.get("total")
            if not isinstance(total, (int, float)):
                missing.append(symbol)
                continue
            values[symbol] = log1p(float(total))
            raw[symbol] = {"total": total}
        except (NaverHubError, OSError, ValueError):
            missing.append(symbol)
    stale = len(values) == 0
    return DatafieldSnapshot(
        field_name="naver_attention",
        source="naver_hub",
        as_of=as_of,
        values=values,
        missing=tuple(missing),
        stale=stale,
        raw=raw,
    )


def sentiment_datafield(
    queries: Mapping[str, str],
    *,
    as_of: str,
    fetcher: Fetcher | None = None,
    display: int = 10,
    search_type: str = "news",
) -> DatafieldSnapshot:
    """Mean headline polarity per symbol, normalised to roughly ``[-1, 1]``.

    Uses the small auditable lexicon above over the most recent ``display``
    headlines' title + description.
    """
    fetch = fetcher or _default_fetcher
    values: dict[str, float] = {}
    missing: list[str] = []
    raw: dict[str, object] = {}
    for symbol, query in queries.items():
        try:
            body = fetch(search_type, query, display, "date")
            items = body.get("items")
            if not isinstance(items, Sequence) or not items:
                missing.append(symbol)
                continue
            scores: list[int] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                text = f"{item.get('title', '')} {item.get('description', '')}"
                scores.append(_polarity(text))
            if not scores:
                missing.append(symbol)
                continue
            mean_score = sum(scores) / len(scores)
            # squash to (-1, 1) without importing tanh-heavy machinery
            values[symbol] = mean_score / (1 + abs(mean_score))
            raw[symbol] = {"headlines_scored": len(scores)}
        except (NaverHubError, OSError, ValueError):
            missing.append(symbol)
    stale = len(values) == 0
    return DatafieldSnapshot(
        field_name="naver_sentiment",
        source="naver_hub",
        as_of=as_of,
        values=values,
        missing=tuple(missing),
        stale=stale,
        raw=raw,
    )
