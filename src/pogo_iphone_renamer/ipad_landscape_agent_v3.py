from __future__ import annotations

from . import ipad_landscape_agent as agent
from .landscape_cv_v3 import measure_ipad14_6_appraisal_v3


agent.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v3


def main(argv: list[str] | None = None) -> int:
    return agent.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
