"""Evidence-backed architecture facts: stack, import graph, modules, important files.

Every stack claim carries citations (file + line) so the Brain can verify it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from agents.context import AgentContext, SourceFile

JS_PACKAGES: dict[str, tuple[str, str]] = {
    "react": ("frontend", "React"), "react-dom": ("frontend", "React"), "next": ("frontend", "Next.js"),
    "vue": ("frontend", "Vue"), "nuxt": ("frontend", "Nuxt"), "svelte": ("frontend", "Svelte"),
    "@angular/core": ("frontend", "Angular"), "solid-js": ("frontend", "Solid"),
    "express": ("backend", "Express"), "koa": ("backend", "Koa"), "fastify": ("backend", "Fastify"),
    "@nestjs/core": ("backend", "NestJS"), "@hapi/hapi": ("backend", "hapi"), "hono": ("backend", "Hono"),
    "pg": ("database", "PostgreSQL"), "postgres": ("database", "PostgreSQL"), "mysql": ("database", "MySQL"),
    "mysql2": ("database", "MySQL"), "mongodb": ("database", "MongoDB"), "mongoose": ("database", "MongoDB (Mongoose)"),
    "sqlite3": ("database", "SQLite"), "better-sqlite3": ("database", "SQLite"),
    "@prisma/client": ("database", "Prisma ORM"), "sequelize": ("database", "Sequelize ORM"),
    "typeorm": ("database", "TypeORM"), "knex": ("database", "Knex"), "redis": ("cache", "Redis"),
    "ioredis": ("cache", "Redis"), "jsonwebtoken": ("authentication", "JWT (jsonwebtoken)"),
    "passport": ("authentication", "Passport"), "bcrypt": ("authentication", "bcrypt"),
    "bcryptjs": ("authentication", "bcrypt"), "express-session": ("authentication", "Server sessions"),
    "next-auth": ("authentication", "NextAuth"), "jose": ("authentication", "JWT (jose)"),
    "stripe": ("integrations", "Stripe"), "axios": ("integrations", "Axios HTTP client"),
}
PY_PACKAGES: dict[str, tuple[str, str]] = {
    "fastapi": ("backend", "FastAPI"), "flask": ("backend", "Flask"), "django": ("backend", "Django"),
    "starlette": ("backend", "Starlette"), "aiohttp": ("backend", "aiohttp"),
    "sqlalchemy": ("database", "SQLAlchemy"), "psycopg2": ("database", "PostgreSQL"),
    "psycopg": ("database", "PostgreSQL"), "asyncpg": ("database", "PostgreSQL"), "pymongo": ("database", "MongoDB"),
    "motor": ("database", "MongoDB"), "sqlite3": ("database", "SQLite"), "peewee": ("database", "Peewee ORM"),
    "redis": ("cache", "Redis"), "celery": ("infrastructure", "Celery workers"),
    "pyjwt": ("authentication", "JWT (PyJWT)"), "jwt": ("authentication", "JWT"),
    "python-jose": ("authentication", "JWT (python-jose)"), "passlib": ("authentication", "passlib"),
    "bcrypt": ("authentication", "bcrypt"), "stripe": ("integrations", "Stripe"),
    "requests": ("integrations", "requests HTTP client"), "httpx": ("integrations", "httpx HTTP client"),
    "pandas": ("data", "pandas"), "numpy": ("data", "NumPy"), "torch": ("ml", "PyTorch"),
    "tensorflow": ("ml", "TensorFlow"), "scikit-learn": ("ml", "scikit-learn"), "openai": ("ml", "OpenAI SDK"),
}
LAYER_ORDER = ("frontend", "backend", "database", "authentication", "integrations", "cache", "infrastructure",
               "data", "ml")
_RESOLVE_SUFFIXES = ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", "/index.js", "/index.ts",
                     "/index.tsx")
_DOM_LINE = re.compile(r"\b(document\.|window\.|localStorage\.)")
AUTH_NAME = re.compile(r"(?i)(auth|login|logout|jwt|token|session|password|signin|signup|register|verify)")
SHARED_DIRS = frozenset({"utils", "util", "lib", "libs", "shared", "common", "helpers", "helper"})


class Claims:
    """Collects layer -> values + citations."""

    def __init__(self) -> None:
        self.values: dict[str, list[str]] = {}
        self.evidence: dict[str, list[dict[str, Any]]] = {}

    def add(self, layer: str, value: str, file: str, line: int, label: str) -> None:
        values = self.values.setdefault(layer, [])
        if value not in values:
            values.append(value)
        evidence = self.evidence.setdefault(layer, [])
        if len(evidence) < 4 and not any(e["file"] == file and e["line_start"] == line for e in evidence):
            evidence.append({"file": file, "line_start": line, "line_end": line, "label": label})

    def has(self, layer: str) -> bool:
        return bool(self.values.get(layer))


def _line_of(file: SourceFile, needle: str) -> int:
    for number, line in enumerate(file.lines, start=1):
        if needle in line:
            return number
    return 1


def _package_root(module: str) -> str:
    if module.startswith("@"):
        return "/".join(module.split("/")[:2])
    return module.split("/")[0].split(".")[0]


def resolve_import(importer: str, module: str, known: set[str], language: str) -> str | None:
    base = PurePosixPath(importer).parent
    if language == "python":
        if module.startswith("."):
            depth = len(module) - len(module.lstrip("."))
            parent = base
            for _ in range(depth - 1):
                parent = parent.parent
            stem = module.lstrip(".").replace(".", "/")
            candidates = [f"{parent / stem}.py" if stem else None, f"{parent / stem}/__init__.py" if stem else None]
        else:
            stem = module.replace(".", "/")
            candidates = [f"{stem}.py", f"{stem}/__init__.py"]
        for candidate in candidates:
            if candidate and candidate.lstrip("./") in known:
                return candidate.lstrip("./")
        return None
    if not module.startswith("."):
        return None
    joined = PurePosixPath(base, module).as_posix()
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    target = "/".join(parts)
    for suffix in _RESOLVE_SUFFIXES:
        if f"{target}{suffix}" in known:
            return f"{target}{suffix}"
    return None


def build_import_graph(context: AgentContext) -> dict[str, set[str]]:
    known = set(context.by_path)
    graph: dict[str, set[str]] = {}
    for file in context.source_files:
        if not file.parsed:
            continue
        targets = {
            resolved
            for ref in file.parsed.imports
            if (resolved := resolve_import(file.path, ref.module, known, file.language)) and resolved != file.path
        }
        graph[file.path] = targets
    return graph


def detect_stack(context: AgentContext) -> dict[str, dict[str, Any]]:
    claims = Claims()
    for manifest in context.manifests:
        name = manifest.name.lower()
        if name == "package.json":
            try:
                data = json.loads(manifest.text)
            except ValueError:
                data = {}
            deps: dict[str, Any] = {}
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key])
            for dep in deps:
                if dep in JS_PACKAGES:
                    layer, value = JS_PACKAGES[dep]
                    claims.add(layer, value, manifest.path, _line_of(manifest, f'"{dep}"'), f"dependency {dep}")
        elif name.startswith("requirements") or name in {"pyproject.toml", "pipfile", "setup.py", "setup.cfg"}:
            for number, line in enumerate(manifest.lines, start=1):
                match = re.match(r"""^\s*["']?([A-Za-z0-9_.\-]+)""", line)
                if not match:
                    continue
                package = match.group(1).lower()
                if package in PY_PACKAGES and (name.startswith("requirements") or re.search(r"""["'=<>]""", line)):
                    layer, value = PY_PACKAGES[package]
                    claims.add(layer, value, manifest.path, number, f"dependency {package}")

    for file in context.source_files:
        if not file.parsed:
            continue
        table = PY_PACKAGES if file.language == "python" else JS_PACKAGES
        for ref in file.parsed.imports:
            root = _package_root(ref.module)
            if root in table:
                layer, value = table[root]
                claims.add(layer, value, file.path, ref.line, f"imports {root}")
        if file.parsed.data_access and not claims.has("database"):
            access = file.parsed.data_access[0]
            label = "SQL database" if access.kind == "sql" else "Document database"
            claims.add("database", label, file.path, access.line, "data access call")

    if not claims.has("frontend"):
        for file in context.source_files:
            if file.parsed and file.parsed.uses_dom and not file.parsed.routes:
                line = next((i for i, text in enumerate(file.lines, start=1) if _DOM_LINE.search(text)), 1)
                claims.add("frontend", "Vanilla JavaScript (browser DOM)", file.path, line, "DOM API usage")
    if not claims.has("backend"):
        for file in context.source_files:
            if file.parsed and file.parsed.routes:
                route = file.parsed.routes[0]
                claims.add("backend", "HTTP API", file.path, route.line, f"route {route.method} {route.path}")
                break

    route_languages = Counter(f.language for f in context.source_files if f.parsed and f.parsed.routes)
    runtime = None
    if route_languages:
        language = route_languages.most_common(1)[0][0]
        runtime = {"javascript": "Node.js", "typescript": "Node.js", "python": "Python", "go": "Go"}.get(language)

    stack: dict[str, dict[str, Any]] = {}
    for layer in LAYER_ORDER:
        values = claims.values.get(layer)
        if not values:
            continue
        value = " · ".join(values[:3])
        if layer == "backend" and runtime and runtime not in value:
            value = f"{runtime} · {value}"
        stack[layer] = {"value": value, "values": values, "evidence": claims.evidence.get(layer, [])}
    return stack


