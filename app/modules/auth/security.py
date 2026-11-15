import base64
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlparse

# OWASP-aligned scrypt parameters (memory-hard KDF built into Python stdlib)
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_SCRYPT_MAXMEM = 32 * 1024 * 1024  # 32 MiB

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")

_COMMON_WEAK_PASSWORDS = frozenset({
    "password1234",
    "password12345",
    "123456789012",
    "1234567890123",
    "qwertyuiopas",
    "admin1234567",
    "letmein12345",
    "welcome12345",
    "changeme1234",
})


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email_format(email: str) -> str:
    cleaned = normalize_email(email)
    if not cleaned or len(cleaned) > 255 or any(ord(c) < 32 for c in cleaned):
        raise ValueError("Invalid email address.")
    if not _EMAIL_RE.match(cleaned):
        raise ValueError("Invalid email address format.")
    return cleaned


def validate_password_strength(password: str, email: str | None = None) -> None:
    if "\x00" in password:
        raise ValueError("Password contains invalid null bytes.")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters long.")
    if len(password) > 128:
        raise ValueError("Password must be 128 characters or fewer.")
    lowered = password.lower()
    if lowered in _COMMON_WEAK_PASSWORDS:
        raise ValueError("Password is too common. Please choose a stronger password.")
    if email:
        local = normalize_email(email).split("@", 1)[0]
        if len(local) >= 4 and local in lowered:
            raise ValueError("Password must not contain your email address.")
    # Require either a 16+ char passphrase or at least 3 character classes
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if len(password) < 16 and classes < 3:
        raise ValueError(
            "Use at least 3 of: uppercase, lowercase, numbers, symbols (or a 16+ character passphrase)."
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored_hash.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


# Precomputed dummy hash so unknown-user login attempts spend equal CPU/time
DUMMY_PASSWORD_HASH: str = hash_password("dummy-timing-equalizer-!@#987654")


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_email(email: str) -> str:
    cleaned = normalize_email(email)
    if "@" not in cleaned:
        return "***"
    local, domain = cleaned.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def is_safe_redirect_url(target: str | None) -> bool:
    if not target:
        return False
    if any(ord(c) < 32 for c in target):
        return False
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == ""

