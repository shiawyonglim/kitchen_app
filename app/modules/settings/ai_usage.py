"""Lightweight AI usage & service telemetry logger, quota calculator, and model tester."""
from __future__ import annotations

import io
import time
import wave
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.database as app_db
from app.config import settings
from app.modules.settings.models import AiModelConfig, AiUsageLog
from app.modules.settings.schemas import (
    AiModelQuotaOut,
    AiProviderUsageOut,
    AiServiceUsageOut,
    AiUsageLogOut,
    AiUsageSummaryOut,
    TestAiModelResponse,
    UpdateAiModelConfigRequest,
)

_PROVIDER_META = [
    ("gemini", "Google Gemini"),
    ("groq", "Groq Cloud"),
    ("nim", "NVIDIA NIM"),
    ("ollama", "Local Ollama"),
]
_PROVIDER_LABEL_MAP = dict(_PROVIDER_META)

_SERVICE_LABELS = {
    "asset_search": "Asset Tracker · Price Search",
    "assistant_triage": "AI Assistant · Intent Triage",
    "assistant_plan": "AI Assistant · Message Planner",
    "assistant_briefing": "AI Assistant · Morning Briefing",
    "assistant_vision": "AI Assistant · Image / Receipt OCR",
    "kitchen_recipe": "Kitchen · Recipe Synthesis",
    "kitchen_taste": "Kitchen · Palate Auto-Profile",
    "model_test": "Settings · Model Health Ping",
}

# Canonical model catalog with capabilities: text, images, web_search, voice_notes
_MODEL_CATALOG: list[dict] = [
    # Google Gemini (current Gemini 3.5+ / 4.x models on Interactions API)
    {
        "provider": "gemini",
        "model": "gemini-4-hydrogen",
        "capabilities": ["text", "images", "web_search", "voice_notes"],
        "limit_period": "day",
        "req_limit": 20,
        "tok_limit": 250000,
    },
    {
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "capabilities": ["text", "images", "web_search", "voice_notes"],
        "limit_period": "day",
        "req_limit": 20,
        "tok_limit": 250000,
    },
    {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "capabilities": ["text", "images", "web_search", "voice_notes"],
        "limit_period": "day",
        "req_limit": 20,
        "tok_limit": 250000,
    },
    {
        "provider": "gemini",
        "model": "gemini-3.5-transcribe",
        "capabilities": ["voice_notes"],
        "limit_period": "day",
        "req_limit": 20,
        "tok_limit": 250000,
    },
    # Groq Cloud
    {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "capabilities": ["text", "web_search"],
        "limit_period": "day",
        "req_limit": 1000,
        "tok_limit": 200000,
    },
    {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "capabilities": ["text", "web_search"],
        "limit_period": "day",
        "req_limit": 1000,
        "tok_limit": 200000,
    },
    {
        "provider": "groq",
        "model": "qwen/qwen3.8-27b",
        "capabilities": ["text"],
        "limit_period": "minute",
        "req_limit": 30,
        "tok_limit": 6000,
    },
    {
        "provider": "groq",
        "model": "whisper-large-v3",
        "capabilities": ["voice_notes"],
        "limit_period": "day",
        "req_limit": 2000,
        "tok_limit": 0,
    },
    {
        "provider": "groq",
        "model": "whisper-large-v3-turbo",
        "capabilities": ["voice_notes"],
        "limit_period": "day",
        "req_limit": 2000,
        "tok_limit": 0,
    },
    # NVIDIA NIM
    {
        "provider": "nim",
        "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "capabilities": ["text", "images"],
        "limit_period": "minute",
        "req_limit": 40,
        "tok_limit": 40000,
    },
    {
        "provider": "nim",
        "model": "mistralai/mistral-large-4-1t-a52b",
        "capabilities": ["text", "images"],
        "limit_period": "minute",
        "req_limit": 40,
        "tok_limit": 40000,
    },
    {
        "provider": "nim",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "capabilities": ["text"],
        "limit_period": "minute",
        "req_limit": 40,
        "tok_limit": 40000,
    },
    {
        "provider": "nim",
        "model": "nvidia/nemotron-3.5-bolt-e4b",
        "capabilities": ["text"],
        "limit_period": "minute",
        "req_limit": 40,
        "tok_limit": 40000,
    },
    {
        "provider": "nim",
        "model": "google/gemma-4-31b-it",
        "capabilities": ["text", "images"],
        "limit_period": "minute",
        "req_limit": 40,
        "tok_limit": 40000,
    },
    # Local Ollama
    {
        "provider": "ollama",
        "model": "local-ollama",
        "capabilities": ["text"],
        "limit_period": "day",
        "req_limit": 0,
        "tok_limit": 0,
    },
]


