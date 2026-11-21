"""Free LLM planner (Groq primary, local Ollama fallback, deterministic heuristic backup)."""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta
import httpx

from app.config import settings
from app.modules.settings.ai_usage import record_ai_usage

_VALID_CATEGORIES = {
    "planning",
    "schedule",
    "task",
    "pay",
    "bank_statement",
    "question",
    "idea",
    "kitchen",
    "expense",
}
_VALID_PRIORITIES = {"high", "normal", "low"}


def _format_plan_text(val) -> str:
    if isinstance(val, list):
        return "\n".join(f"• {str(x).strip()}" for x in val if str(x).strip())
    return str(val or "").strip()


_GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"


def _call_gemini_interaction(
    model_candidates: list[str],
    input_payload,
    service_name: str,
    system_instruction: str = "",
    json_output: bool = False,
    timeout: float = 22.0,
) -> tuple[str, str]:
    """Call Gemini Interactions API (v1) and record usage telemetry."""
    if not settings.gemini_api_key:
        return "", ""
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
        "Content-Type": "application/json",
        "Api-Revision": "2026-05-20",
    }
    for model_id in model_candidates:
        payload: dict = {"model": model_id, "input": input_payload}
        if system_instruction:
            payload["system_instruction"] = system_instruction
        if json_output:
            payload["response_format"] = {"type": "text", "mime_type": "application/json"}
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(_GEMINI_INTERACTIONS_URL, headers=headers, json=payload)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                rjson = resp.json()
                usage = rjson.get("usage") or {}
                record_ai_usage(
                    "gemini",
                    model_id,
                    service_name,
                    status="ok",
                    input_tokens=int(usage.get("total_input_tokens") or 0),
                    output_tokens=int(usage.get("total_output_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                    latency_ms=elapsed_ms,
                )
                parts: list[str] = []
                for step in rjson.get("steps") or []:
                    if step.get("type") == "model_output":
                        for block in step.get("content") or []:
                            if block.get("type") == "text" and block.get("text"):
                                parts.append(str(block["text"]))
                text = "\n".join(parts).strip() or str(rjson.get("output_text") or "").strip()
                if text:
                    return text, f"gemini:{model_id}"
            else:
                record_ai_usage(
                    "gemini",
                    model_id,
                    service_name,
                    status="rate_limited" if resp.status_code == 429 else "error",
                    latency_ms=elapsed_ms,
                    error_detail=f"HTTP {resp.status_code}",
                )
        except Exception as exc:
            record_ai_usage(
                "gemini",
                model_id,
                service_name,
                status="error",
                latency_ms=int((time.monotonic() - t0) * 1000),
                error_detail=type(exc).__name__,
            )
    return "", ""


def extract_image_context(image_base64: str, image_mime: str = "image/jpeg") -> str:
    """Read a forwarded image/receipt using Gemini Interactions (free tier) or local RapidOCR fallback."""
    if not image_base64:
        return ""
    if settings.gemini_api_key:
        text, _ = _call_gemini_interaction(
            ["gemini-3.5-flash-lite", "gemini-3.8-flash"],
            [
                {
                    "type": "text",
                    "text": (
                        "Extract all key details from this image concisely in plain text: "
                        "if it is a receipt, list merchant/type, date, key items, and exact Net Total / Grand Total; "
                        "if it is a screenshot/poster/chat, summarize all dates, times, venues, amounts, and names."
                    ),
                },
                {
                    "type": "image",
                    "data": image_base64,
                    "mime_type": image_mime or "image/jpeg",
                },
            ],
            service_name="assistant_vision",
            timeout=20.0,
        )
        if text:
            return text

    try:
        from app.modules.kitchen import ocr

        lines = ocr.extract_receipt_lines(base64.b64decode(image_base64))
        if lines:
            return " | ".join(lines[:20])
    except Exception:
        pass
    return "Image attached (could not extract text)."


def _parse_ocr_receipt_lines(lines: list[str]) -> dict | None:
    if not lines:
        return None
    merchant = ""
    total_myr = 0.0
    items: list[dict] = []
    food_kw = (
        "chicken", "beef", "pork", "fish", "salmon", "egg", "milk", "bread", "rice",
        "noodle", "tomato", "onion", "garlic", "chili", "vege", "kangkung", "lettuce",
        "cheese", "butter", "yogurt", "fruit", "apple", "banana", "oil", "sauce",
        "flour", "tofu", "mushroom", "sausage", "shrimp", "prawn", "pastry", "coffee",
    )
    skip_kw = ("total", "subtotal", "tax", "sst", "gst", "cash", "change", "rounding", "discount", "visa", "master", "duitnow", "qty", "thank you")
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if not merchant and len(s) >= 3 and not re.match(r"^[\d\W]+$", s):
            merchant = s[:80]
        low = s.lower()
        tot_m = re.search(r"(?:grand\s*total|net\s*total|total)\s*[:rm\s]*([\d,]+\.\d{2})", low)
        if tot_m:
            total_myr = max(total_myr, float(tot_m.group(1).replace(",", "")))
            continue
        item_m = re.match(r"^(.+?)\s+(?:rm\s*)?(\d+(?:\.\d{2}))\s*$", s, re.IGNORECASE)
        if item_m and not any(k in low for k in skip_kw):
            name = item_m.group(1).strip(" .-*")
            price = float(item_m.group(2))
            if len(name) >= 2 and price > 0:
                is_food = any(fk in name.lower() for fk in food_kw)
                items.append({
                    "name": name[:80],
                    "qty": 1.0,
                    "unit": "pcs",
                    "price_myr": price,
                    "is_food": is_food,
                    "days_until_expiry": 5 if is_food else 0,
                })
    if total_myr <= 0 and items:
        total_myr = round(sum(it["price_myr"] for it in items), 2)
    if total_myr <= 0 and not items:
        return None
    return {
        "is_receipt": True,
        "merchant": merchant or "Receipt Purchase",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_myr": round(total_myr, 2),
        "account_name": "Other / Cash",
        "items": items,
    }


def extract_receipt_details(image_base64: str, image_mime: str = "image/jpeg") -> dict | None:
    """Extract structured merchant, total, and itemized purchases from a forwarded receipt image."""
    if not image_base64:
        return None
    if settings.gemini_api_key:
        prompt = (
            "Analyze this image. If it is a store/grocery/restaurant/shopping receipt, extract ALL purchased line items.\n"
            "Return ONLY JSON:\n"
            "{\"is_receipt\": bool, \"merchant\": str, \"date\": \"YYYY-MM-DD\", \"total_myr\": float, "
            "\"account_name\": \"Other / Cash\", "
            "\"items\": [{\"name\": str, \"qty\": float, \"unit\": str, \"price_myr\": float, "
            "\"is_food\": bool, \"days_until_expiry\": int}]}\n"
            "Set is_food=true and sensible days_until_expiry (e.g. 3-7 for fresh meat/veg/milk, 30+ for pantry staples) "
            "for edible groceries/ingredients; set is_food=false and days_until_expiry=0 for non-food items."
        )
        text, _ = _call_gemini_interaction(
            ["gemini-3.5-flash-lite", "gemini-3.8-flash"],
            [
                {"type": "text", "text": prompt},
                {"type": "image", "data": image_base64, "mime_type": image_mime or "image/jpeg"},
            ],
            service_name="assistant_vision",
            json_output=True,
            timeout=22.0,
        )
        if text:
            parsed = _extract_json_dict(text)
            if parsed and parsed.get("is_receipt"):
                return parsed

    try:
        from app.modules.kitchen import ocr
        lines = ocr.extract_receipt_lines(base64.b64decode(image_base64))
        return _parse_ocr_receipt_lines(lines)
    except Exception:
        return None


def _extract_json_dict(raw_text: str) -> dict | None:
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
    return None


_MODEL_COOLDOWN_UNTIL: dict[tuple[str, str], float] = {}


def _call_openai_compat_json(
    base_url: str,
    api_key: str,
    model_candidates: list[str],
    system_prompt: str,
    user_prompt: str,
    provider_tag: str,
    timeout: float = 18.0,
    service_name: str = "assistant_plan",
) -> tuple[dict | None, str]:
    if not api_key:
        return None, ""
    now_mono = time.monotonic()
    for model_id in dict.fromkeys(m for m in model_candidates if m):
        cooldown_until = _MODEL_COOLDOWN_UNTIL.get((provider_tag, model_id), 0.0)
        if now_mono < cooldown_until:
            continue
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                rjson = resp.json()
                usage = rjson.get("usage") or {}
                record_ai_usage(
                    provider_tag,
                    model_id,
                    service_name,
                    status="ok",
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                    latency_ms=elapsed_ms,
                )
                content = rjson["choices"][0]["message"]["content"]
                parsed = _extract_json_dict(content)
                if parsed is not None:
                    short_name = model_id.split("/")[-1][:24]
                    return parsed, f"{provider_tag}:{short_name}"
            elif resp.status_code == 429:
                record_ai_usage(provider_tag, model_id, service_name, status="rate_limited", latency_ms=elapsed_ms, error_detail="HTTP 429")
                retry_hdr = resp.headers.get("retry-after", "")
                try:
                    wait_sec = max(30.0, min(600.0, float(retry_hdr)))
                except (TypeError, ValueError):
                    wait_sec = 120.0
                _MODEL_COOLDOWN_UNTIL[(provider_tag, model_id)] = now_mono + wait_sec
            elif resp.status_code in (401, 403):
                record_ai_usage(provider_tag, model_id, service_name, status="error", latency_ms=elapsed_ms, error_detail=f"HTTP {resp.status_code}")
                return None, ""
            else:
                record_ai_usage(provider_tag, model_id, service_name, status="error", latency_ms=elapsed_ms, error_detail=f"HTTP {resp.status_code}")
        except Exception as exc:
            record_ai_usage(provider_tag, model_id, service_name, status="error", latency_ms=int((time.monotonic() - t0) * 1000), error_detail=type(exc).__name__)
            continue
    return None, ""


def _call_gemini_json(system_prompt: str, user_prompt: str, service_name: str = "assistant_plan") -> tuple[dict | None, str]:
    text, eng = _call_gemini_interaction(
        ["gemini-3.5-flash-lite", "gemini-4-hydrogen", "gemini-3.8-flash"],
        user_prompt,
        service_name=service_name,
        system_instruction=system_prompt,
        json_output=True,
        timeout=20.0,
    )
    if text:
        parsed = _extract_json_dict(text)
        if parsed is not None:
            return parsed, eng
    return None, ""


def _call_groq_json(
    system_prompt: str,
    user_prompt: str,
    models: list[str] | None = None,
    service_name: str = "assistant_plan",
) -> tuple[dict | None, str]:
    candidates = models or [settings.groq_model, "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    return _call_openai_compat_json(
        "https://api.groq.com/openai/v1/chat/completions",
        settings.groq_api_key,
        candidates,
        system_prompt,
        user_prompt,
        "groq",
        service_name=service_name,
    )


def _call_nim_json(
    system_prompt: str,
    user_prompt: str,
    models: list[str] | None = None,
    service_name: str = "assistant_plan",
) -> tuple[dict | None, str]:
    candidates = models or [
        "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "mistralai/mistral-large-4-1t-a52b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3.5-bolt-e4b",
    ]
    return _call_openai_compat_json(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        settings.nvidia_api_key,
        candidates,
        system_prompt,
        user_prompt,
        "nim",
        service_name=service_name,
    )


def _detect_signals(raw_message: str) -> dict:
    lower = raw_message.lower()
    math_scan = re.sub(r"\b\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b", " ", lower)
    math_scan = re.sub(
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        " ",
        math_scan,
    )
    is_event = any(
        k in lower
        for k in ("court", "shuttle", "打球", "🏸", "dinner", "meeting", "appointment", "call")
    )
    is_math_or_hard = (
        bool(re.search(r"\d+\s*[\+\*\^=]\s*-?\d+", math_scan))
        or bool(re.search(r"\b(?:what is|calculate|solve|evaluate)\s+[-+*/\d\sx()=^]+", math_scan))
        or bool(re.search(r"\d*\s*[a-z]\s*[\+\-\*\/]\s*\d+\s*=\s*-?\d+", math_scan))
        or any(
            k in lower
            for k in (
                "solve",
                "equation",
                "integral",
                "derivative",
                "probability",
                "prove",
                "calculate",
                "algorithm",
                "debug",
                "compare",
                "optimize",
            )
        )
    )
    is_cook_craving = (not is_event) and (not lower.startswith("buy ")) and any(
        k in lower
        for k in (
            "cook",
            "eat ",
            "to eat",
            "hungry",
            "craving",
            "crispy",
            "chrispy",
            "crunchy",
            "recipe",
            "what's in my fridge",
            "whats in my fridge",
            "kitchen matrix",
        )
    )
    is_asset_or_debt = any(
        k in lower
        for k in (
            "loan",
            "mortgage",
            "borrowed",
            "lent ",
            "bought a ",
            "bought an ",
            "purchased ",
            "add asset",
            "add debt",
            "installment",
        )
    )
    is_question = (
        "?" in raw_message
        or is_cook_craving
        or bool(re.match(r"^\s*(who|what|where|when|why|how|which|can|could|should|is|are|do|does|did)\b", lower))
        or any(
            k in lower
            for k in (
                "what does",
                "what do",
                "what is",
                "who is",
                "how is",
                "mean",
                "meaning",
                "explain",
                "why ",
                "how to",
                "how do",
                "should i reply",
                "translate",
            )
        )
        or is_math_or_hard
    )
    is_bank_stmt = any(
        k in lower
        for k in (
            "bank statement",
            "statement of account",
            "ewallet statement",
            "transaction history",
        )
    )
    is_pay = (not is_event) and any(
        k in lower for k in ("pay ", "transfer to", "owe ", "settle ", "bill due")
    )
    is_planning = any(
        k in lower for k in ("plan ", "itinerary", "roadmap", "trip to", "strategy", "organize")
    )
    return {
        "is_event": is_event,
        "is_math_or_hard": is_math_or_hard,
        "is_question": is_question,
        "is_cook_craving": is_cook_craving,
        "is_asset_or_debt": is_asset_or_debt,
        "is_bank_stmt": is_bank_stmt,
        "is_pay": is_pay,
        "is_planning": is_planning,
    }


def _classify_intent_and_tier(
    raw_message: str,
    has_image: bool = False,
    has_document: bool = False,
) -> tuple[str, str]:
    """Stage 1 fast triage: classify intent category and complexity cost_tier."""
    if has_document:
        return "bank_statement", "low_cost"

    sig = _detect_signals(raw_message)
    has_multi = "\n---\n" in raw_message
    has_non_ascii = any(ord(c) > 127 for c in raw_message)
    min_high = (
        has_image
        or "[Attached Image/Receipt]:" in raw_message
        or len(raw_message) > 450
        or sig["is_math_or_hard"]
    )

    triage_sys = (
        "Classify this personal assistant message into ONE intent and ONE cost_tier.\n"
        "Intents: 'planning' (trips, roadmaps, multi-step plans), 'schedule' (dated events/appointments), "
        "'pay' (payments/bills/transfers owed), 'bank_statement' (bank/e-wallet statement text), "
        "'question' (asking what a message means/how to reply, math problems, hard reasoning, explanations), "
        "'kitchen' (groceries/food), 'idea', 'expense' (paid receipts), 'task' (other to-dos).\n"
        "cost_tier: 'low_cost' (simple 1-line reminder/grocery/errand), "
        "'medium_cost' (event with date/time/venue, bilingual text, message interpretation, or planning), "
        "'high_cost' (math/hard problems, receipts/images, complex multi-message reasoning).\n"
        "Return ONLY JSON: {\"intent\": str, \"cost_tier\": \"low_cost\"|\"medium_cost\"|\"high_cost\"}"
    )
    parsed = None
    if settings.groq_api_key:
        parsed, _ = _call_groq_json(
            triage_sys,
            raw_message[:400],
            models=["openai/gpt-oss-20b", "llama-3.1-8b-instant"],
            service_name="assistant_triage",
        )
    if not parsed and settings.nvidia_api_key:
        parsed, _ = _call_nim_json(
            triage_sys,
            raw_message[:400],
            models=["nvidia/nemotron-3.5-bolt-e4b"],
            service_name="assistant_triage",
        )

    if parsed:
        intent = str(parsed.get("intent", "")).strip().lower()
        tier = str(parsed.get("cost_tier", "")).strip().lower()
        if intent in _VALID_CATEGORIES and tier in ("low_cost", "medium_cost", "high_cost"):
            if min_high:
                tier = "high_cost"
            elif intent == "question" and tier == "low_cost":
                tier = "medium_cost"
            return intent, tier

    # Deterministic offline fallback
    if sig["is_bank_stmt"]:
        return "bank_statement", "high_cost" if min_high else "low_cost"
    if sig["is_question"]:
        return "question", "high_cost" if min_high else "medium_cost"
    if sig["is_pay"]:
        return "pay", "high_cost" if min_high else "low_cost"
    if sig["is_planning"]:
        return "planning", "high_cost" if min_high else "medium_cost"
    fallback_intent = "schedule" if sig["is_event"] else "task"
    if min_high:
        return fallback_intent, "high_cost"
    if has_multi or has_non_ascii or len(raw_message) > 120:
        return fallback_intent, "medium_cost"
    return fallback_intent, "low_cost"


def _call_ollama_json(prompt: str, service_name: str = "assistant_plan") -> dict | None:
    try:
        with httpx.Client(timeout=0.8) as probe:
            tags = probe.get("http://127.0.0.1:11434/api/tags")
            models = tags.json().get("models", []) if tags.status_code == 200 else []
            model_name = models[0]["name"] if models else None
        if not model_name:
            return None
        t0 = time.monotonic()
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": model_name, "prompt": prompt, "format": "json", "stream": False},
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200 and resp.json().get("response"):
            rjson = resp.json()
            record_ai_usage(
                "ollama",
                model_name,
                service_name,
                status="ok",
                input_tokens=int(rjson.get("prompt_eval_count") or 0),
                output_tokens=int(rjson.get("eval_count") or 0),
                latency_ms=elapsed_ms,
            )
            return _extract_json_dict(rjson["response"])
    except Exception:
        return None
    return None


def _next_weekday(now: datetime, target_wd: int) -> datetime:
    days_ahead = (target_wd - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return now + timedelta(days=days_ahead)


def _heuristic_kitchen_suggestion(text: str, app_context: dict | None = None) -> str:
    lower = text.lower()
    km = (app_context or {}).get("kitchen_matrix") or {}
    inv = km.get("items") or []
    rules = km.get("taste_rules") or []
    profiles = km.get("profiles") or []
    if not inv:
        return (
            "• Your Fridge Inventory is currently empty.\n"
            "• Add ingredients on the Kitchen page (or send a grocery receipt) so I can match dishes from your Kitchen Matrix."
        )
    names = [str(i.get("name", "")).strip() for i in inv if i.get("name")]
    expiring = [str(i.get("name", "")).strip() for i in inv if i.get("expiring") and i.get("name")]
    wants_crispy = any(k in lower for k in ("crispy", "chrispy", "crunchy", "fried"))
    lines = [f"• Available in Fridge ({len(names)}): {', '.join(names[:10])}"]
    if expiring:
        lines.append(f"• Priority expiring soon: {', '.join(expiring[:5])}")
    if wants_crispy:
        lines.append(
            f"• Crispy Idea: Pan-sear or air-fry {expiring[0] if expiring else names[0]} at high heat with a light starch/oil crust until golden and crispy."
        )
    else:
        lines.append(
            f"• Dish Idea: High-heat stir-fry or roast using {', '.join((expiring or names)[:3])}."
        )
    if profiles:
        p_str = "; ".join(f"{p.get('name')} ({p.get('method') or p.get('texture') or 'balanced'})" for p in profiles[:4])
        lines.append(f"• Kitchen Matrix profiles: {p_str}")
    if rules:
        lines.append(f"• Taste Rule: {rules[0]}")
    return "\n".join(lines)


def _heuristic_app_action(text: str) -> tuple[dict | None, str]:
    lower = text.lower()
    rm_m = re.search(r"rm\s*([\d,]+(?:\.\d{1,2})?)", lower)
    amount = float(rm_m.group(1).replace(",", "")) if rm_m else 0.0

    # 1. Loan / Debt detection
    if amount > 0 and any(k in lower for k in ("loan", "mortgage", "borrowed", "lent ", "owe ")):
        rate_m = re.search(r"(\d+(?:\.\d+)?)\s*%", lower)
        rate_pct = float(rate_m.group(1)) if rate_m else 0.0
        tenure_months = None
        yr_m = re.search(r"(\d+)\s*(?:years?|yrs?)\b", lower)
        mo_m = re.search(r"(\d+)\s*(?:months?|mos?)\b", lower)
        if yr_m:
            tenure_months = int(yr_m.group(1)) * 12
        elif mo_m:
            tenure_months = int(mo_m.group(1))
        interest_type = "flat" if "flat" in lower else ("reducing" if "reducing" in lower else "none")
        direction = "owed_to_me" if any(k in lower for k in ("owed to me", "lent ", "borrowed from me")) else "i_owe"
        party_m = re.search(r"\b(?:from|to|with)\s+([A-Za-z][A-Za-z0-9\s]{1,30}?)(?:\s+(?:at|for|rm|\d)|\s*$)", text, re.IGNORECASE)
        borrower = party_m.group(1).strip() if party_m else "Bank"
        event_name = (
            "Car Loan" if "car" in lower
            else "House Loan" if any(k in lower for k in ("house", "home", "mortgage"))
            else "Personal Loan"
        )
        return (
            {
                "type": "create_debt",
                "event_name": event_name,
                "borrower_name": borrower,
                "amount_myr": amount,
                "direction": direction,
                "interest_rate_pct": rate_pct,
                "interest_type": interest_type,
                "tenure_months": tenure_months,
            },
            f"• Logged {event_name} ({borrower}) for RM{amount:,.2f} into Debt Tracker.",
        )

    # 2. Asset purchase detection
    if amount > 0 and any(k in lower for k in ("bought a ", "bought an ", "purchased ", "add asset")):
        name_m = re.search(
            r"(?:bought an?|purchased|add asset[:\s]*)\s+([A-Za-z0-9][A-Za-z0-9\s\-]{1,45}?)(?:\s+(?:for|at|rm)|\s*$)",
            text,
            re.IGNORECASE,
        )
        name = name_m.group(1).strip(" .,-") if name_m else "New Asset"
        if any(k in lower for k in ("car", "civic", "vios", "myvi", "altis", "city", "motor", "bike", "vehicle")):
            cat = "Vehicle"
        elif any(k in lower for k in ("house", "condo", "apartment", "property", "land")):
            cat = "Property"
        elif any(k in lower for k in ("macbook", "laptop", "iphone", "phone", "camera", "ipad", "gpu", "pc")):
            cat = "Electronics"
        else:
            cat = "Hardware"
        cond = "Used" if "used" in lower or "secondhand" in lower else "New"
        return (
            {
                "type": "create_asset",
                "name": name,
                "search_query": f"{name} Malaysia price",
                "category": cat,
                "condition": cond,
                "purchase_price_myr": amount,
                "quantity": 1,
            },
            f"• Added {name} ({cat}, {cond}) at RM{amount:,.2f} to Assets.",
        )
    return None, ""


def _heuristic_single(text: str, app_context: dict | None = None) -> dict:
    lower = text.lower()
    now = datetime.now()
    scheduled_for = ""

    # Time range like "8-10pm" or single time "8pm" / "8:30 pm"
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?(?:\s*-\s*\d{1,2}(?::\d{2})?)?\s*(am|pm)\b", lower)
    hhmm = ""
    if time_match:
        hr = int(time_match.group(1))
        mn = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and hr < 12:
            hr += 12
        elif ampm == "am" and hr == 12:
            hr = 0
        hhmm = f"{hr:02d}:{mn:02d}"

    # Explicit date dd/mm/yy or dd/mm/yyyy
    date_m = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b", lower)
    if date_m:
        day, mon, yr = int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3))
        if yr < 100:
            yr += 2000
        try:
            scheduled_for = f"{datetime(yr, mon, day).strftime('%Y-%m-%d')} {hhmm}".strip()
        except ValueError:
            pass
    elif "tomorrow" in lower or "tmr" in lower:
        scheduled_for = f"{(now + timedelta(days=1)).strftime('%Y-%m-%d')} {hhmm}".strip()
    elif "today" in lower or "tonight" in lower:
        scheduled_for = f"{now.strftime('%Y-%m-%d')} {hhmm}".strip()
    else:
        weekdays = {
            "monday": 0, "星期一": 0,
            "tuesday": 1, "星期二": 1,
            "wednesday": 2, "星期三": 2,
            "thursday": 3, "星期四": 3,
            "friday": 4, "星期五": 4,
            "saturday": 5, "星期六": 5,
            "sunday": 6, "星期日": 6, "星期天": 6,
        }
        for kw, wd in weekdays.items():
            if kw in lower:
                scheduled_for = f"{_next_weekday(now, wd).strftime('%Y-%m-%d')} {hhmm}".strip()
                break
        if not scheduled_for and hhmm:
            scheduled_for = f"{now.strftime('%Y-%m-%d')} {hhmm}"

    kitchen_items: list[str] = []
    app_action: dict | None = None
    sig = _detect_signals(text)
    is_event = sig["is_event"]
    detected_action, action_plan_text = _heuristic_app_action(text)
    if detected_action:
        app_action = detected_action
        category = "pay" if detected_action["type"] == "create_debt" else "expense"
        ai_plan = action_plan_text
    elif sig["is_cook_craving"]:
        category = "question"
        ai_plan = _heuristic_kitchen_suggestion(text, app_context=app_context)
    elif any(k in lower for k in ("buy ", "grocer", "tomato", "chicken", "egg", "milk", "kangkung", "chili")) or ("fridge" in lower and not sig["is_question"]):
        category = "kitchen"
        buy_m = re.search(r"buy\s+(.+)", text, re.IGNORECASE)
        if buy_m:
            raw_list = re.split(r",|\band\b", buy_m.group(1))
            kitchen_items = [x.strip(" .!?") for x in raw_list if 1 < len(x.strip(" .!?")) < 60][:5]
        ai_plan = "1. Added groceries to your Kitchen Shopping List.\n2. Check Fridge Countdown before heading out."
    elif sig["is_bank_stmt"]:
        category = "bank_statement"
        ai_plan = "• Bank statement detected. Upload or forward the PDF/CSV file to auto-import into Wealth Ledger."
    elif sig["is_question"] and not (scheduled_for and is_event):
        category = "question"
        lin_m = re.search(r"(-?\d+)\s*\*?\s*x\s*([+\-])\s*(\d+)\s*=\s*(-?\d+)", lower)
        if lin_m:
            a, op, b, c = int(lin_m.group(1)), lin_m.group(2), int(lin_m.group(3)), int(lin_m.group(4))
            b_signed = b if op == "+" else -b
            rhs = c - b_signed
            x_val = rhs / a if a != 0 else 0
            x_str = str(int(x_val)) if float(x_val).is_integer() else f"{x_val:.4g}"
            ai_plan = f"• {a}x = {c} {'-' if b_signed >= 0 else '+'} {abs(b_signed)} = {rhs}\n• x = {rhs} / {a} = {x_str}"
        else:
            ai_plan = "• Question received and logged for detailed AI interpretation / answer."
    elif sig["is_pay"]:
        category = "pay"
        ai_plan = "• Verify recipient and amount.\n• Complete transfer and mark Done once paid."
    elif not is_event and any(k in lower for k in ("paid ", "spend", "receipt", "transfer", "invoice")):
        category = "expense"
        ai_plan = "1. Verify amount and category in Wealth Ledger.\n2. Attach receipt or mark if claimable."
    elif scheduled_for or is_event or any(k in lower for k in ("remind", "schedule")):
        category = "schedule"
        ai_plan = f"1. Confirmed time slot: {scheduled_for or 'TBD'}.\n2. Prepare gear/items ahead of time."
    elif sig["is_planning"]:
        category = "planning"
        ai_plan = "• Break the plan into key milestones.\n• Set target dates for each phase."
    elif any(k in lower for k in ("idea", "what if", "think", "maybe", "plan to", "brainstorm")):
        category = "idea"
        ai_plan = "1. Break this idea into one 15-minute prototype step.\n2. Decide whether to schedule it this week."
    else:
        category = "task"
        ai_plan = "1. Complete this task in your next free block.\n2. Mark done once finished."

    priority = "high" if any(k in lower for k in ("urgent", "asap", "important", "today", "tonight")) else "normal"
    title = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)[:100]
    return {
        "merge_into_id": None,
        "category": category,
        "title": title,
        "ai_plan": ai_plan,
        "scheduled_for": scheduled_for,
        "priority": priority,
        "kitchen_items": kitchen_items,
        "app_action": app_action,
        "engine": "heuristic",
    }


