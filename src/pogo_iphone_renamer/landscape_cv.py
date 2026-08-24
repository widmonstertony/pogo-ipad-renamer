from __future__ import annotations

import base64
import io
from contextvars import ContextVar
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class IVMeasurement:
    attack: int
    defense: int
    stamina: int
    confidence: float
    endpoints: tuple[int, int, int]
    row_centers: tuple[int, int, int]


@dataclass(frozen=True)
class StageManagerGeometry:
    """Raw screenshot bounds of the active sideways Pokémon GO window."""

    raw_width: int
    raw_height: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


_PREFERRED_STAGE_MANAGER_GEOMETRY: ContextVar[StageManagerGeometry | None] = (
    ContextVar("preferred_stage_manager_geometry", default=None)
)


def set_preferred_stage_manager_geometry(
    geometry: StageManagerGeometry | None,
) -> None:
    _PREFERRED_STAGE_MANAGER_GEOMETRY.set(geometry)


def _portrait_stage_manager_geometry(image: Image.Image) -> StageManagerGeometry:
    """Find a moved/resized Stage Manager game window from pixel edges.

    The iPad14,6 MCP encodes the landscape desktop as a 1024x1366 portrait
    image.  Stage Manager may place the sideways game at the center, on the
    right beside another app, or nearly maximized.  The old fixed crop only
    worked for the last case.  Window sides are persistent high-contrast
    edges; pairing those edges by the observed window aspect remains stable
    across map, menu, detail, appraisal and rename pages.
    """

    raw_width, raw_height = image.size
    sample_width = 512
    sample_height = max(1, round(raw_height * sample_width / raw_width))
    gray = image.convert("L").resize((sample_width, sample_height))
    pixels = gray.load()

    y_start = round(sample_height * 0.18)
    y_end = round(sample_height * 0.90)
    vertical_scores = [0.0]
    for x in range(1, sample_width):
        score = sum(
            abs(pixels[x, y] - pixels[x - 1, y])
            for y in range(y_start, y_end)
        ) / max(1, y_end - y_start)
        vertical_scores.append(score)

    # Prefer the supervised iPad14,6 layout's measured outer frame before
    # considering generic edge pairs.  Detail pages contain a much stronger
    # inner white card only 10 raw pixels inside the game window; strength-only
    # peak selection locks to that card and shifts the portrait Y anchors by
    # roughly 55 touch points.  The outer frame remains visible within this
    # narrow calibrated band on map, inventory, detail and appraisal screens.
    def calibrated_edge(target_ratio: float) -> int | None:
        target = round(sample_width * target_ratio)
        radius = max(3, round(sample_width * 0.006))
        candidates = [
            x
            for x in range(max(1, target - radius), min(sample_width, target + radius + 1))
            if vertical_scores[x] >= 4.0
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda x: (vertical_scores[x] - 2.0 * abs(x - target), -abs(x - target)),
        )

    calibrated_left = calibrated_edge(0.1035)
    calibrated_right = calibrated_edge(0.5781)
    calibrated_pair = (
        calibrated_left is not None
        and calibrated_right is not None
        and round(sample_width * 0.455)
        <= calibrated_right - calibrated_left
        <= round(sample_width * 0.505)
    )

    ranked = sorted(
        range(round(sample_width * 0.05), round(sample_width * 0.95)),
        key=vertical_scores.__getitem__,
        reverse=True,
    )
    peaks: list[int] = []
    # The detail page has a bright content card roughly 10 raw pixels inside
    # the real Stage Manager window.  Keeping peaks 16 raw pixels apart drops
    # the weaker outer edge and makes the detector lock to that inner card,
    # shifting every portrait anchor by about 55 touch points.  Preserve both
    # nearby candidates so the calibrated outer-width prior can choose safely.
    minimum_separation = max(3, round(sample_width * 0.006))
    for x in ranked:
        if all(abs(x - existing) >= minimum_separation for existing in peaks):
            peaks.append(x)
        if len(peaks) >= 40:
            break

    # The supervised iPad14,6 Stage Manager layout keeps the sideways game at
    # 46.7-47.7% of the encoded desktop width across map, inventory, detail and
    # appraisal pages.  A much wider range admitted strong inner content-card
    # edges (about 45.1%) as if they were the actual window frame.
    minimum_window_width = round(sample_width * 0.455)
    maximum_window_width = round(sample_width * 0.505)
    pairs = [
        (
            min(vertical_scores[left], vertical_scores[right]),
            vertical_scores[left] + vertical_scores[right],
            -abs((left + right) / 2.0 - sample_width / 2.0),
            left,
            right,
        )
        for left in peaks
        for right in peaks
        if minimum_window_width <= right - left <= maximum_window_width
    ]
    if calibrated_pair:
        left, right = calibrated_left, calibrated_right
    elif not pairs:
        raise ValueError("unable to find a Stage Manager game-window edge pair")
    else:
        _, _, _, left, right = max(pairs)

    horizontal_scores = [0.0]
    x_start = left + 4
    x_end = right - 4
    for y in range(1, sample_height):
        score = sum(
            abs(pixels[x, y] - pixels[x, y - 1])
            for x in range(x_start, x_end)
        ) / max(1, x_end - x_start)
        horizontal_scores.append(score)

    bottom = max(
        range(round(sample_height * 0.82), round(sample_height * 0.99)),
        key=horizontal_scores.__getitem__,
    )
    if calibrated_pair:
        # The outer top moves by about 12 raw pixels with the window shadow,
        # while the misleading detail-card top is near raw y=248.  Keep the
        # search around the measured outer-frame band so the inner card cannot
        # win merely because it has greater contrast.
        top_start = round(sample_height * 0.13)
        top_end = round(sample_height * 0.16)
    else:
        predicted_top = bottom - 2.34 * (right - left)
        # The active Pokémon GO window is currently about 2.34:1 in the MCP's
        # portrait encoding.  The previous 2.40 prediction put the real top
        # edge just outside a 2% search band when Stage Manager widened the
        # window by a few points.
        top_search_radius = sample_height * 0.02
        top_start = max(
            round(sample_height * 0.01),
            round(predicted_top - top_search_radius),
        )
        top_end = min(
            round(sample_height * 0.40),
            round(predicted_top + top_search_radius),
        )
    if top_start >= top_end:
        raise ValueError("invalid Stage Manager game-window top search range")
    top = max(range(top_start, top_end + 1), key=horizontal_scores.__getitem__)

    scale = raw_width / sample_width
    raw_left, raw_top, raw_right, raw_bottom = (
        round(value * scale) for value in (left, top, right, bottom)
    )
    geometry = StageManagerGeometry(
        raw_width=raw_width,
        raw_height=raw_height,
        left=raw_left,
        top=raw_top,
        right=raw_right,
        bottom=raw_bottom,
    )
    # An adjacent Stage Manager card may cover the lower portion of this
    # already-calibrated Pokémon GO window.  In that layout the two outer
    # vertical edges still match the iPad14,6 calibration exactly, but the
    # visible height contracts to about 1.98:1.  Accept that narrower ratio
    # only for the calibrated edge pair; generic edge-pair detection retains
    # the stricter 2.15:1 lower bound to avoid selecting an unrelated card.
    minimum_aspect_ratio = 1.90 if calibrated_pair else 2.15
    minimum_bottom_ratio = 0.82 if calibrated_pair else 0.90
    if not (
        geometry.width > 0
        and geometry.height > 0
        and minimum_aspect_ratio <= geometry.height / geometry.width <= 2.60
        and geometry.bottom >= round(raw_height * minimum_bottom_ratio)
    ):
        raise ValueError("detected Stage Manager game-window geometry is unsafe")
    return geometry


