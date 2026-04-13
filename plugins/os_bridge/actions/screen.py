from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import mss

    MSS_AVAILABLE = True
except Exception as exc:  # pragma: no cover - import environment dependent
    mss = None
    _mss_error = exc
    MSS_AVAILABLE = False

try:
    from PIL import Image, ImageGrab, ImageOps

    PIL_AVAILABLE = True
except Exception as exc:  # pragma: no cover - import environment dependent
    Image = None
    ImageGrab = None
    ImageOps = None
    _pil_error = exc
    PIL_AVAILABLE = False

try:
    import pytesseract

    TESSERACT_AVAILABLE = True
except Exception as exc:  # pragma: no cover - import environment dependent
    pytesseract = None
    _tesseract_error = exc
    TESSERACT_AVAILABLE = False


def _require_ocr() -> None:
    if not PIL_AVAILABLE:
        raise RuntimeError(f"Pillow is not available: {_pil_error}")
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(f"pytesseract is not available: {_tesseract_error}")


def screenshot(region: tuple[int, int, int, int] | None = None):
    """Capture the current screen and return a PIL image."""
    if MSS_AVAILABLE and PIL_AVAILABLE:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            if region is not None:
                left, top, width, height = region
                monitor = {
                    "left": int(left),
                    "top": int(top),
                    "width": int(width),
                    "height": int(height),
                }
            grabbed = sct.grab(monitor)
            return Image.frombytes("RGB", grabbed.size, grabbed.rgb)

    if PIL_AVAILABLE and ImageGrab is not None:
        if region is not None:
            left, top, width, height = region
            bbox = (int(left), int(top), int(left + width), int(top + height))
            return ImageGrab.grab(bbox=bbox)
        return ImageGrab.grab()

    raise RuntimeError(
        "No screen capture backend available: "
        f"{'mss unavailable' if not MSS_AVAILABLE else ''} "
        f"{'Pillow unavailable' if not PIL_AVAILABLE else ''}"
    )


def extract_text(image: Any | None = None, region: tuple[int, int, int, int] | None = None) -> str:
    """Run OCR over a screenshot or supplied image and return detected text."""
    _require_ocr()
    source = image if image is not None else screenshot(region=region)
    if source is None:
        return ""

    working = source.convert("L")
    working = ImageOps.autocontrast(working)
    text = pytesseract.image_to_string(working)
    return str(text).strip()


def read_screen(region: tuple[int, int, int, int] | None = None) -> dict[str, str]:
    return {"text": extract_text(region=region)}