def _is_provider_configured(provider: str) -> bool:
    if provider == "gemini":
        return bool(settings.gemini_api_key)
    if provider == "groq":
        return bool(settings.groq_api_key)
    if provider == "nim":
        return bool(settings.nvidia_api_key)
    if provider == "ollama":
        return True
    return False


def _utc_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local_midnight_utc(now_utc: datetime) -> datetime:
    local_now = now_utc.astimezone()
    local_mid = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_mid.astimezone(timezone.utc)


def record_ai_usage(
    provider: str,
    model: str,
    service: str,
    status: str = "ok",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    error_detail: str = "",
) -> None:
    """Persist an AI call event without ever raising into the caller."""
    try:
        in_tok = max(0, int(input_tokens or 0))
        out_tok = max(0, int(output_tokens or 0))
        tot_tok = max(0, int(total_tokens or (in_tok + out_tok)))
        lat = max(0, int(latency_ms or 0))
        with app_db.SessionLocal() as db:
            db.add(
                AiUsageLog(
                    provider=str(provider or "unknown")[:32],
                    model=str(model or "unknown")[:128],
                    service=str(service or "general")[:64],
                    status=str(status or "ok")[:24],
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_tokens=tot_tok,
                    latency_ms=lat,
                    error_detail=str(error_detail or "")[:255],
                )
            )
            db.commit()
    except Exception:
        pass


def get_model_quotas(db: Session, recent_rows: list[AiUsageLog] | None = None) -> list[AiModelQuotaOut]:
    now_utc = datetime.now(timezone.utc)
    minute_cutoff = now_utc - timedelta(seconds=60)
    day_cutoff = _local_midnight_utc(now_utc)
    next_midnight = day_cutoff + timedelta(days=1)
    day_reset_secs = max(1, int((next_midnight - now_utc).total_seconds()))

    if recent_rows is None:
        recent_rows = list(
            db.scalars(
                select(AiUsageLog)
                .where(AiUsageLog.created_at >= day_cutoff - timedelta(minutes=2))
                .order_by(AiUsageLog.created_at.desc(), AiUsageLog.id.desc())
            ).all()
        )

    configs = {
        (c.provider, c.model): c
        for c in db.scalars(select(AiModelConfig)).all()
    }

    catalog_map: dict[tuple[str, str], dict] = {
        (item["provider"], item["model"]): dict(item) for item in _MODEL_CATALOG
    }
    ordered_keys = [(item["provider"], item["model"]) for item in _MODEL_CATALOG]

    # Include any custom/local model seen in logs or config
    for key in list(configs.keys()) + [(r.provider, r.model) for r in recent_rows]:
        if key not in catalog_map:
            catalog_map[key] = {
                "provider": key[0],
                "model": key[1],
                "capabilities": ["text"],
                "limit_period": "day",
                "req_limit": 100,
                "tok_limit": 0,
            }
            ordered_keys.append(key)

    out: list[AiModelQuotaOut] = []
    for prov, model in ordered_keys:
        meta = catalog_map[(prov, model)]
        cfg = configs.get((prov, model))
        period = cfg.limit_period if cfg and cfg.limit_period in ("minute", "day") else meta["limit_period"]
        req_limit = cfg.req_limit if cfg is not None else meta["req_limit"]
        tok_limit = cfg.tok_limit if cfg is not None else meta["tok_limit"]

        win_cutoff = minute_cutoff if period == "minute" else day_cutoff
        used_calls = 0
        used_tokens = 0
        oldest_in_min: datetime | None = None
        last_status = ""
        last_error = ""
        last_used_at: datetime | None = None

        for r in recent_rows:
            if r.provider != prov or r.model != model:
                continue
            r_dt = _utc_dt(r.created_at)
            if last_used_at is None:
                last_used_at = r.created_at
                last_status = r.status
                last_error = r.error_detail
            if r_dt >= win_cutoff:
                used_calls += 1
                used_tokens += r.total_tokens
                if period == "minute":
                    oldest_in_min = r_dt if oldest_in_min is None or r_dt < oldest_in_min else oldest_in_min

        if period == "minute":
            if oldest_in_min is not None:
                reset_secs = max(1, int(60 - (now_utc - oldest_in_min).total_seconds()))
            else:
                reset_secs = 60
        else:
            reset_secs = day_reset_secs

        rem_calls = max(0, req_limit - used_calls) if req_limit > 0 else None
        pct_candidates: list[float] = []
        if req_limit > 0:
            pct_candidates.append(max(0.0, min(100.0, ((req_limit - used_calls) / req_limit) * 100.0)))
        if tok_limit > 0:
            pct_candidates.append(max(0.0, min(100.0, ((tok_limit - used_tokens) / tok_limit) * 100.0)))
        rem_pct = round(min(pct_candidates), 1) if pct_candidates else 100.0

        out.append(
            AiModelQuotaOut(
                provider=prov,
                provider_label=_PROVIDER_LABEL_MAP.get(prov, prov.title()),
                model=model,
                capabilities=meta["capabilities"],
                configured=_is_provider_configured(prov),
                limit_period=period,
                used_calls=used_calls,
                req_limit=req_limit,
                used_tokens=used_tokens,
                tok_limit=tok_limit,
                remaining_calls=rem_calls,
                remaining_pct=rem_pct,
                reset_in_seconds=reset_secs,
                last_status=last_status,
                last_error=last_error,
                last_used_at=last_used_at,
            )
        )
    return out