def stage_manager_geometry(
    image: Image.Image,
    *,
    use_preferred: bool = True,
) -> StageManagerGeometry:
    preferred = _PREFERRED_STAGE_MANAGER_GEOMETRY.get() if use_preferred else None
    if (
        preferred is not None
        and preferred.raw_width == image.width
        and preferred.raw_height == image.height
        and 0 <= preferred.left < preferred.right <= image.width
        and 0 <= preferred.top < preferred.bottom <= image.height
    ):
        return preferred
    if image.width < image.height:
        return _portrait_stage_manager_geometry(image)
    # Native-landscape screenshots were emitted by older MCP builds.  Keep
    # their verified nearly-maximized geometry as a conservative fallback;
    # blackframe4 on the target iPad uses the dynamic portrait-encoded path.
    return StageManagerGeometry(
        raw_width=image.width,
        raw_height=image.height,
        left=round(image.width * 0.0242),
        top=round(image.height * 0.2334),
        right=round(image.width * 0.9773),
        bottom=round(image.height * 0.7666),
    )


def _fixed_stage_manager_geometry(image: Image.Image) -> StageManagerGeometry:
    if image.width < image.height:
        return StageManagerGeometry(
            raw_width=image.width,
            raw_height=image.height,
            left=round(image.width * 0.2334),
            top=round(image.height * 0.0242),
            right=round(image.width * 0.7666),
            bottom=round(image.height * 0.9773),
        )
    return StageManagerGeometry(
        raw_width=image.width,
        raw_height=image.height,
        left=round(image.width * 0.0242),
        top=round(image.height * 0.2334),
        right=round(image.width * 0.9773),
        bottom=round(image.height * 0.7666),
    )


