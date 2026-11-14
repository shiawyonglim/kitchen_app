"""Local ONNX Runtime OCR for thermal paper receipts (NSK, Jaya Grocer) with zero cloud API fees."""
from __future__ import annotations

import re
from functools import lru_cache
from rapidocr_onnxruntime import RapidOCR

_BOILERPLATE_RE = re.compile(
    r"(?:total|subtotal|sub-total|cash|change|rounding|gst|sst|tax\s*invoice|thank\s*you|"
    r"welcome|tel\s*:|fax\s*:|date\s*:|time\s*:|cashier|receipt\s*no|invoice\s*no|"
    r"nsk\s*trade|jaya\s*grocer|sdn\s*bhd|roc\s*:|visa|mastercard|duitnow|ewallet)",
    re.IGNORECASE,
)

_PURE_NUMBER_OR_PRICE_RE = re.compile(r"^(?:RM|MYR)?\s*[\d.,:/\-*%]+$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _get_ocr_engine() -> RapidOCR:
    return RapidOCR()


def extract_receipt_lines(image_bytes: bytes) -> list[str]:
    """Run local ONNX OCR on a receipt photo and return candidate grocery line items."""
    engine = _get_ocr_engine()
    result, _elapse = engine(image_bytes)
    if not result:
        return []

    lines: list[str] = []
    for item in result:
        if len(item) < 2:
            continue
        text = str(item[1]).strip()
        if len(text) < 3:
            continue
        if _PURE_NUMBER_OR_PRICE_RE.match(text):
            continue
        if _BOILERPLATE_RE.search(text):
            continue
        lines.append(text)
    return lines

