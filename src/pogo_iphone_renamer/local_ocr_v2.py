from __future__ import annotations

from PIL import ImageEnhance

from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import OCRLine, exact_species_from_lines, ocr_image, rename_dialog_visible


def exact_species_from_name_region(
    image_base64: str, orientation: str, *, minimum_confidence: float = 0.85
) -> tuple[str, float]:
    """Read only the centered current-name row, excluding appraisal dialogue."""
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    region = image.crop(
        (
            round(width * 0.30),
            round(height * 0.43),
            round(width * 0.70),
            round(height * 0.61),
        )
    )
    region = region.resize((region.width * 2, region.height * 2))
    region = ImageEnhance.Contrast(region).enhance(1.8)
    return exact_species_from_lines(
        ocr_image(region), minimum_confidence=minimum_confidence
    )