def stage_manager_geometry_from_base64(image_base64: str) -> StageManagerGeometry:
    image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("RGB")
    return stage_manager_geometry(image)


def stage_manager_upright_ratio_to_touch(
    geometry: StageManagerGeometry,
    observation_width: float,
    observation_height: float,
    x_ratio: float,
    y_ratio: float,
) -> tuple[float, float]:
    """Map canonical portrait ratios through the exact detected game frame."""

    if geometry.raw_width < geometry.raw_height:
        # The portrait-encoded MCP frame is the physical landscape screen
        # rotated counter-clockwise.  The sideways game is additionally
        # upside down inside its Stage Manager window.
        raw_x = geometry.right - geometry.width * x_ratio
        raw_y = geometry.bottom - geometry.height * y_ratio
        return (
            raw_y * observation_width / geometry.raw_height,
            (geometry.raw_width - raw_x)
            * observation_height
            / geometry.raw_width,
        )
    return (
        geometry.left + geometry.width * (1.0 - y_ratio),
        geometry.top + geometry.height * x_ratio,
    )


def rotate_mcp_image_upright(image_base64: str, orientation: str) -> Image.Image:
    image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("RGB")
    if orientation == "STAGE_MANAGER_MAXIMIZED":
        # A portrait-only game in iPad Stage Manager is rendered sideways in
        # a maximized landscape window.  Normalize only that verified game
        # frame back to the game's *portrait* coordinate system.  OCR, IV CV,
        # pencil localization and touch mapping must all share this space.
        try:
            geometry = stage_manager_geometry(image)
        except ValueError:
            # Read-only black-frame and perceptual-change checks also pass
            # deliberately featureless synthetic/real frames here.  Keep a
            # deterministic crop for those checks; write paths separately
            # require a successfully detected geometry before any touch.
            geometry = _fixed_stage_manager_geometry(image)
        if image.width < image.height:
            # ios-mcp's portrait-encoded screenshot is the landscape screen
            # rotated once; the sideways game surface is therefore upside
            # down in this encoding.
            return (
                image.crop(
                    (
                        geometry.left,
                        geometry.top,
                        geometry.right,
                        geometry.bottom,
                    )
                )
                .rotate(180)
                .resize((1024, 1366))
            )
        return (
            image.crop(
                (
                    geometry.left,
                    geometry.top,
                    geometry.right,
                    geometry.bottom,
                )
            )
            .rotate(90, expand=True)
            .resize((1024, 1366))
        )
    if orientation == "AUTO_LANDSCAPE":
        # ios-mcp 1.2.3-blackframe1 returns the native 1366x1024 landscape
        # frame.  Older builds encoded the same viewport as 1024x1366 and
        # required a counter-clockwise correction.  Supporting both here lets
        # a running batch survive an MCP upgrade/restart without applying a
        # second, destructive coordinate transform to already-upright frames.
        return image.rotate(90, expand=True) if image.width < image.height else image
    if orientation == "ROTATED_90_COUNTERCLOCKWISE":
        # The current iPad MCP labels the visual direction from the touch-space
        # perspective. Its encoded screenshot needs a 90-degree CCW pixel rotation.
        return image.rotate(90, expand=True)
    if orientation == "ROTATED_90_CLOCKWISE":
        return image.rotate(-90, expand=True)
    return image


