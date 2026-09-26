"""Lightweight, regex-based source parsing (never executes repository code).

Extracts symbols (functions, classes, methods), imports, exports, HTTP routes,
client API calls, outbound HTTP calls, data-access calls and legacy markers for
JavaScript/TypeScript and Python (plus Go functions). Results are heuristic by
design: every downstream claim is re-verified against the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

JS_LANGUAGES = frozenset({"javascript", "typescript", "vue", "svelte"})
PY_LANGUAGES = frozenset({"python"})


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # function | class | method
    file: str
    line_start: int
    line_end: int
    parent: str | None = None
    signature: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    @property
    def length(self) -> int:
        return self.line_end - self.line_start + 1


@dataclass(frozen=True)
class ImportRef:
    module: str
    line: int
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    file: str
    line: int
    line_end: int
    middlewares: tuple[str, ...] = ()
    handler: str | None = None
    framework: str = "express"


@dataclass(frozen=True)
class HttpCall:
    method: str
    target: str
    file: str
    line: int
    via: str


@dataclass(frozen=True)
class DataAccess:
    file: str
    line: int
    kind: str  # sql | document | orm
    snippet: str


@dataclass(frozen=True)
class Marker:
    file: str
    line: int
    text: str


@dataclass
class ParsedFile:
    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    exports: tuple[str, ...] = ()
    routes: list[Route] = field(default_factory=list)
    api_calls: list[HttpCall] = field(default_factory=list)
    external_calls: list[HttpCall] = field(default_factory=list)
    data_access: list[DataAccess] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    uses_dom: bool = False

    def symbol_at(self, line: int) -> Symbol | None:
        """Innermost symbol whose span contains ``line`` (1-based)."""
        best: Symbol | None = None
        for symbol in self.symbols:
            if symbol.line_start <= line <= symbol.line_end:
                if best is None or symbol.length < best.length:
                    best = symbol
        return best

    def find_symbol(self, name: str) -> Symbol | None:
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        return None


# --------------------------------------------------------------------------- #
# Brace matching (JavaScript / TypeScript / Go)
# --------------------------------------------------------------------------- #


def find_block_end(lines: list[str], start: int, max_lines: int = 800) -> int | None:
    """0-based index of the line that closes the first ``{`` block at/after ``start``."""
    depth = 0
    opened = False
    quote: str | None = None
    in_block_comment = False
    template_depths: list[int] = []
    for index in range(start, min(len(lines), start + max_lines)):
        line = lines[index]
        i = 0
        length = len(line)
        while i < length:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < length else ""
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if quote == "`" and ch == "$" and nxt == "{":
                    template_depths.append(depth)
                    depth += 1
                    quote = None
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch == "/" and nxt == "/":
                break
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch in "\"'`":
                quote = ch
            elif ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if template_depths and depth == template_depths[-1]:
                    template_depths.pop()
                    quote = "`"
                elif opened and depth <= 0:
                    return index
            i += 1
        if quote in ("'", '"'):
            quote = None  # unterminated single-line string: recover at end of line
    return None


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

_JS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]"
)
_JS_VARIABLE_FN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*(?:async\s+)?"
    r"(function\b|\(|[A-Za-z_$][\w$]*\s*=>)"
)
_JS_EXPORTS_FN = re.compile(
    r"^\s*(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(function\b|\(|[A-Za-z_$][\w$]*\s*=>)"
)
_JS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_METHOD = re.compile(
    r"^\s+(?:(?:public|private|protected|static|readonly|override|async|get|set)\s+)*\*?"
    r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{=]+)?\{\s*(?://.*)?$"
)
_JS_NOT_METHODS = frozenset({"if", "for", "while", "switch", "catch", "function", "return", "with", "else"})

_JS_IMPORT_FROM = re.compile(r"""^\s*import\s+(?:type\s+)?([\w$*{}\s,]+?)\s+from\s+['"]([^'"]+)['"]""")
_JS_IMPORT_SIDE_EFFECT = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(
    r"""(?:(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*)?require\(\s*['"]([^'"]+)['"]\s*\)"""
)
_JS_MODULE_EXPORTS = re.compile(r"module\.exports\s*=\s*\{")
_JS_EXPORT_DECL = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)"
)
_JS_EXPORT_LIST = re.compile(r"^\s*export\s*\{([^}]*)\}")
_JS_EXPORTS_ASSIGN = re.compile(r"^\s*(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=")

_JS_ROUTE = re.compile(
    r"""\b(app|router|server|routes?|[A-Za-z_$][\w$]*(?:Router|App|router|app))\."""
    r"""(get|post|put|patch|delete|all|options|head)\(\s*(['"`])([^'"`]+)\3(.*)$"""
)
_PY_ROUTE = re.compile(
    r"""^\s*@(\w+)\.(get|post|put|patch|delete|route|api_route)\(\s*['"]([^'"]+)['"](.*)$"""
)
_PY_METHODS = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""")

_FETCH_LITERAL = re.compile(r"""\bfetch\(\s*(['"`])([^'"`]+)\1""")
_AXIOS_LITERAL = re.compile(r"""\baxios\.(get|post|put|patch|delete)\(\s*(['"`])([^'"`]+)\2""")
_API_LITERAL_CALL = re.compile(r"""(?<![\w$.])([A-Za-z_$][\w$]*)\(\s*(['"`])(/(?:api|v\d+)/[^'"`]*)\2(\s*,)?""")
_METHOD_OPTION = re.compile(r"""method\s*:\s*['"]([A-Za-z]+)['"]""")
_OUTBOUND = re.compile(
    r"""\b(fetch|axios(?:\.(?:get|post|put|patch|delete|request))?|requests\.(?:get|post|put|patch|delete|request)|"""
    r"""httpx\.(?:get|post|put|patch|delete|request)|urllib\.request\.urlopen|https?\.request)\s*\(\s*([^,)]*)"""
)

_DATA_CALL = re.compile(
    r"""\b(pool|client|db|database|conn|connection|knex|prisma|sequelize|cursor|session|collection|mongoose)\."""
    r"""(query|execute|executemany|raw|run|all|findOne|findMany|find|insertOne|insertMany|updateOne|updateMany|"""
    r"""deleteOne|deleteMany|aggregate|collection|select|insert|update|delete|create|save)\s*\("""
)
_SQL_TEXT = re.compile(
    r"""(?i)['"`]\s*(?:SELECT\s.*?\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b"""
)
_MARKER = re.compile(
    r"""(?://|#|/\*|^\s*\*|<!--).*?\b(DEPRECATED|LEGACY|OBSOLETE|scheduled for removal|old implementation)\b|@deprecated""",
    re.IGNORECASE,
)
_DOM_USAGE = re.compile(r"\b(document\.|window\.|localStorage\.|sessionStorage\.)")

_PY_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_PY_CLASS = re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)\s*[(:]")
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)\s*$")
_PY_FROM = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+\(?([^)#]*)")

_GO_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]")

_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_arrow_function(lines: list[str], index: int) -> bool:
    text = " ".join(lines[index : index + 4])
    arrow = text.find("=>")
    if arrow < 0:
        return False
    semicolon = text.find(";")
    return semicolon < 0 or arrow < semicolon


def _expression_arrow_end(lines: list[str], index: int) -> int | None:
    """If the arrow at ``index`` has an expression body, return its last line."""
    for offset in range(0, 3):
        line = lines[index + offset] if index + offset < len(lines) else ""
        pos = line.find("=>")
        if pos >= 0:
            rest = line[pos + 2 :].strip()
            if rest.startswith("{"):
                return None
            if rest == "" and index + offset + 1 < len(lines):
                if lines[index + offset + 1].strip().startswith("{"):
                    return None
            for end in range(index + offset, min(len(lines), index + offset + 30)):
                if lines[end].rstrip().endswith((";", "),", ")")):
                    return end
            return index + offset
    return None


def _js_symbol_end(lines: list[str], index: int, arrow: bool) -> int:
    if arrow:
        expression_end = _expression_arrow_end(lines, index)
        if expression_end is not None:
            return expression_end
    if lines[index].rstrip().endswith(";") and "{" not in lines[index]:
        return index  # declaration / overload signature without a body
    end = find_block_end(lines, index)
    return end if end is not None else index


def _js_import_names(clause: str) -> tuple[str, ...]:
    names: list[str] = []
    for part in re.split(r"[{},]", clause):
        token = part.strip().removeprefix("type ").strip()
        if not token or token == "*":
            continue
        if " as " in token:
            token = token.split(" as ")[-1].strip()
        if ":" in token:
            token = token.split(":")[-1].strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", token):
            names.append(token)
    return tuple(names)


def _route_arguments(rest: str) -> tuple[tuple[str, ...], str | None]:
    """Parse ``, mw1, mw2, handler`` after a route path literal."""
    args = [a.strip() for a in rest.split(",")]
    identifiers: list[str] = []
    for arg in args[1:]:
        match = re.fullmatch(r"([A-Za-z_$][\w$.]*)\s*\)?\s*;?", arg)
        if not match:
            break
        identifiers.append(match.group(1))
    handler: str | None = None
    inline = "=>" in rest or "function" in rest
    if identifiers and not inline and rest.rstrip().endswith((")", ");")):
        handler = identifiers.pop()
    return tuple(identifiers), handler


def _python_block_end(lines: list[str], index: int, indent: int) -> int:
    depth = 0
    signature_end = index
    for j in range(index, min(len(lines), index + 30)):
        code = lines[j].split("#", 1)[0]
        depth += code.count("(") + code.count("[") + code.count("{")
        depth -= code.count(")") + code.count("]") + code.count("}")
        if depth <= 0:
            if not code.rstrip().endswith(":"):
                return j  # one-liner such as `def f(): return 1`
            signature_end = j
            break
    end = signature_end
    for k in range(signature_end + 1, len(lines)):
        stripped = lines[k].strip()
        if not stripped:
            continue
        current_indent = len(lines[k]) - len(lines[k].lstrip())
        if current_indent <= indent and not stripped.startswith("#"):
            break
        end = k
    return end


# --------------------------------------------------------------------------- #
# Language parsers
# --------------------------------------------------------------------------- #


def _parse_js_symbols(path: str, lines: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    classes: list[Symbol] = []
    for index, line in enumerate(lines):
        match = _JS_CLASS.match(line)
        if match:
            end = find_block_end(lines, index)
            symbol = Symbol(match.group(1), "class", path, index + 1, (end if end is not None else index) + 1,
                            signature=line.strip())
            symbols.append(symbol)
            classes.append(symbol)
            continue
        match = _JS_FUNCTION.match(line)
        if match:
            end = _js_symbol_end(lines, index, arrow=False)
            symbols.append(Symbol(match.group(1), "function", path, index + 1, end + 1, signature=line.strip()))
            continue
        match = _JS_VARIABLE_FN.match(line) or _JS_EXPORTS_FN.match(line)
        if match:
            head = match.group(2)
            is_function = head.startswith("function") or "=>" in head or _is_arrow_function(lines, index)
            if is_function:
                arrow = not head.startswith("function")
                end = _js_symbol_end(lines, index, arrow=arrow)
                symbols.append(Symbol(match.group(1), "function", path, index + 1, end + 1, signature=line.strip()))
    for cls in classes:
        for index in range(cls.line_start, cls.line_end - 1):
            line = lines[index]
            match = _JS_METHOD.match(line)
            if not match or match.group(1) in _JS_NOT_METHODS:
                continue
            end = find_block_end(lines, index)
            symbols.append(
                Symbol(match.group(1), "method", path, index + 1, (end if end is not None else index) + 1,
                       parent=cls.name, signature=line.strip())
            )
    symbols.sort(key=lambda s: (s.line_start, -s.length))
    return symbols


def _parse_js_imports_exports(lines: list[str]) -> tuple[list[ImportRef], tuple[str, ...]]:
    imports: list[ImportRef] = []
    exports: list[str] = []
    for index, line in enumerate(lines):
        match = _JS_IMPORT_FROM.match(line)
        if match:
            imports.append(ImportRef(match.group(2), index + 1, _js_import_names(match.group(1))))
        else:
            match = _JS_IMPORT_SIDE_EFFECT.match(line)
            if match:
                imports.append(ImportRef(match.group(1), index + 1))
        for req in _JS_REQUIRE.finditer(line):
            names = _js_import_names(req.group(1)) if req.group(1) else ()
            imports.append(ImportRef(req.group(2), index + 1, names))
        if _JS_MODULE_EXPORTS.search(line):
            block = " ".join(lines[index : index + 25])
            body = block.split("{", 1)[1].split("}", 1)[0]
            exports.extend(_js_import_names(body))
        for pattern in (_JS_EXPORT_DECL, _JS_EXPORTS_ASSIGN):
            match = pattern.match(line)
            if match:
                exports.append(match.group(1))
        match = _JS_EXPORT_LIST.match(line)
        if match:
            exports.extend(_js_import_names(match.group(1)))
    return imports, tuple(dict.fromkeys(exports))


def _parse_js_routes(path: str, lines: list[str]) -> list[Route]:
    routes: list[Route] = []
    for index, line in enumerate(lines):
        match = _JS_ROUTE.search(line)
        if not match:
            continue
        middlewares, handler = _route_arguments(match.group(5) or "")
        end = index
        if handler is None:
            block_end = find_block_end(lines, index)
            end = block_end if block_end is not None else index
        routes.append(
            Route(
                method=match.group(2).upper(),
                path=match.group(4),
                file=path,
                line=index + 1,
                line_end=end + 1,
                middlewares=middlewares,
                handler=handler,
                framework="express",
            )
        )
    return routes


def _parse_python_symbols(path: str, lines: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    classes: list[tuple[Symbol, int]] = []
    for index, line in enumerate(lines):
        match = _PY_CLASS.match(line)
        kind = "class"
        if not match:
            match = _PY_DEF.match(line)
            kind = "function"
        if not match:
            continue
        indent = len(match.group(1).replace("\t", "    "))
        end = _python_block_end(lines, index, indent)
        parent = None
        for cls, cls_indent in reversed(classes):
            if cls.line_start <= index + 1 <= cls.line_end and indent > cls_indent:
                parent = cls.name
                break
        if kind == "function" and parent:
            kind = "method"
        symbol = Symbol(match.group(2), kind, path, index + 1, end + 1, parent=parent, signature=line.strip())
        symbols.append(symbol)
        if kind == "class":
            classes.append((symbol, indent))
    return symbols


def _parse_python_imports(lines: list[str]) -> list[ImportRef]:
    imports: list[ImportRef] = []
    for index, line in enumerate(lines):
        match = _PY_FROM.match(line)
        if match:
            names = tuple(
                n.strip().split(" as ")[-1].strip()
                for n in match.group(2).split(",")
                if n.strip() and n.strip() != "*"
            )
            imports.append(ImportRef(match.group(1), index + 1, names))
            continue
        match = _PY_IMPORT.match(line)
        if match:
            for part in match.group(1).split(","):
                module = part.strip().split(" as ")[0].strip()
                imports.append(ImportRef(module, index + 1))
    return imports


def _parse_python_routes(path: str, lines: list[str], symbols: list[Symbol]) -> list[Route]:
    routes: list[Route] = []
    for index, line in enumerate(lines):
        match = _PY_ROUTE.match(line)
        if not match:
            continue
        verb = match.group(2).lower()
        if verb in {"route", "api_route"}:
            methods = _PY_METHODS.search(match.group(4) or "")
            verb = methods.group(1).split(",")[0].strip(" '\"") if methods else "GET"
        handler = next(
            (s for s in symbols if s.kind in {"function", "method"} and s.line_start > index + 1
             and s.line_start <= index + 8),
            None,
        )
        routes.append(
            Route(
                method=verb.upper(),
                path=match.group(3),
                file=path,
                line=index + 1,
                line_end=handler.line_end if handler else index + 1,
                handler=handler.name if handler else None,
                framework="python",
            )
        )
    return routes


def _parse_go_symbols(path: str, lines: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for index, line in enumerate(lines):
        match = _GO_FUNC.match(line)
        if match:
            end = find_block_end(lines, index)
            symbols.append(Symbol(match.group(1), "function", path, index + 1,
                                  (end if end is not None else index) + 1, signature=line.strip()))
    return symbols


# --------------------------------------------------------------------------- #
# Cross-language extraction
# --------------------------------------------------------------------------- #


def _http_method_for_helper(name: str, symbols: list[Symbol], lines: list[str], has_payload: bool) -> str:
    lowered = name.lower()
    for verb in ("delete", "patch", "post", "put"):
        if verb in lowered:
            return verb.upper()
    helper = next((s for s in symbols if s.name == name), None)
    if helper:
        body = "\n".join(lines[helper.line_start - 1 : helper.line_end])
        match = _METHOD_OPTION.search(body)
        if match:
            return match.group(1).upper()
    return "POST" if has_payload else "GET"


def _extract_http(path: str, lines: list[str], symbols: list[Symbol]) -> tuple[list[HttpCall], list[HttpCall]]:
    api_calls: list[HttpCall] = []
    external: list[HttpCall] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("//", "#", "*")):
            continue
        for match in _FETCH_LITERAL.finditer(line):
            url = match.group(2)
            window = " ".join(lines[index : index + 8])
            method_match = _METHOD_OPTION.search(window)
            method = method_match.group(1).upper() if method_match else "GET"
            call = HttpCall(method, url, path, index + 1, "fetch")
            (api_calls if url.startswith("/") else external).append(call)
        for match in _AXIOS_LITERAL.finditer(line):
            url = match.group(3)
            call = HttpCall(match.group(1).upper(), url, path, index + 1, "axios")
            (api_calls if url.startswith("/") else external).append(call)
        for match in _API_LITERAL_CALL.finditer(line):
            helper = match.group(1)
            if helper in {"fetch", "require", "import", "get", "post", "put", "patch", "delete"}:
                continue
            method = _http_method_for_helper(helper, symbols, lines, bool(match.group(4)))
            api_calls.append(HttpCall(method, match.group(3), path, index + 1, helper))
        for match in _OUTBOUND.finditer(line):
            target = match.group(2).strip()
            if target.startswith(("'/", '"/', "`/")):
                continue  # relative URL: an internal API call, handled above
            if target and target[0] in "'\"`" and not target.strip("'\"`").startswith("http"):
                continue
            external.append(HttpCall("?", target[:120] or "(dynamic)", path, index + 1, match.group(1)))
    return api_calls, external


def _extract_data_access(path: str, lines: list[str]) -> list[DataAccess]:
    found: list[DataAccess] = []
    for index, line in enumerate(lines):
        call = _DATA_CALL.search(line)
        sql = _SQL_TEXT.search(line)
        if not call and not sql:
            continue
        method = call.group(2) if call else ""
        if sql or method in {"query", "execute", "executemany", "raw", "run", "all"}:
            kind = "sql"
        elif method.startswith(("find", "insert", "update", "delete", "aggregate", "collection")):
            kind = "document"
        else:
            kind = "orm"
        found.append(DataAccess(path, index + 1, kind, line.strip()[:160]))
    return found


def _extract_markers(path: str, lines: list[str]) -> list[Marker]:
    return [Marker(path, i + 1, line.strip()[:200]) for i, line in enumerate(lines) if _MARKER.search(line)]


def parse_source(path: str, language: str, lines: list[str]) -> ParsedFile:
    parsed = ParsedFile(path=path, language=language)
    if language in JS_LANGUAGES:
        parsed.symbols = _parse_js_symbols(path, lines)
        parsed.imports, parsed.exports = _parse_js_imports_exports(lines)
        parsed.routes = _parse_js_routes(path, lines)
        parsed.uses_dom = any(_DOM_USAGE.search(line) for line in lines)
    elif language in PY_LANGUAGES:
        parsed.symbols = _parse_python_symbols(path, lines)
        parsed.imports = _parse_python_imports(lines)
        parsed.exports = tuple(
            s.name for s in parsed.symbols if s.parent is None and not s.name.startswith("_")
        )
        parsed.routes = _parse_python_routes(path, lines, parsed.symbols)
    elif language == "go":
        parsed.symbols = _parse_go_symbols(path, lines)
    if language in JS_LANGUAGES or language in PY_LANGUAGES:
        parsed.api_calls, parsed.external_calls = _extract_http(path, lines, parsed.symbols)
    parsed.data_access = _extract_data_access(path, lines)
    parsed.markers = _extract_markers(path, lines)
    return parsed


def called_identifiers(text: str) -> list[str]:
    """Identifiers used as direct (non-method) calls in ``text``, in order of appearance."""
    seen: dict[str, None] = {}
    for match in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", text):
        seen.setdefault(match.group(1), None)
    return list(seen)
