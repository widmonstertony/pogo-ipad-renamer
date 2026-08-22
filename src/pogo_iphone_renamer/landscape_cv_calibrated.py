from __future__ import annotations

from .landscape_cv import IVMeasurement, measure_landscape_appraisal, rotate_mcp_image_upright


def measure_ipad14_6_appraisal(image_base64: str, orientation: str) -> IVMeasurement:
    """Measure IV endpoints using the calibrated iPad14,6 landscape profile."""
    preliminary = measure_landscape_appraisal(image_base64, orientation)
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, _ = image.size
    start = round(width * 0.087)
    full_endpoint = round(width * 0.348)
    full_span = full_endpoint - start + 1

    values: list[int] = []
    confidences: list[float] = []
    for endpoint in preliminary.endpoints:
        raw = 15.0 * (endpoint - start + 1) / full_span
        value = max(0, min(15, round(raw)))
        error = abs(raw - value)
        values.append(value)
        confidences.append(max(0.0, min(1.0, 1.0 - error / 0.35)))

    return IVMeasurement(
        attack=values[0],
        defense=values[1],
        stamina=values[2],
        confidence=min(confidences),
        endpoints=preliminary.endpoints,
        row_centers=preliminary.row_centers,
    )