def image_to_base64_jpeg(image: Image.Image, quality: int = 92) -> str:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _is_attack_fill(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return 165 <= red <= 245 and red - green >= 48 and red - blue >= 38


def _is_gold_fill(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red >= 175 and 105 <= green <= 205 and blue <= 155 and red - green >= 34 and green - blue >= 34


def _row_score(
    image: Image.Image,
    y: int,
    x_start: int,
    x_end: int,
    predicate,
) -> int:
    return sum(1 for x in range(x_start, x_end + 1) if predicate(image.getpixel((x, y))))


def _measure_bar(
    image: Image.Image,
    *,
    y_min_ratio: float,
    y_max_ratio: float,
    predicate,
) -> tuple[int, int, float]:
    width, height = image.size
    x_start = round(width * 0.087)
    x_end = round(width * 0.352)
    y_start = round(height * y_min_ratio)
    y_end = round(height * y_max_ratio)
    scored = [
        (_row_score(image, y, x_start, x_end, predicate), y)
        for y in range(y_start, y_end + 1)
    ]
    best_score, center_y = max(scored)
    if best_score < (x_end - x_start) * 0.08:
        raise ValueError("IV colored fill was not found in the expected appraisal region")

    colored: list[int] = []
    for x in range(x_start, x_end + 1):
        votes = sum(
            1
            for y in range(max(0, center_y - 3), min(height, center_y + 4))
            if predicate(image.getpixel((x, y)))
        )
        if votes >= 4:
            colored.append(x)
    if not colored:
        raise ValueError("IV bar endpoint could not be measured")

    endpoint = max(colored)
    span = x_end - x_start
    raw_value = 15.0 * (endpoint - x_start + 1) / span
    value = max(0, min(15, round(raw_value)))
    endpoint_error = abs(raw_value - value)
    coverage = min(1.0, best_score / max(1.0, span * max(value, 1) / 15.0))
    confidence = max(0.0, min(1.0, 1.0 - endpoint_error / 0.5)) * coverage
    return value, endpoint, confidence


def measure_landscape_appraisal(image_base64: str, orientation: str) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if width <= height:
        raise ValueError(f"expected an upright landscape appraisal image, got {width}x{height}")

    attack, attack_end, attack_conf = _measure_bar(
        image,
        y_min_ratio=0.700,
        y_max_ratio=0.735,
        predicate=_is_attack_fill,
    )
    defense, defense_end, defense_conf = _measure_bar(
        image,
        y_min_ratio=0.750,
        y_max_ratio=0.790,
        predicate=_is_gold_fill,
    )
    stamina, stamina_end, stamina_conf = _measure_bar(
        image,
        y_min_ratio=0.800,
        y_max_ratio=0.840,
        predicate=_is_gold_fill,
    )

    def best_row(y_min_ratio: float, y_max_ratio: float, predicate) -> int:
        x_start = round(width * 0.087)
        x_end = round(width * 0.352)
        rows = range(round(height * y_min_ratio), round(height * y_max_ratio) + 1)
        return max(rows, key=lambda y: _row_score(image, y, x_start, x_end, predicate))

    return IVMeasurement(
        attack=attack,
        defense=defense,
        stamina=stamina,
        confidence=min(attack_conf, defense_conf, stamina_conf),
        endpoints=(attack_end, defense_end, stamina_end),
        row_centers=(
            best_row(0.700, 0.735, _is_attack_fill),
            best_row(0.750, 0.790, _is_gold_fill),
            best_row(0.800, 0.840, _is_gold_fill),
        ),
    )
