from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.utils import (
    is_valid_tracking_number,
    normalize_tracking_number,
)

CORREIOS_RE = re.compile(
    r"\b[A-Z]{2}\s*\d{9}\s*[A-Z]{2}\b",
    re.IGNORECASE,
)
TRACKING_KEYWORD_RE = re.compile(
    r"(?:c[oó]digo(?:\s+de)?\s+(?:rastreio|rastreamento)|"
    r"rastreio|rastreamento|tracking(?:\s*(?:code|number|no))?|"
    r"awb|objeto|remessa|shipment)"
    r"\s*[:#\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-]{4,49})",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(
    r"\b[A-Z0-9][A-Z0-9\-]{7,39}\b",
    re.IGNORECASE,
)
ORDER_RE = re.compile(
    r"\b(?:pedido|order|compra)\s*(?:n[ºo°.]*)?\s*[:#\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-./]{3,39})",
    re.IGNORECASE,
)
PRODUCT_RE = re.compile(
    r"(?:^|\n)\s*(?:produto|item|descri[cç][aã]o)\s*[:\-]\s*"
    r"([^\n]{2,180})",
    re.IGNORECASE,
)

STORE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Shopee", re.compile(r"\bshopee\b", re.IGNORECASE)),
    ("Amazon", re.compile(r"\bamazon\b", re.IGNORECASE)),
    ("AliExpress", re.compile(r"\baliexpress\b", re.IGNORECASE)),
    ("Mercado Livre", re.compile(r"\bmercado\s*livre\b|\bmercadolivre\b", re.IGNORECASE)),
    ("Shein", re.compile(r"\bshein\b", re.IGNORECASE)),
    ("Temu", re.compile(r"\btemu\b", re.IGNORECASE)),
    ("KaBuM!", re.compile(r"\bkabum\b", re.IGNORECASE)),
    ("Magalu", re.compile(r"\bmagalu\b|\bmagazine\s+luiza\b", re.IGNORECASE)),
    ("Casas Bahia", re.compile(r"\bcasas\s+bahia\b", re.IGNORECASE)),
)

STRONG_CONTEXT_RE = re.compile(
    r"\b(rastreio|rastreamento|tracking|awb|transportadora|"
    r"encomenda|remessa|shipment|pacote|objeto)\b",
    re.IGNORECASE,
)

_ocr_engine: Any = None
_ocr_lock = threading.Lock()


@dataclass(frozen=True)
class TrackingCandidate:
    number: str
    confidence: float
    source: str
    store_name: str | None = None
    order_number: str | None = None
    product_name: str | None = None
    nickname: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: str | None) -> str:
    text = str(value or "")
    return (
        text.replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
    )


def _store_name(text: str) -> str | None:
    for label, pattern in STORE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _order_number(text: str) -> str | None:
    match = ORDER_RE.search(text)
    return match.group(1).strip()[:120] if match else None


def _product_name(text: str) -> str | None:
    match = PRODUCT_RE.search(text)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip(" -:•")
    return value[:180] or None


def suggested_nickname(
    *,
    store_name: str | None,
    order_number: str | None,
    product_name: str | None,
) -> str | None:
    if product_name and store_name:
        return f"{store_name} • {product_name}"[:120]
    if product_name:
        return product_name[:120]
    if store_name and order_number:
        return f"{store_name} • Pedido {order_number}"[:120]
    if store_name:
        return store_name[:120]
    return None


def _normalize_candidate(
    raw: str,
    *,
    allow_numeric: bool = False,
) -> str | None:
    raw = re.sub(r"\s+", "", str(raw or ""))
    number = normalize_tracking_number(raw.strip(" .,:;()[]{}"))
    if not is_valid_tracking_number(number):
        return None
    if number.isdigit() and not allow_numeric:
        return None
    if not any(ch.isdigit() for ch in number):
        return None
    if (
        not allow_numeric
        and not any(ch.isalpha() for ch in number)
    ):
        return None
    return number


def extract_tracking_candidates(
    text: str | None,
    *,
    source: str = "message",
    limit: int = 5,
) -> list[TrackingCandidate]:
    clean = _clean_text(text)
    if not clean:
        return []

    store = _store_name(clean)
    order = _order_number(clean)
    product = _product_name(clean)
    nickname = suggested_nickname(
        store_name=store,
        order_number=order,
        product_name=product,
    )

    scores: dict[str, float] = {}

    for match in CORREIOS_RE.finditer(clean):
        number = _normalize_candidate(match.group(0))
        if number:
            scores[number] = max(scores.get(number, 0.0), 1.0)

    for match in TRACKING_KEYWORD_RE.finditer(clean):
        raw = match.group(1)
        # Stop before common sentence separators when OCR/text captured too much.
        raw = re.split(r"[\n,;|]", raw, maxsplit=1)[0]
        number = _normalize_candidate(
            raw,
            allow_numeric=True,
        )
        if number:
            scores[number] = max(scores.get(number, 0.0), 0.96)

    strong_context = bool(STRONG_CONTEXT_RE.search(clean))
    if strong_context:
        for match in TOKEN_RE.finditer(clean):
            number = _normalize_candidate(match.group(0))
            if not number:
                continue
            # Generic codes need a reasonably strong mixed format. This avoids
            # order numbers and timestamps becoming tracking candidates.
            if len(number) < 9:
                continue
            score = 0.80 if store else 0.76
            scores[number] = max(scores.get(number, 0.0), score)

    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )

    return [
        TrackingCandidate(
            number=number,
            confidence=score,
            source=source,
            store_name=store,
            order_number=order,
            product_name=product,
            nickname=nickname,
        )
        for number, score in ordered[: max(1, int(limit))]
    ]