def _module_key(file: SourceFile, depth: int) -> str:
    parts = PurePosixPath(file.path).parts
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


def map_modules(context: AgentContext) -> list[dict[str, Any]]:
    files = [f for f in context.files if f.kind in {"source", "doc", "config", "manifest"}]
    tops = Counter(_module_key(f, 1) for f in context.source_files)
    depth = 2 if tops and tops.most_common(1)[0][1] / max(1, len(context.source_files)) > 0.7 and len(tops) == 1 else 1
    groups: dict[str, list[SourceFile]] = {}
    for file in files:
        groups.setdefault(_module_key(file, depth), []).append(file)

    modules = []
    for name, members in sorted(groups.items(), key=lambda item: (item[0] == "(root)", item[0])):
        sources = [m for m in members if m.parsed]
        symbols = [s for m in sources for s in m.parsed.symbols if s.kind != "class"]  # type: ignore[union-attr]
        routes = [r for m in sources for r in m.parsed.routes]  # type: ignore[union-attr]
        lowered = name.lower().split("/")[-1]
        if lowered in {"frontend", "client", "web", "ui", "public", "components", "pages"} or (
            sources and sum(1 for m in sources if m.parsed and m.parsed.uses_dom) > len(sources) / 2
        ):
            role = "Frontend (browser UI)"
        elif routes:
            role = "Backend / API"
        elif lowered in SHARED_DIRS:
            role = "Shared utilities"
        elif lowered in {"tests", "test", "__tests__", "spec"}:
            role = "Tests"
        elif lowered in {"db", "database", "models", "migrations", "schema"}:
            role = "Data layer"
        elif lowered in {"docs", "doc"}:
            role = "Documentation"
        elif name == "(root)":
            role = "Project root (docs & configuration)"
        else:
            role = "Application code"
        description = f"{len(members)} file(s) · {len(symbols)} function(s)"
        if routes:
            description += f" · {len(routes)} route(s)"
        modules.append(
            {
                "name": name,
                "role": role,
                "files": [m.path for m in members],
                "symbols": [s.name for s in symbols[:8]],
                "routes": len(routes),
                "description": description,
            }
        )
    return modules


