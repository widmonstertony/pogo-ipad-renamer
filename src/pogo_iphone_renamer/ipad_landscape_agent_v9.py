from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from . import ipad_landscape_agent_v7 as v7  # noqa: E402
from .local_ocr_v2 import exact_species_from_name_region  # noqa: E402


# The v7 state machine is retained; only its full-screen name recognizer is
# replaced with a high-contrast crop of the actual current-name row.
v7.exact_species_from_mcp_screenshot = exact_species_from_name_region


def main(argv: list[str] | None = None) -> int:
    return v7.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