def _barcode_format_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value or "").lower(),
    )


def _barcode_direct_score(
    format_key: str,
    number: str,
) -> float:
    if CORREIOS_RE.fullmatch(number):
        return 1.0

    if "qrcode" in format_key:
        return 0.99

    if format_key in {
        "code128",
        "code39",
        "code93",
    }:
        return 0.98

    return 0.93


def _barcode_allows_numeric(
    format_key: str,
) -> bool:
    # EAN/UPC are usually product barcodes, not shipment identifiers.
    return format_key in {
        "code128",
        "code39",
        "code93",
        "itf",
        "codabar",
    }


def _barcode_tokens(text: str) -> list[str]:
    if not text:
        return []

    values = [text]
    values.extend(
        item
        for item in re.split(
            r"[\s|;,/?#=&:]+",
            text,
        )
        if item
    )

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip(
            " .,:;()[]{}<>"
        )
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def barcode_tracking_candidates_from_image_bytes(
    image_bytes: bytes,
    *,
    limit: int = 5,
) -> list[TrackingCandidate]:
    """Decode QR/1D/2D labels before falling back to OCR."""

    if not image_bytes:
        return []

    import cv2
    import numpy as np
    import zxingcpp

    encoded = np.frombuffer(
        image_bytes,
        dtype=np.uint8,
    )
    image = cv2.imdecode(
        encoded,
        cv2.IMREAD_COLOR,
    )
    if image is None:
        return []

    height, width = image.shape[:2]
    longest = max(
        int(height),
        int(width),
    )
    if longest > 3200:
        scale = 3200.0 / longest
        image = cv2.resize(
            image,
            (
                max(
                    1,
                    int(width * scale),
                ),
                max(
                    1,
                    int(height * scale),
                ),
            ),
            interpolation=cv2.INTER_AREA,
        )

    barcodes = zxingcpp.read_barcodes(
        image,
        try_rotate=True,
        try_downscale=True,
        try_invert=True,
    )

    best: dict[
        str,
        TrackingCandidate,
    ] = {}

    for barcode in barcodes:
        text = str(
            getattr(
                barcode,
                "text",
                "",
            )
            or ""
        ).strip()
        if not text:
            continue

        format_key = _barcode_format_key(
            getattr(
                barcode,
                "format",
                "",
            )
        )
        source = (
            "barcode:"
            + (
                format_key
                or "unknown"
            )
        )

        # QR payloads frequently contain URLs or descriptive text.
        for candidate in extract_tracking_candidates(
            text,
            source=source,
            limit=limit,
        ):
            current = best.get(
                candidate.number
            )
            if (
                current is None
                or candidate.confidence
                > current.confidence
            ):
                best[
                    candidate.number
                ] = candidate

        allow_numeric = (
            _barcode_allows_numeric(
                format_key
            )
        )

        for token in _barcode_tokens(
            text
        ):
            number = _normalize_candidate(
                token,
                allow_numeric=allow_numeric,
            )
            if not number:
                continue

            # Numeric-only values from common retail symbologies are deliberately
            # ignored to avoid treating an EAN/UPC product code as a shipment.
            if (
                number.isdigit()
                and not allow_numeric
            ):
                continue

            score = _barcode_direct_score(
                format_key,
                number,
            )
            candidate = TrackingCandidate(
                number=number,
                confidence=score,
                source=source,
            )
            current = best.get(
                number
            )
            if (
                current is None
                or score
                > current.confidence
            ):
                best[number] = candidate

    ordered = sorted(
        best.values(),
        key=lambda item: (
            -item.confidence,
            -len(item.number),
            item.number,
        ),
    )
    return ordered[
        : max(
            1,
            int(limit),
        )
    ]


def barcode_runtime_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import zxingcpp  # noqa: F401
    except Exception:
        return False
    return True


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine

    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr import RapidOCR

            _ocr_engine = RapidOCR(
                params={
                    "Global.text_score": 0.45,
                    "Global.log_level": "warning",
                }
            )
    return _ocr_engine


def ocr_text_from_image_bytes(
    image_bytes: bytes,
    *,
    suffix: str = ".jpg",
) -> str:
    if not image_bytes:
        return ""

    suffix = suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as handle:
            handle.write(image_bytes)
            temp_path = handle.name

        result = _get_ocr_engine()(temp_path)
        texts = getattr(result, "txts", None) or ()
        return "\n".join(
            str(item).strip()
            for item in texts
            if str(item).strip()
        )
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


def ocr_runtime_available() -> bool:
    if os.getenv("IMAGE_OCR_ENABLED", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    try:
        import rapidocr  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True
