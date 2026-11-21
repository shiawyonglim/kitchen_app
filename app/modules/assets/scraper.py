from __future__ import annotations

import random
import re
import statistics
from dataclasses import dataclass, field

from app.modules.assets.ai_valuator import AIValuationError, lookup_asset_valuation

_PRICE_RE = re.compile(r"RM\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)


class CarousellBlockedError(AIValuationError):
    """Legacy exception alias kept for backwards compatibility; inherits from AIValuationError."""


@dataclass
class ScrapeResult:
    median_price_myr: float
    min_price_myr: float
    max_price_myr: float
    sample_count: int
    source: str
    filtered_prices: list[float]
    raw_prices: list[float]
    summary: str = ""
    citations: list[dict[str, str]] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)


def filter_outliers(prices: list[float]) -> list[float]:
    """Remove extreme low placeholders (e.g. RM 1) and scalper outliers via standard deviation."""
    clean = [p for p in prices if p >= 10.0]
    if len(clean) < 4:
        return clean

    mean_val = statistics.mean(clean)
    stdev_val = statistics.stdev(clean)
    if stdev_val == 0:
        return clean

    filtered = [p for p in clean if (mean_val - 1.5 * stdev_val) <= p <= (mean_val + 1.5 * stdev_val)]
    return filtered if filtered else clean


def _simulated_market_prices(search_query: str) -> list[float]:
    q = search_query.lower()
    if "charizard" in q:
        base = [420.0, 450.0, 465.0, 480.0, 495.0, 520.0, 1200.0, 1.0]
    elif "moza" in q and "r3" in q:
        base = [1450.0, 1520.0, 1580.0, 1600.0, 1650.0, 1720.0, 2900.0]
    else:
        base = [250.0, 280.0, 300.0, 315.0, 340.0, 900.0]
    return [round(p * random.uniform(0.97, 1.03), 2) for p in base]


def parse_pasted_listings(raw_text: str) -> ScrapeResult:
    """Extract RM/MYR prices from pasted listing text and filter outliers."""
    raw_prices: list[float] = []
    for match in _PRICE_RE.findall(raw_text):
        val = float(match.replace(",", ""))
        if val > 0:
            raw_prices.append(val)
    if not raw_prices:
        for num in re.findall(r"\b\d{2,6}(?:\.\d{1,2})?\b", raw_text):
            raw_prices.append(float(num))

    filtered = filter_outliers(raw_prices)
    if not filtered:
        raise ValueError("No valid RM prices (>= RM 10) found in pasted text.")

    return ScrapeResult(
        median_price_myr=round(statistics.median(filtered), 2),
        min_price_myr=round(min(filtered), 2),
        max_price_myr=round(max(filtered), 2),
        sample_count=len(filtered),
        source="carousell_pasted",
        filtered_prices=filtered,
        raw_prices=raw_prices,
        summary="Manual pasted listings",
    )


def scrape_carousell_my(
    search_query: str,
    name: str = "",
    condition: str = "Used",
    category: str = "Hardware",
) -> ScrapeResult:
    """Delegate asset market valuation to AI web search (kept as thin compatibility wrapper)."""
    res = lookup_asset_valuation(
        name=name or search_query,
        search_query=search_query,
        condition=condition,
        category=category,
    )
    return ScrapeResult(
        median_price_myr=res.median_price_myr,
        min_price_myr=res.min_price_myr,
        max_price_myr=res.max_price_myr,
        sample_count=res.sample_count,
        source=res.source,
        filtered_prices=res.filtered_prices,
        raw_prices=res.raw_prices,
        summary=res.summary,
        citations=res.citations,
        source_names=res.source_names,
    )
