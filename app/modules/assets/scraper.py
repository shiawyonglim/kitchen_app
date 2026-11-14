from __future__ import annotations

import json
import random
import re
import statistics
import time
from dataclasses import dataclass
from urllib.parse import quote
import httpx
from bs4 import BeautifulSoup

from app.config import settings

_PRICE_RE = re.compile(r"RM\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-MY,en;q=0.9",
}


class CarousellBlockedError(RuntimeError):
    """Raised when Carousell returns 403/anti-bot protection during live scraping."""


@dataclass
class ScrapeResult:
    median_price_myr: float
    min_price_myr: float
    max_price_myr: float
    sample_count: int
    source: str
    filtered_prices: list[float]
    raw_prices: list[float]


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


def _extract_prices_from_html(html: str) -> list[float]:
    soup = BeautifulSoup(html, "html.parser")
    prices: list[float] = []

    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        try:
            payload_text = json.dumps(json.loads(next_data.string))
            for match in _PRICE_RE.findall(payload_text):
                val = float(match.replace(",", ""))
                if val >= 10:
                    prices.append(val)
        except Exception:
            pass

    if not prices:
        text = soup.get_text(" ", strip=True)
        for match in _PRICE_RE.findall(text):
            val = float(match.replace(",", ""))
            if val >= 10:
                prices.append(val)

    return prices[:40]


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
    """Extract RM/MYR prices from pasted Carousell search text and filter outliers."""
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
    )


def scrape_carousell_my(search_query: str) -> ScrapeResult:
    """Scrape Carousell Malaysia with exponential backoff; raises if blocked in live mode."""
    raw_prices: list[float] = []
    source = "carousell_live"
    last_status: int | None = None

    if settings.carousell_mode.lower() == "live":
        url = f"https://www.carousell.com.my/search/{quote(search_query)}"
        for attempt in range(2):
            try:
                with httpx.Client(headers=_HEADERS, timeout=6.0, follow_redirects=True) as client:
                    resp = client.get(url)
                    last_status = resp.status_code
                    if resp.status_code == 200:
                        extracted = _extract_prices_from_html(resp.text)
                        if extracted:
                            raw_prices = extracted
                            break
                    elif resp.status_code in (403, 429):
                        break
            except Exception:
                pass
            time.sleep(0.4 * (2**attempt))

        if not raw_prices:
            raise CarousellBlockedError(
                f"Carousell returned HTTP {last_status or 'timeout'} (anti-bot protection). "
                "Use 'Paste Listings' on the asset card to compute an outlier-filtered median from real prices."
            )
    else:
        raw_prices = _simulated_market_prices(search_query)
        source = "carousell_demo"

    filtered = filter_outliers(raw_prices)
    if not filtered:
        filtered = [0.0]

    return ScrapeResult(
        median_price_myr=round(statistics.median(filtered), 2),
        min_price_myr=round(min(filtered), 2),
        max_price_myr=round(max(filtered), 2),
        sample_count=len(filtered),
        source=source,
        filtered_prices=filtered,
        raw_prices=raw_prices,
    )

