from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from pogo_iphone_renamer.gui_appraisal import main


if __name__ == "__main__":
    raise SystemExit(main())

