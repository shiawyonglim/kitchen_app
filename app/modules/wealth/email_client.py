from __future__ import annotations

import base64
import email
import imaplib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from bs4 import BeautifulSoup
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings


@dataclass
class EmailAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class RawEmail:
    message_id: str
    sender_email: str
    sender_name: str
    subject: str
    body: str
    received_at: datetime
    gmail_thread_id: str | None = None
    attachments: list[EmailAttachment] = field(default_factory=list)


def _parse_gm_thrid(header_bytes: bytes | None) -> str | None:
    """Convert Gmail IMAP X-GM-THRID decimal ID to lowercase hex for direct Gmail Web URLs."""
    if not header_bytes:
        return None
    m = re.search(rb"X-GM-THRID\s+(\d+)", header_bytes)
    if not m:
        return None
    return format(int(m.group(1)), "x")


def _decode_mime_str(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded).strip()


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                plain_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)
        if plain_parts:
            return "\n".join(plain_parts)
        if html_parts:
            return BeautifulSoup("\n".join(html_parts), "html.parser").get_text(" ", strip=True)
        return ""

    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    if msg.get_content_type() == "text/html":
        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return text


def _extract_attachments(msg: email.message.Message) -> list[EmailAttachment]:
    attachments: list[EmailAttachment] = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = (part.get_content_type() or "").lower()
        disposition = str(part.get("Content-Disposition") or "").lower()
        filename = _decode_mime_str(part.get_filename())
        is_statement_type = content_type in ("application/pdf", "text/csv", "application/vnd.ms-excel") or filename.lower().endswith((".pdf", ".csv"))
        if "attachment" not in disposition and not (filename and is_statement_type) and content_type != "application/pdf":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        attachments.append(
            EmailAttachment(
                filename=filename or ("statement.pdf" if content_type == "application/pdf" else "statement.csv"),
                content_type=content_type,
                data=payload,
            )
        )
    return attachments


def _imap_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


