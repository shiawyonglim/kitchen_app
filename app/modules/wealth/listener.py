from __future__ import annotations

import asyncio
import json
import select
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.modules.settings import service as settings_service
from app.modules.wealth.email_client import ImapEmailProvider


class WealthLiveListener:
    """Persistent IMAP IDLE listener that triggers instant sync on new inbox activity."""

    def __init__(self) -> None:
        self.status: str = "starting"
        self.last_event_at: str | None = None
        self.last_error: str | None = None
        self.total_events_synced: int = 0
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "last_event_at": self.last_event_at,
            "last_error": self.last_error,
            "total_events_synced": self.total_events_synced,
            "email_mode": settings.email_mode,
        }

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    def broadcast(self, payload: dict[str, Any]) -> None:
        msg = json.dumps(payload)
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def _broadcast_threadsafe(self, payload: dict[str, Any]) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.broadcast, payload)

    def _run_sync_now(self) -> None:
        self.status = "syncing"
        with SessionLocal() as db:
            res_settings = settings_service.run_daily_auto_sync(db)
            status_text = res_settings.last_auto_sync_status or ""
        self.last_event_at = datetime.now(timezone.utc).isoformat()
        self.last_error = None
        self.total_events_synced += 1
        self._broadcast_threadsafe(
            {
                "type": "ledger_updated",
                "status": status_text,
                "at": self.last_event_at,
            }
        )

    def _idle_session_blocking(self, stop_flag: list[bool]) -> None:
        """Open one IMAP IDLE session, wait for EXISTS/RECENT push, and sync immediately."""
        provider = ImapEmailProvider(
            host=settings.imap_host,
            port=settings.imap_port,
            user=settings.imap_user,
            app_password=settings.imap_app_password,
        )
        client = provider._connect()
        try:
            sock = client.sock
            tag = client._new_tag()
            client.send(tag + b" IDLE\r\n")
            resp = client.readline()
            if not resp.startswith(b"+"):
                raise RuntimeError(f"IMAP IDLE rejected: {resp.decode(errors='replace').strip()}")

            self.status = "listening_idle"
            self.last_error = None
            idle_started = datetime.now(timezone.utc)
            needs_sync = False

            while not stop_flag[0]:
                # Renew IDLE every 240s per RFC 2177
                if (datetime.now(timezone.utc) - idle_started).total_seconds() >= 240:
                    break

                # Check if user disabled live listener in Settings
                with SessionLocal() as db:
                    if not settings_service.get_settings(db).live_listener_enabled:
                        break

                readable, _, _ = select.select([sock], [], [], 5.0)
                if not readable:
                    continue

                line = client.readline()
                if not line:
                    # Server closed connection cleanly
                    break
                upper = line.upper()
                if b"EXISTS" in upper or b"RECENT" in upper:
                    needs_sync = True
                    break

            try:
                client.send(b"DONE\r\n")
                # Drain until tagged OK
                for _ in range(10):
                    done_line = client.readline()
                    if not done_line or done_line.startswith(tag):
                        break
            except Exception:
                pass
        finally:
            try:
                client.logout()
            except Exception:
                pass

        if needs_sync and not stop_flag[0]:
            self._run_sync_now()

    async def run_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        stop_flag = [False]
        try:
            while True:
                if settings.email_mode != "imap":
                    self.status = "mock_mode"
                    await asyncio.sleep(10)
                    continue

                def _is_enabled() -> bool:
                    with SessionLocal() as db:
                        return settings_service.get_settings(db).live_listener_enabled

                enabled = await asyncio.to_thread(_is_enabled)
                if not enabled:
                    self.status = "disabled"
                    await asyncio.sleep(5)
                    continue

                try:
                    await asyncio.to_thread(self._idle_session_blocking, stop_flag)
                except asyncio.CancelledError:
                    stop_flag[0] = True
                    raise
                except Exception as exc:
                    self.status = "reconnecting"
                    self.last_error = str(exc)[:180]
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            stop_flag[0] = True
            self.status = "stopped"
            raise


wealth_listener = WealthLiveListener()