def _heuristic_analyze_batch(
    raw_message: str,
    cost_tier: str = "low_cost",
    app_context: dict | None = None,
) -> list[dict]:
    chunks = [c.strip() for c in raw_message.split("\n---\n") if c.strip()]
    if not chunks:
        res = _heuristic_single(raw_message, app_context=app_context)
        res["cost_tier"] = cost_tier
        return [res]
    out = []
    for c in chunks:
        r = _heuristic_single(c, app_context=app_context)
        r["cost_tier"] = cost_tier
        out.append(r)
    return out


def analyze_messages(
    raw_message: str,
    sender: str = "Me",
    recent_pending: list[dict] | None = None,
    has_image: bool = False,
    has_document: bool = False,
    app_context: dict | None = None,
) -> list[dict]:
    """2-stage tiered AI router: Stage 1 classifies intent + cost_tier; Stage 2 routes to optimal model."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    recent_ctx = json.dumps(recent_pending or [], ensure_ascii=False)
    detected_intent, cost_tier = _classify_intent_and_tier(
        raw_message, has_image=has_image, has_document=has_document
    )

    system_prompt = (
        "You are a sharp personal assistant handling messages sent or forwarded by the user on WhatsApp (English, Chinese, or Malay).\n"
        "The user may send 1 message OR multiple messages separated by '---'.\n"
        "RULES:\n"
        "1. GROUPING: If consecutive forwarded messages belong to the SAME event or topic "
        "(e.g. 'Yo 星期五打球吗8-10pm' + 'PLAypoint puchong Court b4 & b5'), MERGE them into ONE plan.\n"
        "2. SEPARATING: If messages are DIFFERENT events/tasks (e.g. Saturday Sri Petaling badminton vs Friday Playpoint Puchong vs Refuel car), "
        "return SEPARATE items in the 'plans' array.\n"
        "3. FOLLOW-UP MERGE: Look at 'recent_pending'. If a new message only adds location/court/time details to one of those recent pending items, "
        "set 'merge_into_id' to that item's integer id; otherwise set 'merge_into_id' to null.\n"
        "4. CLARITY: Write a clear, concise English 'title' (e.g. 'Badminton @ Pusat Komuniti Sri Petaling (RM18, 3 Courts)'), "
        "exact 'scheduled_for' ('YYYY-MM-DD HH:MM' or '' if none), and a practical 'ai_plan' as a SINGLE string with newline-separated bullets "
        "(e.g. '• Venue: ...\\n• Time: ...\\n• Action: ...' — never a JSON array, no generic filler).\n"
        "5. RECEIPTS/IMAGES: If '[Attached Image/Receipt]' is present alongside a message like 'Pay christie', combine them into ONE 'pay' task "
        "including the extracted total/merchant in the title and details in ai_plan.\n"
        "6. CATEGORIES: 'planning' (trips, roadmaps, multi-step projects, strategies), "
        "'schedule' (events/appointments/reminders with a time or venue, even if a fee like RM18 is mentioned), "
        "'pay' (paying a person, splitting/settling a bill, utilities), "
        "'bank_statement' (bank/e-wallet statement text or document summaries), "
        "'question' (general/factual questions, typos of questions, math/hard problems, technical questions, or asking what someone's forwarded message means/how to reply), "
        "'kitchen' (groceries/food), 'idea', 'expense' (already-paid personal receipts), or 'task' (other to-dos).\n"
        "7. QUESTIONS, TYPOS, MATH & MESSAGE INTERPRETATION: When category is 'question':\n"
        "   - DIRECT QUESTIONS & TYPOS: If the user asks a general, factual, or how-to question (even with an obvious typo or grammar slip like 'How is hitler' -> 'Who is Hitler?'), understand the intended question, briefly note the typo correction if helpful, and give a clear, accurate, direct answer in 'ai_plan' with a clean title (e.g. 'Who is Hitler?').\n"
        "   - MATH / HARD PROBLEMS: Show step-by-step working and the final answer.\n"
        "   - MESSAGE INTERPRETATION: ONLY break down Tone / Subtext / Suggested Reply when the user is explicitly asking what someone else's message means or how to reply to it.\n"
        "   - NEVER use generic task steps or label a factual question as 'Interpretation of ...'.\n"
        "8. WHOLE-APP ACTIONS & KITCHEN MATRIX:\n"
        "   - ASSET PURCHASE: If the user says they bought/own an asset (car, laptop, phone, camera, property, etc. with a price), set 'app_action' to "
        "{\"type\": \"create_asset\", \"name\": str, \"category\": \"Vehicle\"|\"Electronics\"|\"Hardware\"|\"Property\"|\"Other\", \"condition\": \"New\"|\"Used\", \"purchase_price_myr\": float, \"quantity\": 1, \"search_query\": str}.\n"
        "   - LOAN / DEBT: If the user took a loan or owes/lent money (e.g. car loan, bank loan, personal loan, borrowed), set 'app_action' to "
        "{\"type\": \"create_debt\", \"event_name\": str, \"borrower_name\": str, \"amount_myr\": float, \"direction\": \"i_owe\"|\"owed_to_me\", \"interest_rate_pct\": float, \"interest_type\": \"flat\"|\"reducing\"|\"none\", \"tenure_months\": int|null, \"due_day\": int|null}.\n"
        "   - If BOTH an asset purchase AND a loan are in one message (e.g. bought a car with a loan), return TWO separate items in 'plans' (one create_asset, one create_debt).\n"
        "   - COOKING / CRAVINGS / KITCHEN MATRIX: If the user asks what to eat/cook or craves a style/texture (e.g. 'something crispy'), set category='question', "
        "inspect 'App Context -> kitchen_matrix' (available fridge items, expiring items, cooking methods, textures, taste rules), and recommend specific dishes using what they HAVE (highlighting any crispy/matching technique and any missing items).\n"
        "   - CROSS-APP QUESTIONS: For questions about debts, assets, or net worth, answer directly using 'App Context'.\n"
        "   - Otherwise omit 'app_action' or set {\"type\": \"none\"}.\n"
        "Return ONLY JSON: {\"plans\": [{\"merge_into_id\": int|null, \"category\": str, \"title\": str, "
        "\"ai_plan\": str, \"scheduled_for\": str, \"priority\": \"high\"|\"normal\"|\"low\", \"kitchen_items\": [str], \"app_action\": dict|null}]}"
    )
    app_ctx_json = json.dumps(app_context or {}, ensure_ascii=False)
    user_prompt = (
        f"Current local time: {now_str}\n"
        f"Sender: {sender}\n"
        f"Detected intent hint: {detected_intent}\n"
        f"Recent pending items: {recent_ctx}\n"
        f"App Context: {app_ctx_json}\n"
        f"Forwarded message(s):\n{raw_message}"
    )

    _TIER_ORDER = {
        "high_cost": ["high_cost", "medium_cost", "low_cost"],
        "medium_cost": ["medium_cost", "high_cost", "low_cost"],
        "low_cost": ["low_cost", "medium_cost", "high_cost"],
    }

    def _try_tier(tier_name: str) -> tuple[dict | None, str]:
        if tier_name == "high_cost":
            p, eng = _call_nim_json(
                system_prompt,
                user_prompt,
                models=["nvidia/nemotron-3.5-super-vl-120b-a12b", "mistralai/mistral-large-4-1t-a52b"],
            )
            if p:
                return p, eng
            return _call_groq_json(system_prompt, user_prompt, models=["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
        if tier_name == "medium_cost":
            p, eng = _call_groq_json(system_prompt, user_prompt, models=[settings.groq_model, "qwen/qwen3.8-27b"])
            if p:
                return p, eng
            return _call_nim_json(
                system_prompt,
                user_prompt,
                models=["nvidia/nemotron-3.5-lightning-30b-a3b", "google/gemma-4-31b-it"],
            )
        p, eng = _call_groq_json(system_prompt, user_prompt, models=["openai/gpt-oss-20b", "llama-3.1-8b-instant"])
        if p:
            return p, eng
        return _call_nim_json(system_prompt, user_prompt, models=["nvidia/nemotron-3.5-bolt-e4b"])

    engine = "heuristic"
    parsed = None
    effective_tier = cost_tier
    for tier_candidate in _TIER_ORDER.get(cost_tier, ["low_cost", "medium_cost", "high_cost"]):
        parsed, engine = _try_tier(tier_candidate)
        if parsed:
            effective_tier = tier_candidate
            break

    if not parsed:
        parsed, gem_eng = _call_gemini_json(system_prompt, user_prompt)
        if parsed:
            engine = gem_eng
            effective_tier = "medium_cost"

    if not parsed:
        parsed = _call_ollama_json(f"{system_prompt}\n\n{user_prompt}")
        if parsed:
            engine = "ollama_local"

    if not parsed or not isinstance(parsed.get("plans"), list) or not parsed["plans"]:
        return _heuristic_analyze_batch(raw_message, cost_tier=cost_tier, app_context=app_context)

    results: list[dict] = []
    for item in parsed["plans"]:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category", "task")).strip().lower()
        if cat not in _VALID_CATEGORIES:
            cat = "task"
        prio = str(item.get("priority", "normal")).strip().lower()
        if prio not in _VALID_PRIORITIES:
            prio = "normal"
        k_items = item.get("kitchen_items")
        if not isinstance(k_items, list):
            k_items = []
        app_act = item.get("app_action")
        if not isinstance(app_act, dict):
            app_act = None
        merge_id = item.get("merge_into_id")
        try:
            merge_id = int(merge_id) if merge_id is not None else None
        except (TypeError, ValueError):
            merge_id = None

        results.append(
            {
                "merge_into_id": merge_id,
                "category": cat,
                "title": str(item.get("title") or raw_message.splitlines()[0])[:255].strip(),
                "ai_plan": _format_plan_text(item.get("ai_plan")),
                "scheduled_for": str(item.get("scheduled_for") or "")[:64].strip(),
                "priority": prio,
                "kitchen_items": [str(x).strip() for x in k_items if str(x).strip()][:8],
                "app_action": app_act,
                "engine": engine,
                "cost_tier": effective_tier,
            }
        )
    return results or _heuristic_analyze_batch(raw_message, cost_tier=cost_tier, app_context=app_context)


def generate_morning_briefing_text(
    pending_titles: list[str],
    scheduled_lines: list[str],
    expiring_kitchen: list[str],
) -> tuple[str, str]:
    """Generate a concise morning focus plan using NVIDIA NIM, Groq, Ollama, or fallback."""
    context = (
        f"Scheduled: {scheduled_lines or ['None']}\n"
        f"Pending tasks: {pending_titles[:8] or ['None']}\n"
        f"Expiring kitchen items: {expiring_kitchen or ['None']}"
    )
    sys_p = "Return ONLY a JSON object with key 'focus_plan' as a SINGLE string with 3 newline-separated bullet points (not an array)."

    parsed, engine = _call_nim_json(
        sys_p,
        context,
        models=["nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3.5-lightning-30b-a3b"],
        service_name="assistant_briefing",
    )
    if parsed and parsed.get("focus_plan"):
        return _format_plan_text(parsed["focus_plan"]), engine

    parsed, engine = _call_groq_json(sys_p, context, service_name="assistant_briefing")
    if parsed and parsed.get("focus_plan"):
        return _format_plan_text(parsed["focus_plan"]), engine

    parsed = _call_ollama_json(f"{sys_p}\n\n{context}", service_name="assistant_briefing")
    if parsed and parsed.get("focus_plan"):
        return _format_plan_text(parsed["focus_plan"]), "ollama_local"

    lines = []
    if scheduled_lines:
        lines.append(f"• Lock in your scheduled commitments: {'; '.join(scheduled_lines[:3])}.")
    if pending_titles:
        lines.append(f"• Top priorities to clear first: {', '.join(pending_titles[:3])}.")
    if expiring_kitchen:
        lines.append(f"• Use expiring fridge items tonight: {', '.join(expiring_kitchen[:3])}.")
    if not lines:
        lines.append("• Inbox is clear — forward messages to WhatsApp or add a goal above to plan your day.")
    return "\n".join(lines), "heuristic"
