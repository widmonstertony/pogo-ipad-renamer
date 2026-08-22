from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from .ipad_landscape_agent_v7 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
