"""AI web-search asset valuation (Gemini Google Search primary, Groq browser_search fallback)."""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
import httpx

from app.config import settings
from app.modules.settings.ai_usage import record_ai_usage

_GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"
_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_MALAYSIAN_MARKET_DOMAINS = ("carousell.com.my", "mudah.my", "shopee.com.my", "lazada.com.my")

_gemini_search_cooldown_until: float = 0.0
_groq_model_cooldown_until: dict[str, float] = {}

_VALUATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "median_price_myr": {"type": "number"},
        "min_price_myr": {"type": "number"},
        "max_price_myr": {"type": "number"},
        "sample_count": {"type": "integer"},
        "found_prices": {"type": "array", "items": {"type": "number"}},
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "median_price_myr",
        "min_price_myr",
        "max_price_myr",
        "sample_count",
        "summary",
    ],
}


class AIValuationError(RuntimeError):
    """Raised when AI web-search valuation providers are unconfigured or fail."""


@dataclass
class AIValuationResult:
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


def _build_valuation_prompt(name: str, search_query: str, condition: str, category: str) -> str:
    query_hint = search_query.strip() if search_query and search_query.strip() != name.strip() else ""
    hint_line = f"Search hint: {query_hint}\n" if query_hint else ""
    return (
        "Search Malaysian marketplaces (Carousell Malaysia, Mudah.my, Shopee Malaysia, Lazada, local hobby/retail stores) "
        "for the current realistic selling price in MYR of this item:\n"
        f"Item: {name}\n"
        f"Condition: {condition}\n"
        f"Category: {category}\n"
        f"{hint_line}"
        "Rules:\n"
        "- Do ONE web search only and open at most 1-2 results.\n"
        "- Match the specified condition (second-hand resale vs sealed/brand-new).\n"
        "- Exclude RM 1 placeholders, deposits, empty boxes, or unrelated add-on bundles.\n"
        "- Return ONLY a JSON object with keys: "
        "median_price_myr (number), min_price_myr (number), max_price_myr (number), "
        "sample_count (integer), found_prices (array of MYR numbers), "
        "summary (<= 90 chars concise 1-line finding), sources (array of short site names)."
    )