def upsert_model_config(db: Session, req: UpdateAiModelConfigRequest) -> AiModelQuotaOut:
    prov = req.provider.strip().lower()
    model = req.model.strip()
    row = db.scalar(
        select(AiModelConfig).where(
            AiModelConfig.provider == prov,
            AiModelConfig.model == model,
        )
    )
    if row is None:
        row = AiModelConfig(
            provider=prov,
            model=model,
            limit_period=req.limit_period,
            req_limit=req.req_limit,
            tok_limit=req.tok_limit,
        )
        db.add(row)
    else:
        row.limit_period = req.limit_period
        row.req_limit = req.req_limit
        row.tok_limit = req.tok_limit
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    quotas = get_model_quotas(db)
    for q in quotas:
        if q.provider == prov and q.model == model:
            return q
    raise ValueError("Model config could not be loaded.")


def _make_silent_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 4000)
    return buf.getvalue()


def test_ai_model(provider: str, model: str) -> TestAiModelResponse:
    """Run a live minimal ping against a specific AI provider + model and record telemetry."""
    prov = provider.strip().lower()
    mod = model.strip()
    t0 = time.monotonic()

    if prov == "gemini":
        if not settings.gemini_api_key:
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="unconfigured", latency_ms=0, detail="GEMINI_API_KEY not set")
        try:
            import base64

            headers = {
                "x-goog-api-key": settings.gemini_api_key,
                "Content-Type": "application/json",
                "Api-Revision": "2026-05-20",
            }
            if mod == "gemini-3.5-transcribe":
                b64_wav = base64.b64encode(_make_silent_wav_bytes()).decode("ascii")
                url = "https://generativelanguage.googleapis.com/v1beta/interactions"
                payload = {
                    "model": mod,
                    "input": [{"type": "audio", "data": b64_wav, "mime_type": "audio/wav"}],
                }
            else:
                url = "https://generativelanguage.googleapis.com/v1/interactions"
                think_lvl = "minimal" if "lite" in mod or "3.6" in mod else "low"
                payload = {
                    "model": mod,
                    "input": "Reply OK",
                    "generation_config": {"thinking_level": think_lvl},
                }
            resp = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=35.0,
            )
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                usage = resp.json().get("usage") or {}
                tok = int(usage.get("total_tokens") or 0)
                record_ai_usage(prov, mod, "model_test", status="ok", total_tokens=tok, latency_ms=ms)
                det = "Voice Transcribe OK" if mod == "gemini-3.5-transcribe" else "Free Tier Text/Images OK (Search needs Paid Tier)"
                return TestAiModelResponse(provider=prov, model=mod, ok=True, status="ok", latency_ms=ms, tokens=tok, detail=det)
            st = "rate_limited" if resp.status_code == 429 else "error"
            det = f"HTTP {resp.status_code}"
            record_ai_usage(prov, mod, "model_test", status=st, latency_ms=ms, error_detail=det)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status=st, latency_ms=ms, detail=det)
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            record_ai_usage(prov, mod, "model_test", status="error", latency_ms=ms, error_detail=type(exc).__name__)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="error", latency_ms=ms, detail=type(exc).__name__)

    if prov == "groq":
        if not settings.groq_api_key:
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="unconfigured", latency_ms=0, detail="GROQ_API_KEY not set")
        try:
            headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
            if "whisper" in mod.lower():
                resp = httpx.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers=headers,
                    files={"file": ("ping.wav", _make_silent_wav_bytes(), "audio/wav")},
                    data={"model": mod},
                    timeout=20.0,
                )
            else:
                resp = httpx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"model": mod, "messages": [{"role": "user", "content": "Reply OK"}], "max_completion_tokens": 12},
                    timeout=20.0,
                )
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                usage = resp.json().get("usage") or {}
                tok = int(usage.get("total_tokens") or 0)
                record_ai_usage(prov, mod, "model_test", status="ok", total_tokens=tok, latency_ms=ms)
                return TestAiModelResponse(provider=prov, model=mod, ok=True, status="ok", latency_ms=ms, tokens=tok, detail="Connected OK")
            st = "rate_limited" if resp.status_code == 429 else "error"
            det = f"HTTP {resp.status_code}"
            record_ai_usage(prov, mod, "model_test", status=st, latency_ms=ms, error_detail=det)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status=st, latency_ms=ms, detail=det)
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            record_ai_usage(prov, mod, "model_test", status="error", latency_ms=ms, error_detail=type(exc).__name__)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="error", latency_ms=ms, detail=type(exc).__name__)

    if prov == "nim":
        if not settings.nvidia_api_key:
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="unconfigured", latency_ms=0, detail="NVIDIA_API_KEY not set")
        try:
            resp = httpx.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.nvidia_api_key}", "Content-Type": "application/json"},
                json={"model": mod, "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 12},
                timeout=20.0,
            )
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                usage = resp.json().get("usage") or {}
                tok = int(usage.get("total_tokens") or 0)
                record_ai_usage(prov, mod, "model_test", status="ok", total_tokens=tok, latency_ms=ms)
                return TestAiModelResponse(provider=prov, model=mod, ok=True, status="ok", latency_ms=ms, tokens=tok, detail="Connected OK")
            st = "rate_limited" if resp.status_code == 429 else "error"
            det = f"HTTP {resp.status_code}"
            record_ai_usage(prov, mod, "model_test", status=st, latency_ms=ms, error_detail=det)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status=st, latency_ms=ms, detail=det)
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            record_ai_usage(prov, mod, "model_test", status="error", latency_ms=ms, error_detail=type(exc).__name__)
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="error", latency_ms=ms, detail=type(exc).__name__)

    if prov == "ollama":
        try:
            tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
            models = tags.json().get("models", []) if tags.status_code == 200 else []
            target_mod = mod if mod != "local-ollama" else (models[0]["name"] if models else "")
            if not target_mod:
                return TestAiModelResponse(provider=prov, model=mod, ok=False, status="offline", latency_ms=int((time.monotonic() - t0) * 1000), detail="Ollama offline or no models pulled")
            resp = httpx.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": target_mod, "prompt": "Reply OK", "stream": False},
                timeout=15.0,
            )
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                rjson = resp.json()
                tok = int(rjson.get("prompt_eval_count") or 0) + int(rjson.get("eval_count") or 0)
                record_ai_usage(prov, target_mod, "model_test", status="ok", total_tokens=tok, latency_ms=ms)
                return TestAiModelResponse(provider=prov, model=target_mod, ok=True, status="ok", latency_ms=ms, tokens=tok, detail=f"Ollama ({target_mod}) OK")
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="error", latency_ms=ms, detail=f"HTTP {resp.status_code}")
        except Exception:
            return TestAiModelResponse(provider=prov, model=mod, ok=False, status="offline", latency_ms=int((time.monotonic() - t0) * 1000), detail="Ollama not running on :11434")

    return TestAiModelResponse(provider=prov, model=mod, ok=False, status="error", latency_ms=0, detail="Unknown provider")


