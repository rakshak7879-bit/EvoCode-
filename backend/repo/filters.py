"""File classification and ignore rules for repository ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__", "dist",
        "build", "coverage", ".next", ".nuxt", ".svelte-kit", ".cache", ".parcel-cache",
        ".turbo", ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        ".gradle", "target", "vendor", "bower_components", "htmlcov", ".terraform",
        ".evo-data", "__MACOSX",
    }
)

SAFE_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})
SENSITIVE_NAMES = frozenset(
    {
        ".env", ".envrc", ".npmrc", ".pypirc", ".netrc", ".git-credentials", "id_rsa",
        "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json", "secrets.json",
        "service-account.json",
    }
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
GENERATED_NAMES = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
        "composer.lock", "cargo.lock", "go.sum", "bun.lockb", ".ds_store", "thumbs.db",
    }
)
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".bundle.js", ".chunk.js")

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".mts": "typescript", ".cts": "typescript", ".vue": "vue", ".svelte": "svelte",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".rb": "ruby", ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".swift": "swift", ".m": "objective-c",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".sql": "sql",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".md": "markdown", ".mdx": "markdown", ".rst": "restructuredtext", ".txt": "text",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
    ".cfg": "ini", ".conf": "ini", ".xml": "xml", ".gradle": "gradle",
    ".properties": "properties", ".graphql": "graphql", ".gql": "graphql",
    ".prisma": "prisma", ".proto": "protobuf", ".tf": "terraform", ".dart": "dart",
    ".lua": "lua", ".r": "r", ".ex": "elixir", ".exs": "elixir",
}
FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "dockerfile", "makefile": "makefile", "procfile": "procfile",
    "gemfile": "ruby", "rakefile": "ruby", "jenkinsfile": "groovy",
    ".env.example": "dotenv", ".env.sample": "dotenv", ".env.template": "dotenv",
    ".env.dist": "dotenv", ".gitignore": "ignore", ".dockerignore": "ignore",
    ".editorconfig": "ini", "license": "text", "pipfile": "toml",
}
DOC_LANGUAGES = frozenset({"markdown", "restructuredtext", "text"})
CONFIG_LANGUAGES = frozenset(
    {
        "json", "yaml", "toml", "ini", "xml", "properties", "dotenv", "ignore", "dockerfile",
        "makefile", "procfile", "gradle", "terraform", "groovy",
    }
)
MANIFEST_NAMES = frozenset(
    {
        "package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml",
        "setup.py", "setup.cfg", "pipfile", "go.mod", "cargo.toml", "pom.xml",
        "build.gradle", "build.gradle.kts", "gemfile", "composer.json", "environment.yml",
    }
)
ENTRYPOINT_NAMES = frozenset(
    {
        "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "server.py", "server.js",
        "app.js", "index.js", "main.js", "server.ts", "app.ts", "index.ts", "main.ts",
        "main.go", "main.rs", "program.cs",
    }
)
_TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|_test\.go$|Test\.java$)"
)
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


@dataclass(frozen=True)
class FileClass:
    language: str
    kind: str  # source | doc | config | manifest
    tags: tuple[str, ...]


def is_sensitive_file(name: str) -> bool:
    lower = name.lower()
    if lower in SAFE_ENV_TEMPLATES:
        return False
    if lower in SENSITIVE_NAMES or lower.startswith(".env."):
        return True
    return lower.endswith(SENSITIVE_SUFFIXES)


def is_generated_file(name: str) -> bool:
    lower = name.lower()
    return lower in GENERATED_NAMES or lower.endswith(GENERATED_SUFFIXES)


def _looks_like_test(path: PurePosixPath) -> bool:
    if any(part.lower() in _TEST_DIRS for part in path.parts[:-1]):
        return True
    return bool(_TEST_NAME.search(path.name))


def classify_file(relpath: str) -> FileClass | None:
    """Return the language/kind/tags of a repository file, or None if unsupported."""
    pure = PurePosixPath(relpath)
    lower = pure.name.lower()
    suffix = pure.suffix.lower()

    language = FILENAME_LANGUAGES.get(lower)
    if language is None and (lower.startswith("dockerfile") or lower.endswith(".dockerfile")):
        language = "dockerfile"
    if language is None:
        language = EXTENSION_LANGUAGES.get(suffix)
    if language is None and lower.startswith("readme"):
        language = "text"
    if language is None:
        return None

    tags: list[str] = []
    if lower.startswith("readme"):
        tags.append("readme")
    if lower in MANIFEST_NAMES or (lower.startswith("requirements") and suffix == ".txt"):
        tags.append("manifest")
    if language == "dockerfile":
        tags.append("dockerfile")
    if lower.startswith(("docker-compose", "compose.")) and suffix in {".yml", ".yaml"}:
        tags.append("compose")
    if "/.github/workflows/" in f"/{relpath}" or lower in {".gitlab-ci.yml", "jenkinsfile"}:
        tags.append("ci")
    if lower in ENTRYPOINT_NAMES:
        tags.append("entrypoint")
    if _looks_like_test(pure):
        tags.append("test")

    if "manifest" in tags:
        kind = "manifest"
    elif language in DOC_LANGUAGES:
        kind = "doc"
    elif language in CONFIG_LANGUAGES:
        kind = "config"
        tags.append("config")
    else:
        kind = "source"
    return FileClass(language=language, kind=kind, tags=tuple(tags))
