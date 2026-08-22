from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatchPauseFile:
    """A cross-process pause request owned by the desktop GUI."""

    path: Path

    @property
    def requested(self) -> bool:
        return self.path.is_file()

    def request(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("pause\n", encoding="utf-8")
        temporary.replace(self.path)

    def resume(self) -> None:
        self.path.unlink(missing_ok=True)
