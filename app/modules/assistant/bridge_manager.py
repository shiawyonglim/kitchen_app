"""Manages the local WhatsApp Web Bridge (Option 1) subprocess alongside FastAPI."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_BRIDGE_DIR = Path(__file__).resolve().parents[3] / "scripts" / "whatsapp_bridge"
_STATUS_FILE = _BRIDGE_DIR / "bridge_status.json"
_AUTH_DIR = _BRIDGE_DIR / "auth_info"
_proc: subprocess.Popen | None = None


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def start_bridge() -> bool:
    """Launch node index.js if configured and not already running."""
    global _proc
    if is_running():
        return True
    if "pytest" in sys.modules:
        return False
    if not settings.whatsapp_bridge_token:
        return False
    if not (_BRIDGE_DIR / "node_modules").exists():
        logger.warning("WhatsApp bridge skipped: scripts/whatsapp_bridge/node_modules not found.")
        return False
    node_bin = shutil.which("node")
    if not node_bin:
        logger.warning("WhatsApp bridge skipped: node executable not found on PATH.")
        return False

    try:
        _proc = subprocess.Popen(
            [node_bin, "index.js"],
            cwd=str(_BRIDGE_DIR),
            stdout=None,
            stderr=None,
        )
        logger.info("Started WhatsApp Web Bridge subprocess (pid=%s).", _proc.pid)
        return True
    except Exception as exc:
        logger.warning("Failed to start WhatsApp Web Bridge: %s", exc)
        _proc = None
        return False


def stop_bridge() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None


def restart_bridge(clear_session: bool = False) -> bool:
    stop_bridge()
    if clear_session and _AUTH_DIR.exists():
        shutil.rmtree(_AUTH_DIR, ignore_errors=True)
    if _STATUS_FILE.exists():
        try:
            _STATUS_FILE.unlink()
        except OSError:
            pass
    return start_bridge()


def get_bridge_status() -> dict:
    running = is_running()
    data = {"status": "stopped", "qr_data_url": "", "user": "", "updated_at": ""}
    if _STATUS_FILE.exists():
        try:
            raw = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    if not running and data.get("status") in ("connected", "qr_ready", "starting"):
        data["status"] = "stopped"
    return {
        "running": running,
        "configured": bool(settings.whatsapp_bridge_token),
        "status": data.get("status", "stopped"),
        "qr_data_url": data.get("qr_data_url", ""),
        "user": data.get("user", ""),
        "updated_at": data.get("updated_at", ""),
    }
