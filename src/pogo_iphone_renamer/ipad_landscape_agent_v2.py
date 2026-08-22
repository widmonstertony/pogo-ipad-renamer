from __future__ import annotations

from . import ipad_landscape_agent as agent
from .landscape_cv_v2 import measure_ipad14_6_appraisal_v2


# Compatibility adapter: keep the audited navigation/rename state machine and
# replace only its image measurement implementation.
agent.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v2


def main(argv: list[str] | None = None) -> int:
    return agent.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
