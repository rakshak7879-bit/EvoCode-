"""Runtime configuration for the Evo Code backend.

Settings come only from environment variables (optionally loaded from a `.env`
file in the project root or `backend/`). Analyzed repositories can never change
configuration: repository files are treated as data, never as settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_CORS = ("http://localhost:5173", "http://127.0.0.1:5173")


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from ``path`` without overriding real environment variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_env_str(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(_env_str(name, str(default))))
    except ValueError:
        return default


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    data_dir: Path
    demo_repo_path: Path
    llm_provider: str = "auto"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 45.0
    max_upload_mb: int = 50
    max_extracted_mb: int = 200
    max_file_kb: int = 512
    max_files: int = 3000
    pacing_ms: int = 350
    agent_timeout_seconds: float = 120.0
    allow_source_edits: bool = True
    simulate_agent_failure: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = DEFAULT_CORS
    github_token: str = field(default="", repr=False)
    log_level: str = "INFO"
    log_format: str = "text"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "evo.db"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_extracted_bytes(self) -> int:
        return self.max_extracted_mb * 1024 * 1024

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_kb * 1024

    def with_overrides(self, **changes: object) -> "Settings":
        return replace(self, **changes)  # type: ignore[arg-type]


def load_settings() -> Settings:
    """Build settings from the environment (after loading optional `.env` files)."""
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(BACKEND_DIR / ".env")
    return Settings(
        data_dir=_resolve_path(_env_str("EVO_DATA_DIR", ".evo-data")),
        demo_repo_path=_resolve_path(_env_str("EVO_DEMO_REPO", "demo-repo")),
        llm_provider=_env_str("EVO_LLM_PROVIDER", "auto").lower(),
        openai_api_key=_env_str("OPENAI_API_KEY"),
        openai_model=_env_str("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=_env_str("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_timeout_seconds=_env_float("EVO_LLM_TIMEOUT_SECONDS", 45.0, 1.0),
        max_upload_mb=_env_int("EVO_MAX_UPLOAD_MB", 50, 1),
        max_extracted_mb=_env_int("EVO_MAX_EXTRACTED_MB", 200, 1),
        max_file_kb=_env_int("EVO_MAX_FILE_KB", 512, 1),
        max_files=_env_int("EVO_MAX_FILES", 3000, 1),
        pacing_ms=_env_int("EVO_PACING_MS", 350, 0),
        agent_timeout_seconds=_env_float("EVO_AGENT_TIMEOUT_SECONDS", 120.0, 1.0),
        allow_source_edits=_env_bool("EVO_ALLOW_SOURCE_EDITS", True),
        simulate_agent_failure=tuple(a.lower() for a in _env_list("EVO_SIMULATE_AGENT_FAILURE")),
        cors_origins=_env_list("EVO_CORS_ORIGINS", DEFAULT_CORS),
        github_token=_env_str("GITHUB_TOKEN"),
        log_level=_env_str("EVO_LOG_LEVEL", "INFO").upper(),
        log_format=_env_str("EVO_LOG_FORMAT", "text").lower(),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
