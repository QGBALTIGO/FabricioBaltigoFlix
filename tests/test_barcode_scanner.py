import cv2
import numpy as np
import zxingcpp

from app.intake import (
    barcode_runtime_available,
    barcode_tracking_candidates_from_image_bytes,
)


def _png_bytes(
    text: str,
    barcode_format,
    *,
    scale: int = 5,
) -> bytes:
    barcode = zxingcpp.create_barcode(
        text,
        barcode_format,
    )
    image = np.asarray(
        barcode.to_image(
            scale=scale,
        )
    )
    ok, encoded = cv2.imencode(
        ".png",
        image,
    )
    assert ok
    return encoded.tobytes()


def test_barcode_runtime_is_available():
    assert barcode_runtime_available() is True


def test_code128_tracking_label_is_decoded_without_ocr():
    code = "AB123456789BR"
    raw = _png_bytes(
        code,
        zxingcpp.BarcodeFormat.Code128,
    )

    candidates = (
        barcode_tracking_candidates_from_image_bytes(
            raw
        )
    )

    assert candidates
    assert candidates[0].number == code
    assert candidates[0].source.startswith(
        "barcode:"
    )
    assert candidates[0].confidence >= 0.98


def test_qr_url_extracts_tracking_code():
    code = "AB123456789BR"
    raw = _png_bytes(
        (
            "https://exemplo.invalid/rastreio/"
            + code
        ),
        zxingcpp.BarcodeFormat.QRCode,
        scale=8,
    )

    candidates = (
        barcode_tracking_candidates_from_image_bytes(
            raw
        )
    )

    assert any(
        item.number == code
        for item in candidates
    )


def test_numeric_code128_can_represent_shipping_identifier():
    code = "12345678901234"
    raw = _png_bytes(
        code,
        zxingcpp.BarcodeFormat.Code128,
    )

    candidates = (
        barcode_tracking_candidates_from_image_bytes(
            raw
        )
    )

    assert any(
        item.number == code
        for item in candidates
    )


def test_ean_product_barcode_is_not_treated_as_tracking():
    raw = _png_bytes(
        "5901234123457",
        zxingcpp.BarcodeFormat.EAN13,
    )

    candidates = (
        barcode_tracking_candidates_from_image_bytes(
            raw
        )
    )

    assert candidates == []


def test_invalid_image_bytes_return_no_candidates():
    assert (
        barcode_tracking_candidates_from_image_bytes(
            b"not-an-image"
        )
        == []
    )