def get_ai_usage_summary(db: Session, days: int = 30) -> AiUsageSummaryOut:
    window_days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    today_date = now.astimezone().date()

    rows = list(
        db.scalars(
            select(AiUsageLog)
            .where(AiUsageLog.created_at >= cutoff)
            .order_by(AiUsageLog.created_at.desc(), AiUsageLog.id.desc())
        ).all()
    )

    prov_buckets: dict[str, dict] = {
        key: {
            "provider": key,
            "label": label,
            "configured": _is_provider_configured(key),
            "calls": 0,
            "today_calls": 0,
            "success_calls": 0,
            "rate_limited_calls": 0,
            "error_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "today_tokens": 0,
            "models": [],
            "last_used_at": None,
            "last_status": "",
        }
        for key, label in _PROVIDER_META
    }
    svc_buckets: dict[str, dict] = {}

    total_calls = 0
    today_calls = 0
    success_calls = 0
    rate_limited_calls = 0
    error_calls = 0
    total_tokens = 0
    today_tokens = 0

    for row in rows:
        total_calls += 1
        total_tokens += row.total_tokens
        row_dt = _utc_dt(row.created_at)
        is_today = row_dt.astimezone().date() == today_date
        if is_today:
            today_calls += 1
            today_tokens += row.total_tokens

        if row.status == "ok":
            success_calls += 1
        elif row.status == "rate_limited":
            rate_limited_calls += 1
        else:
            error_calls += 1

        pb = prov_buckets.get(row.provider)
        if pb is None:
            pb = {
                "provider": row.provider,
                "label": row.provider.title(),
                "configured": _is_provider_configured(row.provider),
                "calls": 0,
                "today_calls": 0,
                "success_calls": 0,
                "rate_limited_calls": 0,
                "error_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "today_tokens": 0,
                "models": [],
                "last_used_at": None,
                "last_status": "",
            }
            prov_buckets[row.provider] = pb

        pb["calls"] += 1
        pb["input_tokens"] += row.input_tokens
        pb["output_tokens"] += row.output_tokens
        pb["total_tokens"] += row.total_tokens
        if is_today:
            pb["today_calls"] += 1
            pb["today_tokens"] += row.total_tokens
        if row.status == "ok":
            pb["success_calls"] += 1
        elif row.status == "rate_limited":
            pb["rate_limited_calls"] += 1
        else:
            pb["error_calls"] += 1
        if row.model and row.model not in pb["models"]:
            pb["models"].append(row.model)
        if pb["last_used_at"] is None:
            pb["last_used_at"] = row.created_at
            pb["last_status"] = row.status

        sb = svc_buckets.get(row.service)
        if sb is None:
            sb = {
                "service": row.service,
                "label": _SERVICE_LABELS.get(row.service, row.service.replace("_", " ").title()),
                "calls": 0,
                "success_calls": 0,
                "rate_limited_calls": 0,
                "error_calls": 0,
                "total_tokens": 0,
                "latency_sum": 0,
                "last_model": row.model,
                "last_used_at": row.created_at,
            }
            svc_buckets[row.service] = sb
        sb["calls"] += 1
        sb["total_tokens"] += row.total_tokens
        sb["latency_sum"] += row.latency_ms
        if row.status == "ok":
            sb["success_calls"] += 1
        elif row.status == "rate_limited":
            sb["rate_limited_calls"] += 1
        else:
            sb["error_calls"] += 1

    providers_out = [AiProviderUsageOut(**v) for v in prov_buckets.values()]
    services_out = [
        AiServiceUsageOut(
            service=v["service"],
            label=v["label"],
            calls=v["calls"],
            success_calls=v["success_calls"],
            rate_limited_calls=v["rate_limited_calls"],
            error_calls=v["error_calls"],
            total_tokens=v["total_tokens"],
            avg_latency_ms=round(v["latency_sum"] / v["calls"]) if v["calls"] else 0,
            last_model=v["last_model"],
            last_used_at=v["last_used_at"],
        )
        for v in sorted(svc_buckets.values(), key=lambda item: item["calls"], reverse=True)
    ]

    return AiUsageSummaryOut(
        days=window_days,
        total_calls=total_calls,
        today_calls=today_calls,
        success_calls=success_calls,
        rate_limited_calls=rate_limited_calls,
        error_calls=error_calls,
        total_tokens=total_tokens,
        today_tokens=today_tokens,
        providers=providers_out,
        services=services_out,
        models=get_model_quotas(db),
        recent_logs=[AiUsageLogOut.model_validate(r) for r in rows[:25]],
    )


def clear_ai_usage_logs(db: Session) -> int:
    res = db.execute(delete(AiUsageLog))
    db.commit()
    return int(res.rowcount or 0)