def _extract_json_dict(raw_text: str) -> dict | None:
    cleaned = re.sub(r"<think>.*?</think>", "", str(raw_text or ""), flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
    return None


def _to_positive_float(val) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        num = float(val)
    else:
        cleaned = re.sub(r"[^0-9.]", "", str(val).replace(",", ""))
        if not cleaned:
            return None
        try:
            num = float(cleaned)
        except ValueError:
            return None
    if not math.isfinite(num) or num <= 0:
        return None
    return round(num, 2)


def _sanitize_citations(raw_citations: list[dict], prioritize_malaysia: bool = False) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for item in raw_citations:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        host = urlparse(url).netloc.removeprefix("www.")
        title = re.sub(r"\s+", " ", str(item.get("title") or host)).strip()[:80] or host
        cleaned.append({"title": title, "url": url})

    if prioritize_malaysia:
        cleaned.sort(
            key=lambda c: (0 if any(dom in c["url"].lower() for dom in _MALAYSIAN_MARKET_DOMAINS) else 1)
        )
    return cleaned[:4]


def _normalize_valuation(
    parsed: dict,
    source: str,
    citations: list[dict[str, str]],
    prioritize_malaysia: bool = False,
) -> AIValuationResult:
    from app.modules.assets.scraper import filter_outliers

    raw_prices: list[float] = []
    found_list = parsed.get("found_prices")
    if isinstance(found_list, list):
        for entry in found_list:
            num = _to_positive_float(entry)
            if num is not None and num >= 10.0:
                raw_prices.append(num)

    filtered_prices = filter_outliers(raw_prices) if len(raw_prices) >= 4 else list(raw_prices)

    median_v = _to_positive_float(parsed.get("median_price_myr"))
    min_v = _to_positive_float(parsed.get("min_price_myr"))
    max_v = _to_positive_float(parsed.get("max_price_myr"))

    if median_v is None:
        if filtered_prices:
            median_v = round(statistics.median(filtered_prices), 2)
        elif min_v is not None and max_v is not None:
            median_v = round((min_v + max_v) / 2.0, 2)
        elif min_v is not None:
            median_v = min_v
        elif max_v is not None:
            median_v = max_v
        else:
            raise ValueError("AI valuation response did not include a valid positive MYR price.")

    if min_v is None:
        min_v = min(filtered_prices) if filtered_prices else median_v
    if max_v is None:
        max_v = max(filtered_prices) if filtered_prices else median_v

    low = round(min(min_v, max_v, median_v), 2)
    high = round(max(min_v, max_v, median_v), 2)
    med = round(min(max(median_v, low), high), 2)

    try:
        sample_count = int(parsed.get("sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    if sample_count < 1:
        sample_count = len(filtered_prices) if filtered_prices else 1

    summary = re.sub(r"\s+", " ", str(parsed.get("summary") or "")).strip()[:120]
    raw_sources = parsed.get("sources")
    source_names = (
        [re.sub(r"\s+", " ", str(s)).strip()[:40] for s in raw_sources if str(s).strip()][:4]
        if isinstance(raw_sources, list)
        else []
    )
    clean_citations = _sanitize_citations(citations, prioritize_malaysia=prioritize_malaysia)
    if not source_names and clean_citations:
        hosts = [urlparse(c["url"]).netloc.removeprefix("www.") for c in clean_citations]
        source_names = list(dict.fromkeys(h for h in hosts if h))[:4]

    return AIValuationResult(
        median_price_myr=med,
        min_price_myr=low,
        max_price_myr=high,
        sample_count=sample_count,
        source=source,
        filtered_prices=filtered_prices or [med],
        raw_prices=raw_prices or [low, med, high],
        summary=summary,
        citations=clean_citations,
        source_names=source_names,
    )


def _lookup_via_gemini_search(prompt: str) -> tuple[AIValuationResult | None, str]:
    global _gemini_search_cooldown_until
    if not settings.gemini_api_key:
        return None, "GEMINI_API_KEY not configured"
    if time.monotonic() < _gemini_search_cooldown_until:
        return None, "Gemini Google Search on quota cooldown"

    candidates = list(dict.fromkeys([settings.gemini_search_model, "gemini-3.5-flash-lite"]))
    last_err = "Gemini request failed"
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
        "Content-Type": "application/json",
        "Api-Revision": "2026-05-20",
    }
    for model_id in candidates:
        if not model_id:
            continue
        payload = {
            "model": model_id,
            "input": prompt,
            "tools": [{"type": "google_search"}],
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": _VALUATION_JSON_SCHEMA,
            },
        }
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(_GEMINI_INTERACTIONS_URL, headers=headers, json=payload)
        except Exception as exc:
            last_err = f"Gemini connection error ({type(exc).__name__})"
            record_ai_usage(
                "gemini",
                model_id,
                "asset_search",
                status="error",
                latency_ms=int((time.monotonic() - t0) * 1000),
                error_detail=last_err,
            )
            continue

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 429:
            _gemini_search_cooldown_until = time.monotonic() + 300.0
            record_ai_usage("gemini", model_id, "asset_search", status="rate_limited", latency_ms=elapsed_ms, error_detail="HTTP 429")
            return None, "Gemini Google Search quota exceeded (429)"
        if resp.status_code != 200:
            last_err = f"Gemini HTTP {resp.status_code}"
            record_ai_usage("gemini", model_id, "asset_search", status="error", latency_ms=elapsed_ms, error_detail=last_err)
            continue

        data = resp.json()
        usage = data.get("usage") if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        record_ai_usage(
            "gemini",
            model_id,
            "asset_search",
            status="ok",
            input_tokens=int(usage.get("total_input_tokens") or 0),
            output_tokens=int(usage.get("total_output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=elapsed_ms,
        )
        text_parts: list[str] = []
        citations: list[dict[str, str]] = []
        for step in data.get("steps") or []:
            if step.get("type") != "model_output":
                continue
            for block in step.get("content") or []:
                if block.get("type") != "text":
                    continue
                if block.get("text"):
                    text_parts.append(str(block["text"]))
                for ann in block.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        citations.append(
                            {
                                "title": str(ann.get("title") or "").strip(),
                                "url": str(ann["url"]).strip(),
                            }
                        )

        raw_text = "\n".join(text_parts).strip() or str(data.get("output_text") or "").strip()
        parsed = _extract_json_dict(raw_text)
        if not parsed:
            last_err = f"Gemini ({model_id}) returned non-JSON output"
            continue
        try:
            return _normalize_valuation(parsed, f"ai:{model_id}", citations), ""
        except ValueError as exc:
            last_err = str(exc)
            continue

    return None, last_err


def _lookup_via_groq_search(prompt: str) -> tuple[AIValuationResult | None, str]:
    if not settings.groq_api_key:
        return None, "GROQ_API_KEY not configured"

    candidates = list(
        dict.fromkeys(
            m.strip()
            for m in (settings.groq_search_model, "openai/gpt-oss-120b", "openai/gpt-oss-20b")
            if m and m.strip()
        )
    )
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    now = time.monotonic()
    active_candidates = [m for m in candidates if now >= _groq_model_cooldown_until.get(m, 0.0)]
    if not active_candidates:
        active_candidates = candidates

    last_err = "Groq search failed"
    short_retry_wait: tuple[str, float] | None = None

    for model_id in active_candidates:
        res, err, retry_secs, is_daily_limit = _call_groq_search_once(model_id, headers, prompt)
        if res is not None:
            return res, ""
        last_err = err
        if retry_secs is not None:
            if is_daily_limit:
                _groq_model_cooldown_until[model_id] = time.monotonic() + min(max(retry_secs, 60.0), 600.0)
            elif retry_secs <= 65.0 and short_retry_wait is None:
                short_retry_wait = (model_id, retry_secs)
            else:
                _groq_model_cooldown_until[model_id] = time.monotonic() + min(max(retry_secs, 15.0), 120.0)

    if short_retry_wait is not None:
        retry_model, wait_secs = short_retry_wait
        time.sleep(min(wait_secs + 1.0, 66.0))
        res, err, _, _ = _call_groq_search_once(retry_model, headers, prompt)
        if res is not None:
            return res, ""
        last_err = err

    return None, last_err


def _call_groq_search_once(
    model_id: str,
    headers: dict[str, str],
    prompt: str,
) -> tuple[AIValuationResult | None, str, float | None, bool]:
    payload = {
        "model": model_id,
        "reasoning_effort": "low",
        "max_completion_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "browser_search"}],
    }
    resp = None
    t0 = time.monotonic()
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(_GROQ_CHAT_URL, headers=headers, json=payload)
        except Exception as exc:
            if attempt == 1:
                err_msg = f"Groq search connection error ({type(exc).__name__})"
                record_ai_usage("groq", model_id, "asset_search", status="error", latency_ms=int((time.monotonic() - t0) * 1000), error_detail=err_msg)
                return None, err_msg, None, False
            continue

        if resp.status_code == 400 and attempt == 0:
            # Groq browser_search occasionally raises transient 400 tool_use_failed; one retry resolves it.
            continue
        break

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp is None:
        record_ai_usage("groq", model_id, "asset_search", status="error", latency_ms=elapsed_ms, error_detail="No response")
        return None, "Groq search failed", None, False

    if resp.status_code == 429:
        retry_hdr = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
        try:
            retry_secs = float(retry_hdr) if retry_hdr else 10.0
        except (TypeError, ValueError):
            retry_secs = 10.0
        body_lower = (resp.text or "").lower() if hasattr(resp, "text") else ""
        is_daily = "tokens per day" in body_lower or "tpd" in body_lower
        record_ai_usage(
            "groq",
            model_id,
            "asset_search",
            status="rate_limited",
            latency_ms=elapsed_ms,
            error_detail="HTTP 429 (TPD)" if is_daily else "HTTP 429 (TPM)",
        )
        return None, f"Groq search HTTP 429 ({model_id.split('/')[-1]})", retry_secs, is_daily

    if resp.status_code != 200:
        err_msg = f"Groq search HTTP {resp.status_code}"
        record_ai_usage("groq", model_id, "asset_search", status="error", latency_ms=elapsed_ms, error_detail=err_msg)
        return None, err_msg, None, False

    try:
        resp_json = resp.json()
        msg = resp_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError, ValueError):
        record_ai_usage("groq", model_id, "asset_search", status="error", latency_ms=elapsed_ms, error_detail="Malformed payload")
        return None, "Groq search returned malformed payload", None, False

    usage = resp_json.get("usage") if isinstance(resp_json, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    record_ai_usage(
        "groq",
        model_id,
        "asset_search",
        status="ok",
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        latency_ms=elapsed_ms,
    )

    parsed = _extract_json_dict(msg.get("content", ""))
    if not parsed:
        return None, "Groq search returned non-JSON output", None, False

    raw_citations: list[dict[str, str]] = []
    for tool in msg.get("executed_tools") or []:
        if not isinstance(tool, dict):
            continue
        sr = tool.get("search_results")
        if not isinstance(sr, dict):
            continue
        for item in sr.get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                raw_citations.append(
                    {
                        "title": str(item.get("title") or "").strip(),
                        "url": str(item["url"]).strip(),
                    }
                )

    short_model = model_id.split("/")[-1][:24]
    try:
        return (
            _normalize_valuation(
                parsed,
                f"ai:{short_model}",
                raw_citations,
                prioritize_malaysia=True,
            ),
            "",
            None,
            False,
        )
    except ValueError as exc:
        return None, str(exc), None, False


def lookup_asset_valuation(
    name: str,
    search_query: str = "",
    condition: str = "Used",
    category: str = "Hardware",
) -> AIValuationResult:
    """Query AI web-search models for current Malaysian market prices (MYR)."""
    if settings.carousell_mode.lower() == "demo":
        from app.modules.assets.scraper import _simulated_market_prices, filter_outliers

        raw_prices = _simulated_market_prices(search_query or name)
        filtered = filter_outliers(raw_prices) or [0.0]
        return AIValuationResult(
            median_price_myr=round(statistics.median(filtered), 2),
            min_price_myr=round(min(filtered), 2),
            max_price_myr=round(max(filtered), 2),
            sample_count=len(filtered),
            source="ai_demo",
            filtered_prices=filtered,
            raw_prices=raw_prices,
            summary="Simulated market estimate (demo mode)",
            citations=[],
            source_names=["Demo"],
        )

    prompt = _build_valuation_prompt(name, search_query, condition, category)
    errors: list[str] = []

    res, err = _lookup_via_gemini_search(prompt)
    if res is not None:
        return res
    if err:
        errors.append(err)

    res, err = _lookup_via_groq_search(prompt)
    if res is not None:
        return res
    if err:
        errors.append(err)

    raise AIValuationError(
        "AI price search failed: " + ("; ".join(errors) if errors else "no search-enabled AI key configured")
    )
