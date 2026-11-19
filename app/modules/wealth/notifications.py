from __future__ import annotations

import logging
import urllib.request
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.modules.wealth.models import LedgerEntry

logger = logging.getLogger(__name__)


def send_ntfy_notification(
    title: str,
    message: str,
    *,
    tags: str = "moneybag",
    priority: str = "high",
) -> bool:
    """Send a push notification to the configured ntfy.sh topic URL. Never raises."""
    topic_url = (settings.ntfy_topic_url or "").strip()
    if not topic_url.startswith(("http://", "https://")):
        return False

    req = urllib.request.Request(
        topic_url,
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title[:120],
            "Tags": tags,
            "Priority": priority,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        logger.warning("ntfy notification failed: %s", exc)
        return False


def _format_entry_line(entry: LedgerEntry) -> str:
    sign = "+" if entry.tx_type in ("income", "repayment") else "-"
    return f"{sign}RM {entry.amount_myr:,.2f} · {entry.merchant} ({entry.category}) [{entry.account_name}]"


def notify_new_transactions(entries: list[LedgerEntry]) -> int:
    """Send one ntfy push notification summarizing newly synced non-self transactions."""
    relevant = [e for e in entries if not e.is_self_transfer]
    if not relevant:
        return 0

    if len(relevant) == 1:
        e = relevant[0]
        title = {
            "income": "New Income",
            "repayment": "New Repayment",
        }.get(e.tx_type, "New Expense")
        tags = "chart_with_upwards_trend" if e.tx_type == "income" else "money_with_wings"
        body = _format_entry_line(e)
    else:
        title = f"{len(relevant)} New Transactions"
        tags = "moneybag"
        lines = [_format_entry_line(e) for e in relevant[:4]]
        if len(relevant) > 4:
            lines.append(f"...and {len(relevant) - 4} more")
        body = "\n".join(lines)

    return len(relevant) if send_ntfy_notification(title, body, tags=tags) else 0