def detect_capabilities(context: AgentContext) -> dict[str, dict[str, Any]]:
    patterns = {
        "authentication": AUTH_NAME,
        "payments": re.compile(r"(?i)(payment|checkout|charge|billing|stripe|invoice|card)"),
        "validation": re.compile(r"(?i)(validat|is[A-Z]?valid|sanitiz|check[A-Z])"),
    }
    capabilities: dict[str, dict[str, Any]] = {}
    for capability, pattern in patterns.items():
        symbols = []
        files: list[str] = []
        for file in context.source_files:
            if not file.parsed:
                continue
            matched = [s for s in file.parsed.symbols if pattern.search(s.name)]
            route_hits = [r for r in file.parsed.routes if pattern.search(r.path)]
            if matched or route_hits or pattern.search(PurePosixPath(file.path).stem):
                files.append(file.path)
            symbols.extend(
                {"name": s.name, "file": s.file, "line_start": s.line_start, "line_end": s.line_end} for s in matched
            )
        if files:
            capabilities[capability] = {"files": files, "symbols": symbols[:12]}
    data_files = [f.path for f in context.source_files if f.parsed and f.parsed.data_access]
    if data_files:
        capabilities["database"] = {"files": data_files, "symbols": []}
    api_files = [f.path for f in context.source_files if f.parsed and f.parsed.routes]
    if api_files:
        capabilities["api"] = {"files": api_files, "symbols": []}
    return capabilities


def rank_important_files(context: AgentContext, inbound: Counter[str], limit: int = 8) -> list[dict[str, Any]]:
    main_entry = None
    for manifest in context.manifests:
        if manifest.name == "package.json":
            try:
                main_entry = json.loads(manifest.text).get("main")
            except ValueError:
                main_entry = None
    ranked = []
    for file in context.files:
        score = 0.0
        reasons: list[str] = []
        if "readme" in file.tags:
            score += 6
            reasons.append("Project documentation and intent")
        if "manifest" in file.tags:
            score += 4
            reasons.append("Declares dependencies and scripts")
        if "entrypoint" in file.tags or (main_entry and file.path == str(main_entry).lstrip("./")):
            score += 5
            reasons.append("Application entry point")
        parsed = file.parsed
        if parsed:
            if parsed.routes:
                score += 2 * len(parsed.routes)
                reasons.append(f"Defines {len(parsed.routes)} API route(s)")
            if parsed.data_access:
                score += 2
                reasons.append("Talks to the database")
            auth_symbols = [s.name for s in parsed.symbols if AUTH_NAME.search(s.name)]
            if auth_symbols:
                score += 2
                reasons.append("Authentication logic (" + ", ".join(auth_symbols[:3]) + ")")
            score += 0.2 * len(parsed.symbols)
        imported_by = inbound.get(file.path, 0)
        if imported_by:
            score += 1.5 * imported_by
            reasons.append(f"Imported by {imported_by} file(s)")
        if PurePosixPath(file.path).parts[0:1] and PurePosixPath(file.path).parts[0].lower() in SHARED_DIRS:
            score += 1
            reasons.append("Shared utilities")
        if score > 0 and reasons:
            ranked.append({"file": file.path, "score": round(score, 1), "reason": "; ".join(reasons)})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
