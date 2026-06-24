from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from stock_analyzer.catalysts.models import NewsItem, SignalContribution


POSITIVE_KEYWORDS = {
    "acquisition",
    "approval",
    "beat",
    "beats",
    "collaboration",
    "contract",
    "guidance raised",
    "launch",
    "partnership",
    "raises guidance",
    "strategic investment",
    "upgrade",
}
NEGATIVE_KEYWORDS = {
    "bankruptcy",
    "class action",
    "cuts guidance",
    "delisting",
    "dilution",
    "downgrade",
    "financing",
    "fraud",
    "investigation",
    "lawsuit",
    "misses",
    "offering",
    "convertible debt",
    "debt financing",
    "recall",
    "sec probe",
    "share sale",
    "subpoena",
}
THEME_KEYWORDS = {
    "ai",
    "artificial intelligence",
    "autonomous",
    "chip",
    "data center",
    "defense",
    "gpu",
    "hbm",
    "inference",
    "memory",
    "nuclear",
    "quantum",
    "robotics",
    "semiconductor",
    "space",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def prepare_news_items(
    items: list[NewsItem],
    max_clusters: int = 3,
    similarity_threshold: float = 0.78,
) -> list[NewsItem]:
    prepared = [
        replace(
            item,
            url=canonicalize_url(item.url),
            category=item.category or classify_news_category(item.headline),
            fingerprint=item.fingerprint or headline_fingerprint(item.headline),
        )
        for item in items
        if item.headline.strip()
    ]
    prepared.sort(
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    clusters: list[tuple[set[str], str, str]] = []
    result: list[NewsItem] = []
    for item in prepared:
        tokens = headline_tokens(item.headline)
        matched_cluster = ""
        for existing_tokens, existing_url, cluster_id in clusters:
            same_url = bool(item.url and existing_url and item.url == existing_url)
            if same_url or _jaccard(tokens, existing_tokens) >= similarity_threshold:
                matched_cluster = cluster_id
                break
        if matched_cluster:
            continue
        cluster_id = item.fingerprint
        clusters.append((tokens, item.url, cluster_id))
        result.append(replace(item, cluster_id=cluster_id))
        if len(result) >= max_clusters:
            break
    return result


def score_news_items(
    items: list[NewsItem],
    run_at: datetime,
    provider: str,
) -> tuple[list[SignalContribution], list[str], list[str], list[str]]:
    contributions: list[SignalContribution] = []
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    for item in items:
        text = item.headline.lower()
        positive = _keyword_hits(text, POSITIVE_KEYWORDS)
        negative = _keyword_hits(text, NEGATIVE_KEYWORDS)
        themes = _keyword_hits(text, THEME_KEYWORDS)
        decay = recency_decay(item.published_at, run_at)
        score = 0.0
        score += min(len(positive) * 0.8, 2.4)
        score += min(len(themes) * 0.35, 1.0)
        score -= min(len(negative) * 1.25, 3.75)
        if item.sentiment is not None:
            score += max(min(item.sentiment, 1.0), -1.0) * 0.6
        score *= decay * max(min(item.relevance, 1.0), 0.25)
        event_id = item.cluster_id or item.fingerprint
        if abs(score) >= 0.05:
            contributions.append(
                SignalContribution(
                    category="news",
                    score_delta=round(score, 3),
                    confidence=round(0.08 + item.relevance * 0.08, 3),
                    source=provider,
                    summary=item.headline[:180],
                    event_id=event_id,
                    metadata={
                        "source": item.source,
                        "url": item.url,
                        "published_at": (
                            item.published_at.isoformat() if item.published_at else None
                        ),
                        "category": item.category,
                        "relevance": round(item.relevance, 3),
                        "sentiment": item.sentiment,
                    },
                )
            )
        if positive or themes:
            reasons.append(
                f"Relevant recent news includes {', '.join(sorted(positive | themes)[:4])}."
            )
        if negative:
            risks.append(
                f"Relevant news includes risk terms: {', '.join(sorted(negative)[:4])}."
            )
        events.append(f"News: {item.headline[:140]}")
    return contributions, _dedupe(reasons), _dedupe(risks), _dedupe(events)


def recency_decay(published_at: datetime | None, run_at: datetime) -> float:
    if published_at is None:
        return 0.4
    published = _as_utc(published_at)
    current = _as_utc(run_at)
    age_hours = max((current - published).total_seconds() / 3600, 0.0)
    if age_hours < 6:
        return 1.0
    if age_hours < 24:
        return 0.7
    if age_hours <= 72:
        return 0.4
    return 0.0


def headline_fingerprint(headline: str) -> str:
    normalized = " ".join(sorted(headline_tokens(headline)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def headline_tokens(headline: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", headline.lower())
    return {token for token in tokens if len(token) > 1 and token not in STOPWORDS}


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def classify_news_category(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ["earnings", "revenue", "eps", "guidance"]):
        return "earnings"
    if any(word in lower for word in ["offering", "financing", "dilution", "debt"]):
        return "financing"
    if any(word in lower for word in ["lawsuit", "probe", "investigation", "fraud"]):
        return "legal"
    if any(word in lower for word in ["contract", "partnership", "acquisition"]):
        return "corporate"
    if any(word in lower for word in THEME_KEYWORDS):
        return "theme"
    return "other"


def _keyword_hits(text: str, keywords: set[str]) -> set[str]:
    return {keyword for keyword in keywords if keyword in text}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