class ImapEmailProvider:
    """Reads real emails over IMAP SSL with targeted FROM filtering to skip junk."""

    def __init__(self, host: str, port: int, user: str, app_password: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.app_password = app_password

    def _connect(self) -> imaplib.IMAP4_SSL:
        if not self.user or not self.app_password:
            raise ValueError("IMAP_USER and IMAP_APP_PASSWORD must be configured for EMAIL_MODE=imap.")
        client = imaplib.IMAP4_SSL(self.host, self.port)
        client.login(self.user, self.app_password)
        client.select("INBOX", readonly=True)
        return client

    def fetch_messages(
        self,
        start_date: date,
        end_date: date,
        sender_emails: list[str] | None = None,
        headers_only: bool = False,
    ) -> list[RawEmail]:
        client = self._connect()
        try:
            since_str = _imap_date(start_date)
            before_str = _imap_date(end_date + timedelta(days=1))

            msg_ids: set[bytes] = set()
            if sender_emails:
                for sender in sender_emails:
                    criteria = f'(SINCE "{since_str}" BEFORE "{before_str}" FROM "{sender}")'
                    status, data = client.search(None, criteria)
                    if status == "OK" and data and data[0]:
                        msg_ids.update(data[0].split())
            else:
                criteria = f'(SINCE "{since_str}" BEFORE "{before_str}")'
                status, data = client.search(None, criteria)
                if status == "OK" and data and data[0]:
                    msg_ids.update(data[0].split())

            results: list[RawEmail] = []
            is_gmail = "gmail" in self.host.lower()
            if headers_only:
                fetch_spec = (
                    "(X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
                    if is_gmail
                    else "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
                )
            else:
                fetch_spec = "(X-GM-THRID RFC822)" if is_gmail else "(RFC822)"
            sorted_ids = sorted(msg_ids, key=lambda x: int(x))
            batch_size = 100 if headers_only else 25

            for i in range(0, len(sorted_ids), batch_size):
                batch = b",".join(sorted_ids[i : i + batch_size])
                status, msg_data = client.fetch(batch, fetch_spec)
                if status != "OK" or not msg_data:
                    continue
                for item in msg_data:
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    gmail_thread_id = _parse_gm_thrid(item[0]) if is_gmail else None
                    raw_bytes = item[1]
                    msg = email.message_from_bytes(raw_bytes)
                    display_name, addr = parseaddr(_decode_mime_str(msg.get("From")))
                    sender_email = addr.lower().strip()
                    if not sender_email:
                        continue
                    subject = _decode_mime_str(msg.get("Subject"))
                    message_id = (msg.get("Message-ID") or f"imap-{len(results)}").strip()
                    try:
                        dt = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else datetime.now(timezone.utc)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        dt = datetime.now(timezone.utc)

                    body = "" if headers_only else _extract_body(msg)
                    attachments = [] if headers_only else _extract_attachments(msg)
                    results.append(
                        RawEmail(
                            message_id=message_id,
                            sender_email=sender_email,
                            sender_name=display_name or sender_email.split("@")[0],
                            subject=subject,
                            body=body,
                            received_at=dt,
                            gmail_thread_id=gmail_thread_id,
                            attachments=attachments,
                        )
                    )
            return results
        finally:
            try:
                client.close()
            except Exception:
                pass
            client.logout()

    def resolve_gmail_thread_id(self, message_id: str) -> str | None:
        """Look up the Gmail hex thread ID for an existing email by its RFC822 Message-ID."""
        if not message_id or "gmail" not in self.host.lower():
            return None
        msg_id = message_id.strip()
        client = self._connect()
        try:
            for mailbox in (None, '"[Gmail]/All Mail"'):
                if mailbox is not None:
                    sel_status, _ = client.select(mailbox, readonly=True)
                    if sel_status != "OK":
                        continue
                status, data = client.search(None, "HEADER", "Message-ID", msg_id)
                if status != "OK" or not data or not data[0]:
                    continue
                seq = data[0].split()[-1]
                f_status, f_data = client.fetch(seq, "(X-GM-THRID)")
                if f_status == "OK" and f_data:
                    for item in f_data:
                        raw_hdr = item[0] if isinstance(item, tuple) else item
                        thrid = _parse_gm_thrid(raw_hdr if isinstance(raw_hdr, bytes) else None)
                        if thrid:
                            return thrid
            return None
        finally:
            try:
                client.close()
            except Exception:
                pass
            client.logout()


class DemoEmailProvider:
    """Built-in realistic Malaysian inbox for instant testing without IMAP credentials."""

    _SEED_EMAILS: list[tuple[str, str, str, str, str, str]] = [
        # (id, sender_email, sender_name, subject, body, iso_datetime)
        ("demo-001", "no-reply@bolt.eu", "Bolt", "Your Bolt ride receipt", "Thanks for riding with Bolt! Total charged: RM 18.40. Route: Subang Jaya to KL Sentral.", "2026-09-02T09:15:00+08:00"),
        ("demo-002", "noreply@touchngo.com.my", "Touch 'n Go eWallet", "TNG eWallet Parking Payment", "Payment successful at Sunway Pyramid Parking. Amount: RM 6.00.", "2026-09-03T18:40:00+08:00"),
        ("demo-003", "promo@lazada.com.my", "Lazada Malaysia", "9.9 Mega Sale Starts Now! Up to 90% OFF", "Check out flash vouchers and free shipping deals today!", "2026-09-04T10:00:00+08:00"),
        ("demo-004", "m2u@maybank.com.my", "Maybank2u", "Maybank2u DuitNow QR Transfer Alert", "You have paid RM 24.50 to Jaya Grocer via DuitNow QR.", "2026-09-06T19:20:00+08:00"),
        ("demo-005", "noreply@wise.com", "Wise", "You received a payment from AI Vision Labs", "Your AI freelance invoice #104 has been paid. Deposited: MYR 2,850.00.", "2026-09-10T14:05:00+08:00"),
        ("demo-006", "no-reply@bolt.eu", "Bolt", "Your Bolt ride receipt", "Thanks for riding with Bolt! Total charged: RM 22.90. Route: Bangsar to Bukit Bintang.", "2026-09-14T21:10:00+08:00"),
        ("demo-007", "newsletter@shopee.com.my", "Shopee Deals", "Coins Cashback Weekend is Here", "Claim your RM 5 voucher on electronics.", "2026-09-16T11:30:00+08:00"),
        ("demo-008", "receipts@setel.com", "Setel", "Your Setel Fuel Receipt - Petronas", "Fuel purchase Ron95 completed. Total Paid: RM 68.00.", "2026-09-19T08:45:00+08:00"),
        ("demo-009", "noreply@touchngo.com.my", "Touch 'n Go eWallet", "TNG eWallet Parking Payment", "Parking fee deducted at Mid Valley Megamall. Total: RM 9.00.", "2026-09-25T16:50:00+08:00"),
        ("demo-010", "jobs-noreply@linkedin.com", "LinkedIn", "12 new AI Engineer jobs in Kuala Lumpur", "See who is hiring near you.", "2026-10-01T07:00:00+08:00"),
        ("demo-011", "m2u@maybank.com.my", "Maybank2u", "Maybank2u Card Spend Alert", "Card ending 4421 spent RM 135.80 at NSK Trade City.", "2026-10-05T17:30:00+08:00"),
        ("demo-012", "no-reply@bolt.eu", "Bolt", "Your Bolt ride receipt", "Thanks for riding with Bolt! Total charged: RM 15.60.", "2026-10-12T13:20:00+08:00"),
        ("demo-013", "noreply@wise.com", "Wise", "You received a payment from LLM Ops Consulting", "AI freelance milestone payout received: MYR 3,200.00.", "2026-10-18T15:10:00+08:00"),
        ("demo-014", "noreply@touchngo.com.my", "Touch 'n Go eWallet", "TNG eWallet Toll & Parking", "Parking deduction at Pavilion KL. Amount: RM 12.00.", "2026-10-24T20:15:00+08:00"),
        ("demo-015", "receipts@setel.com", "Setel", "Your Setel Fuel Receipt - Petronas", "Fuel purchase Ron95 completed. Total Paid: RM 72.50.", "2026-10-30T09:05:00+08:00"),
        ("demo-016", "no-reply@bolt.eu", "Bolt", "Your Bolt ride receipt", "Thanks for riding with Bolt! Total charged: RM 19.80.", "2026-11-04T22:00:00+08:00"),
        ("demo-017", "m2u@maybank.com.my", "Maybank2u", "Maybank2u DuitNow QR Transfer Alert", "You have paid RM 42.00 at Badminton Court Booking.", "2026-11-08T19:45:00+08:00"),
        ("demo-018", "noreply@wise.com", "Wise", "You received a payment from PromptCraft Studio", "AI freelance retainer received: MYR 1,950.00.", "2026-11-12T11:20:00+08:00"),
        ("demo-019", "noreply@touchngo.com.my", "Touch 'n Go eWallet", "TNG eWallet Parking Payment", "Parking fee deducted at TRX Exchange. Total: RM 10.00.", "2026-11-13T18:10:00+08:00"),
        ("demo-020", "noreply@rytbank.my", "Ryt Bank", "Ryt Bank DuitNow Transfer Alert", "You transferred RM 35.00 via DuitNow from your Ryt Bank account.", "2026-11-14T10:15:00+08:00"),
        ("demo-021", "noreply@rytbank.my", "Ryt Bank", "Ryt Bank Daily Interest Credited", "Interest credited to your account: RM 4.25.", "2026-11-14T12:00:00+08:00"),
    ]

    def fetch_messages(
        self,
        start_date: date,
        end_date: date,
        sender_emails: list[str] | None = None,
        headers_only: bool = False,
    ) -> list[RawEmail]:
        allowed = {s.lower().strip() for s in sender_emails} if sender_emails else None
        results: list[RawEmail] = []
        for mid, s_email, s_name, subj, body, iso_dt in self._SEED_EMAILS:
            dt = datetime.fromisoformat(iso_dt)
            if dt.date() < start_date or dt.date() > end_date:
                continue
            if allowed is not None and s_email.lower() not in allowed:
                continue
            results.append(
                RawEmail(
                    message_id=mid,
                    sender_email=s_email.lower(),
                    sender_name=s_name,
                    subject=subj,
                    body="" if headers_only else body,
                    received_at=dt,
                )
            )
        return results


class GmailApiEmailProvider:
    """Reads real Google emails via the Gmail REST API using OAuth 2.0 tokens."""

    BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, db: Session, token_row) -> None:
        self.db = db
        self.token_row = token_row

    def _access_token(self) -> str:
        from app.modules.auth.google_oauth import get_valid_google_access_token

        return get_valid_google_access_token(self.db, self.token_row)

    def _parse_gmail_message(self, payload: dict, headers_only: bool, idx: int) -> RawEmail | None:
        thread_id = payload.get("threadId")
        if headers_only:
            hdrs = {
                h["name"].lower(): h["value"]
                for h in payload.get("payload", {}).get("headers", [])
                if "name" in h and "value" in h
            }
            display_name, addr = parseaddr(_decode_mime_str(hdrs.get("from")))
            sender_email = addr.lower().strip()
            if not sender_email:
                return None
            subject = _decode_mime_str(hdrs.get("subject"))
            message_id = (hdrs.get("message-id") or f"gmail-{payload.get('id', idx)}").strip()
            date_hdr = hdrs.get("date")
            try:
                dt = parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            return RawEmail(
                message_id=message_id,
                sender_email=sender_email,
                sender_name=display_name or sender_email.split("@")[0],
                subject=subject,
                body="",
                received_at=dt,
                gmail_thread_id=thread_id,
                attachments=[],
            )

        raw_b64 = payload.get("raw")
        if not raw_b64:
            return None
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        raw_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
        msg = email.message_from_bytes(raw_bytes)
        display_name, addr = parseaddr(_decode_mime_str(msg.get("From")))
        sender_email = addr.lower().strip()
        if not sender_email:
            return None
        subject = _decode_mime_str(msg.get("Subject"))
        message_id = (msg.get("Message-ID") or f"gmail-{payload.get('id', idx)}").strip()
        try:
            dt = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        return RawEmail(
            message_id=message_id,
            sender_email=sender_email,
            sender_name=display_name or sender_email.split("@")[0],
            subject=subject,
            body=_extract_body(msg),
            received_at=dt,
            gmail_thread_id=thread_id,
            attachments=_extract_attachments(msg),
        )

    def fetch_messages(
        self,
        start_date: date,
        end_date: date,
        sender_emails: list[str] | None = None,
        headers_only: bool = False,
    ) -> list[RawEmail]:
        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        after_str = start_date.strftime("%Y/%m/%d")
        before_str = (end_date + timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"after:{after_str} before:{before_str}"
        if sender_emails:
            from_clause = " OR ".join(f"from:{s}" for s in sender_emails)
            query = f"{query} ({from_clause})"

        msg_refs: list[dict] = []
        page_token: str | None = None
        with httpx.Client(timeout=20.0) as client:
            while True:
                params: dict[str, str | int] = {"q": query, "maxResults": 100}
                if page_token:
                    params["pageToken"] = page_token
                list_resp = client.get(f"{self.BASE_URL}/messages", headers=headers, params=params)
                list_resp.raise_for_status()
                data = list_resp.json()
                msg_refs.extend(data.get("messages", []))
                page_token = data.get("nextPageToken")
                if not page_token or len(msg_refs) >= 500:
                    break

            results: list[RawEmail] = []
            fmt = "metadata" if headers_only else "raw"
            for idx, ref in enumerate(msg_refs):
                msg_id = ref.get("id")
                if not msg_id:
                    continue
                msg_params: list[tuple[str, str]] = [("format", fmt)]
                if headers_only:
                    for h in ("From", "Subject", "Date", "Message-ID"):
                        msg_params.append(("metadataHeaders", h))
                msg_resp = client.get(
                    f"{self.BASE_URL}/messages/{msg_id}",
                    headers=headers,
                    params=msg_params,
                )
                if msg_resp.status_code != 200:
                    continue
                parsed = self._parse_gmail_message(msg_resp.json(), headers_only=headers_only, idx=idx)
                if parsed is not None:
                    results.append(parsed)
            return results


def get_email_provider(
    db: Session | None = None,
) -> GmailApiEmailProvider | ImapEmailProvider | DemoEmailProvider:
    if db is not None:
        from app.database import get_current_user_id
        from app.modules.auth.models import GoogleOAuthToken

        uid = db.info.get("user_id") or get_current_user_id()
        token_row = db.scalar(
            select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == uid)
        )
        if token_row is not None and token_row.access_token:
            return GmailApiEmailProvider(db=db, token_row=token_row)

    if settings.email_mode.lower() == "imap":
        return ImapEmailProvider(
            host=settings.imap_host,
            port=settings.imap_port,
            user=settings.imap_user,
            app_password=settings.imap_app_password,
        )
    return DemoEmailProvider()

