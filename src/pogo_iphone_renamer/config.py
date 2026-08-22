from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BATCH_LIMIT_UNLIMITED = 0
BATCH_LIMIT_MIN = 1


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mcp_url: str
    health_url: str
    protocol_version: str
    pokemon_go_bundle_id: str
    write_enabled: bool
    batch_limit: int
    observation_ttl_seconds: float
    journal_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        mcp_url = os.getenv("IPHONE_MCP_URL", "").strip()
        if not mcp_url:
            raise ValueError("IPHONE_MCP_URL is required")
        health_url = os.getenv("IPHONE_MCP_HEALTH_URL", "").strip()
        if not health_url:
            health_url = mcp_url.removesuffix("/mcp") + "/health"
        bundle_id = os.getenv("POKEMON_GO_BUNDLE_ID", "").strip()
        if not bundle_id:
            raise ValueError("POKEMON_GO_BUNDLE_ID is required")
        batch_limit = int(os.getenv("POGO_BATCH_LIMIT", "20"))
        if batch_limit < BATCH_LIMIT_UNLIMITED:
            raise ValueError("POGO_BATCH_LIMIT must be zero (unlimited) or positive")
        ttl = float(os.getenv("POGO_OBSERVATION_TTL_SECONDS", "20"))
        if ttl < 3 or ttl > 120:
            raise ValueError("POGO_OBSERVATION_TTL_SECONDS must be 3..120")
        return cls(
            mcp_url=mcp_url,
            health_url=health_url,
            protocol_version=os.getenv(
                "IPHONE_MCP_PROTOCOL_VERSION", "2025-11-25"
            ).strip(),
            pokemon_go_bundle_id=bundle_id,
            write_enabled=_bool_env("POGO_WRITE_ENABLED", False),
            batch_limit=batch_limit,
            observation_ttl_seconds=ttl,
            journal_path=Path(
                os.getenv("POGO_JOURNAL_PATH", ".pogo-journal/actions.jsonl")
            ),
        )
