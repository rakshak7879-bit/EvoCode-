"""Request-flow tracing: UI -> API call -> route -> middleware -> services -> database.

Flows are derived from the code itself: client API calls are matched to
backend routes, route handlers are scanned for calls to functions defined in
the repository, and those functions are scanned for data access. Every hop
carries a file + line citation.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from agents.architecture import AUTH_NAME
from agents.context import AgentContext, SourceFile
from repo.parser import HttpCall, Route, Symbol, called_identifiers

_PARAM = re.compile(r"\$\{[^}]+\}|:\w+|\{[^}]+\}|<[^>]+>")
_SKIP_CALLS = frozenset({"require", "next", "json", "status", "send", "parseInt", "String", "Number", "Error",
                         "fetch", "Boolean", "Object", "Array", "JSON", "Promise", "console", "super"})


def _normalize_path(path: str) -> list[str]:
    path = path.split("?")[0].split("#")[0]
    path = _PARAM.sub("*", path)
    return [segment for segment in path.strip("/").split("/") if segment]


def _segments_match(a: list[str], b: list[str]) -> bool:
    return len(a) == len(b) and all(x == y or "*" in (x, y) for x, y in zip(a, b))


def match_route(call: HttpCall, routes: list[tuple[SourceFile, Route]]) -> tuple[SourceFile, Route] | None:
    target = _normalize_path(call.target)
    exact = [(f, r) for f, r in routes if _segments_match(target, _normalize_path(r.path))]
    for file, route in exact:
        if route.method == call.method or route.method == "ALL":
            return file, route
    if exact:
        return exact[0]
    for file, route in routes:  # mounted routers: "/login" served under "/api"
        route_segments = _normalize_path(route.path)
        if route_segments and _segments_match(target[-len(route_segments):], route_segments):
            return file, route
    return None


def resolve_symbol(context: AgentContext, name: str, preferred: SourceFile) -> Symbol | None:
    candidates = context.symbol_index.get(name, [])
    if not candidates:
        return None
    for symbol in candidates:
        if symbol.file == preferred.path:
            return symbol
    return candidates[0]


def _flow_name(path: str) -> str:
    segments = [s for s in _normalize_path(path) if s not in {"api", "*"} and not re.fullmatch(r"v\d+", s)]
    if not segments:
        return "Root request"
    return " · ".join(s.replace("-", " ").replace("_", " ").capitalize() for s in segments[:2])


def _step(layer: str, label: str, file: str, line: int, symbol: str | None = None) -> dict[str, Any]:
    return {"layer": layer, "label": label, "file": file, "line": line, "symbol": symbol}


def _handler_text(context: AgentContext, file: SourceFile, route: Route) -> tuple[str, SourceFile, int, int]:
    if route.handler:
        symbol = resolve_symbol(context, route.handler, file)
        if symbol:
            handler_file = context.file(symbol.file) or file
            return ("\n".join(handler_file.lines[symbol.line_start - 1 : symbol.line_end]), handler_file,
                    symbol.line_start, symbol.line_end)
    return "\n".join(file.lines[route.line - 1 : route.line_end]), file, route.line, route.line_end


def _backend_steps(context: AgentContext, file: SourceFile, route: Route, database: str) -> list[dict[str, Any]]:
    steps = [_step("Route", f"{route.method} {route.path}", file.path, route.line)]
    for middleware in route.middlewares:
        symbol = resolve_symbol(context, middleware.split(".")[-1], file)
        layer = "Auth" if AUTH_NAME.search(middleware) else "Middleware"
        steps.append(_step(layer, f"{middleware}() middleware", symbol.file if symbol else file.path,
                           symbol.line_start if symbol else route.line, middleware))
    text, handler_file, start, end = _handler_text(context, file, route)
    database_added = False
    for name in called_identifiers(text):
        if name in _SKIP_CALLS or name in route.middlewares or len(steps) >= 7:
            continue
        symbol = resolve_symbol(context, name, handler_file)
        if symbol is None or symbol.kind == "class":
            continue
        target_file = context.file(symbol.file)
        accesses = []
        if target_file and target_file.parsed:
            accesses = [a for a in target_file.parsed.data_access if symbol.line_start <= a.line <= symbol.line_end]
        layer = "Auth" if AUTH_NAME.search(name) and not accesses else ("Data" if accesses else "Service")
        steps.append(_step(layer, f"{name}()", symbol.file, symbol.line_start, name))
        if accesses and not database_added:
            steps.append(_step("Database", f"{database} query", symbol.file, accesses[0].line))
            database_added = True
    if handler_file.parsed:
        for access in handler_file.parsed.data_access:
            if start <= access.line <= end and not database_added:
                steps.append(_step("Database", f"{database} query", handler_file.path, access.line))
                database_added = True
        for call in handler_file.parsed.external_calls:
            if start <= call.line <= end:
                steps.append(_step("External", f"Outbound HTTP call ({call.target})", handler_file.path, call.line))
                break
    return steps


def trace_request_flows(context: AgentContext, database: str = "Database", limit: int = 4) -> list[dict[str, Any]]:
    routes = [(f, r) for f in context.source_files if f.parsed for r in f.parsed.routes]
    flows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file in context.source_files:
        if not file.parsed or file.parsed.routes:
            continue
        for call in file.parsed.api_calls:
            trigger = f"{call.method} {call.target}"
            if trigger in seen:
                continue
            seen.add(trigger)
            enclosing = file.parsed.symbol_at(call.line)
            steps = [
                _step("UI", f"{enclosing.name}() in the browser" if enclosing else "Browser UI", file.path,
                      enclosing.line_start if enclosing else call.line, enclosing.name if enclosing else None),
                _step("API call", f"{trigger}" + (f" via {call.via}()" if call.via not in {"fetch", "axios"} else ""),
                      file.path, call.line),
            ]
            matched = match_route(call, routes)
            if matched:
                steps.extend(_backend_steps(context, matched[0], matched[1], database))
            flows.append({"name": _flow_name(call.target), "trigger": trigger, "resolved": bool(matched),
                          "steps": steps})
    if not flows:
        for file, route in routes[:limit]:
            flows.append({"name": _flow_name(route.path), "trigger": f"{route.method} {route.path}", "resolved": True,
                          "steps": _backend_steps(context, file, route, database)})
    flows.sort(key=lambda flow: (not flow["resolved"], -len(flow["steps"])))
    return flows[:limit]


def route_table(context: AgentContext) -> list[dict[str, Any]]:
    table = []
    for file in context.source_files:
        if not file.parsed:
            continue
        for route in file.parsed.routes:
            table.append({"method": route.method, "path": route.path, "file": file.path, "line": route.line,
                          "middlewares": list(route.middlewares), "module": PurePosixPath(file.path).parts[0]})
    return table
