"""Free LLM planner (Groq primary, local Ollama fallback, deterministic heuristic backup)."""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
import httpx

from app.config import settings

_VALID_CATEGORIES = {"task", "schedule", "idea", "kitchen", "expense"}
_VALID_PRIORITIES = {"high", "normal", "low"}


def _format_plan_text(val) -> str:
    if isinstance(val, list):
        return "\n".join(f"• {str(x).strip()}" for x in val if str(x).strip())
    return str(val or "").strip()


def extract_image_context(image_base64: str, image_mime: str = "image/jpeg") -> str:
    """Read a forwarded image/receipt using Gemini Vision (free tier) or local RapidOCR fallback."""
    if not image_base64:
        return ""
    if settings.gemini_api_key:
        try:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={settings.gemini_api_key}"
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": (
                                    "Extract all key details from this image concisely in plain text: "
                                    "if it is a receipt, list merchant/type, date, key items, and exact Net Total / Grand Total; "
                                    "if it is a screenshot/poster/chat, summarize all dates, times, venues, amounts, and names."
                                )
                            },
                            {
                                "inline_data": {
                                    "mime_type": image_mime or "image/jpeg",
                                    "data": image_base64,
                                }
                            },
                        ]
                    }
                ]
            }
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code == 200:
                parts = resp.json()["candidates"][0]["content"]["parts"]
                text = " ".join(p.get("text", "") for p in parts).strip()
                if text:
                    return text
        except Exception:
            pass

    try:
        from app.modules.kitchen import ocr

        lines = ocr.extract_receipt_lines(base64.b64decode(image_base64))
        if lines:
            return " | ".join(lines[:20])
    except Exception:
        pass
    return "Image attached (could not extract text)."


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


def _call_groq_json(system_prompt: str, user_prompt: str) -> dict | None:
    if not settings.groq_api_key:
        return None
    candidates = list(
        dict.fromkeys([settings.groq_model, "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"])
    )
    for model_id in candidates:
        if not model_id:
            continue
        try:
            with httpx.Client(timeout=18.0) as client:
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
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
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = _extract_json_dict(content)
                if parsed is not None:
                    return parsed
        except Exception:
            continue
    return None


def _call_ollama_json(prompt: str) -> dict | None:
    try:
        with httpx.Client(timeout=0.8) as probe:
            tags = probe.get("http://127.0.0.1:11434/api/tags")
            models = tags.json().get("models", []) if tags.status_code == 200 else []
            model_name = models[0]["name"] if models else None
        if not model_name:
            return None
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": model_name, "prompt": prompt, "format": "json", "stream": False},
            )
        if resp.status_code == 200 and resp.json().get("response"):
            return _extract_json_dict(resp.json()["response"])
    except Exception:
        return None
    return None


def _next_weekday(now: datetime, target_wd: int) -> datetime:
    days_ahead = (target_wd - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return now + timedelta(days=days_ahead)


def _heuristic_single(text: str) -> dict:
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
    is_event = any(k in lower for k in ("court", "shuttle", "打球", "🏸", "dinner", "meeting", "appointment", "call"))
    if any(k in lower for k in ("buy ", "grocer", "fridge", "tomato", "chicken", "egg", "milk", "kangkung", "chili")):
        category = "kitchen"
        buy_m = re.search(r"buy\s+(.+)", text, re.IGNORECASE)
        if buy_m:
            raw_list = re.split(r",|\band\b", buy_m.group(1))
            kitchen_items = [x.strip(" .!?") for x in raw_list if 1 < len(x.strip(" .!?")) < 60][:5]
        ai_plan = "1. Added groceries to your Kitchen Shopping List.\n2. Check Fridge Countdown before heading out."
    elif not is_event and any(k in lower for k in ("paid ", "spend", "receipt", "transfer", "invoice")):
        category = "expense"
        ai_plan = "1. Verify amount and category in Wealth Ledger.\n2. Attach receipt or mark if claimable."
    elif scheduled_for or is_event or any(k in lower for k in ("remind", "schedule")):
        category = "schedule"
        ai_plan = f"1. Confirmed time slot: {scheduled_for or 'TBD'}.\n2. Prepare gear/items ahead of time."
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
        "engine": "heuristic",
    }


def _heuristic_analyze_batch(raw_message: str) -> list[dict]:
    chunks = [c.strip() for c in raw_message.split("\n---\n") if c.strip()]
    if not chunks:
        return [_heuristic_single(raw_message)]
    return [_heuristic_single(c) for c in chunks]


def analyze_messages(
    raw_message: str,
    sender: str = "Me",
    recent_pending: list[dict] | None = None,
) -> list[dict]:
    """Classify 1 or more forwarded messages into merged or separated plans."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    recent_ctx = json.dumps(recent_pending or [], ensure_ascii=False)

    system_prompt = (
        "You are a sharp personal assistant parsing forwarded WhatsApp messages (English, Chinese, or Malay).\n"
        "The user may forward 1 message OR multiple messages separated by '---'.\n"
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
        "5. RECEIPTS/IMAGES: If '[Attached Image/Receipt]' is present alongside a message like 'Pay christie', combine them into ONE task "
        "including the extracted total/merchant in the title and details in ai_plan.\n"
        "6. CATEGORIES: 'schedule' (events/appointments/reminders with a time or venue, even if a fee like RM18 is mentioned), "
        "'task', 'idea', 'kitchen' (groceries/food), or 'expense' (actual payments/receipts).\n"
        "Return ONLY JSON: {\"plans\": [{\"merge_into_id\": int|null, \"category\": str, \"title\": str, "
        "\"ai_plan\": str, \"scheduled_for\": str, \"priority\": \"high\"|\"normal\"|\"low\", \"kitchen_items\": [str]}]}"
    )
    user_prompt = (
        f"Current local time: {now_str}\n"
        f"Sender: {sender}\n"
        f"Recent pending items: {recent_ctx}\n"
        f"Forwarded message(s):\n{raw_message}"
    )

    engine = "heuristic"
    parsed = _call_groq_json(system_prompt, user_prompt)
    if parsed:
        engine = "groq"
    else:
        parsed = _call_ollama_json(f"{system_prompt}\n\n{user_prompt}")
        if parsed:
            engine = "ollama_local"

    if not parsed or not isinstance(parsed.get("plans"), list) or not parsed["plans"]:
        return _heuristic_analyze_batch(raw_message)

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
                "engine": engine,
            }
        )
    return results or _heuristic_analyze_batch(raw_message)


def generate_morning_briefing_text(
    pending_titles: list[str],
    scheduled_lines: list[str],
    expiring_kitchen: list[str],
) -> tuple[str, str]:
    """Generate a concise morning focus plan using Groq, Ollama, or deterministic fallback."""
    context = (
        f"Scheduled: {scheduled_lines or ['None']}\n"
        f"Pending tasks: {pending_titles[:8] or ['None']}\n"
        f"Expiring kitchen items: {expiring_kitchen or ['None']}"
    )
    sys_p = "Return ONLY a JSON object with key 'focus_plan' as a SINGLE string with 3 newline-separated bullet points (not an array)."

    parsed = _call_groq_json(sys_p, context)
    if parsed and parsed.get("focus_plan"):
        return _format_plan_text(parsed["focus_plan"]), "groq"

    parsed = _call_ollama_json(f"{sys_p}\n\n{context}")
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
