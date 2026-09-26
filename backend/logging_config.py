"""Structured logging for Evo Code.

Log records carry structured context via ``extra={...}`` (for example
``repository_id``, ``agent``, ``stage``). The formatter renders them either as
``key=value`` pairs (text) or as one JSON object per line (json).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class StructuredFormatter(logging.Formatter):
    def __init__(self, json_output: bool = False) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
        if self.json_output:
            payload = {
                "ts": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                **fields,
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)

        extras = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{timestamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if extras:
            line = f"{line} | {extras}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure the ``evo`` logger hierarchy once."""
    logger = logging.getLogger("evo")
    logger.setLevel(level)
    if not any(getattr(h, "_evo_handler", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter(json_output=json_output))
        handler._evo_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
