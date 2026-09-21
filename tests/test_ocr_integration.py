import re

import cv2
import numpy as np

from app.intake import (
    extract_tracking_candidates,
    ocr_text_from_image_bytes,
)


def test_real_ocr_reads_tracking_code_from_generated_image():
    code = "AB123456789BR"

    image = np.full(
        (260, 1400, 3),
        255,
        dtype=np.uint8,
    )
    cv2.putText(
        image,
        "CODIGO DE RASTREIO",
        (45, 85),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.5,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        code,
        (45, 190),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.4,
        (0, 0, 0),
        5,
        cv2.LINE_AA,
    )

    ok, encoded = cv2.imencode(".png", image)
    assert ok

    text = ocr_text_from_image_bytes(
        encoded.tobytes(),
        suffix=".png",
    )
    compact = re.sub(
        r"[^A-Z0-9]",
        "",
        text.upper(),
    )

    assert code in compact

    candidates = extract_tracking_candidates(
        text,
        source="ocr_integration",
    )
    assert candidates
    assert candidates[0].number == code
