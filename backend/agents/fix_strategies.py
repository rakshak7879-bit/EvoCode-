"""Deterministic fix strategies used by the Fixer Agent's Patch Writer.

Each strategy rewrites the exact lines cited by a verified security finding and
keeps the number of lines unchanged, so every other finding keeps its line
numbers (and stays VERIFIED) after the patch is applied. A strategy raises
``NoFix`` with a reason when the code does not match a pattern it can rewrite
safely; the finding is then left for the LLM Patch Writer or for a human.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from agents.context import SourceFile
from agents.security_rules import RULES, SECRET_ASSIGNMENT
from memory.tokens import split_identifier

JS_LANGUAGES = frozenset({"javascript", "typescript", "vue", "svelte"})
BROWSER_DIRS = frozenset({"frontend", "public", "client", "web", "static", "www", "browser"})
_RULES = {rule.id: rule for rule in RULES}
_COMMENT = re.compile(r"^(\s*)(//+|#+|\*|<!--|--)\s?")
CORS_ALLOW_LIST = "(process.env.CORS_ORIGINS || '').split(',').filter(Boolean)"

MANUAL_REASONS: dict[str, str] = {
    "unsafe-eval": "Replacing eval needs a real expression parser or rule engine; the new behavior must be designed.",
    "unsafe-eval-py": "Replacing eval/exec needs explicit parsing; the new behavior must be designed.",
    "weak-hash": "Changing the password hash invalidates every stored hash; this needs bcrypt/Argon2 and a migration.",
    "token-localstorage": "Moving tokens to HttpOnly cookies changes both the server and the client.",
    "private-key": "Delete the key from the repository and its history, then rotate it.",
    "command-injection": "Rewriting shell calls to execFile with argument arrays needs input validation design.",
    "command-injection-py": "Rewriting shell calls to subprocess argument lists needs input validation design.",
    "path-traversal": "Path handling needs a fixed base directory and validation that depends on the feature.",
    "mass-assignment": "An allow-list of permitted fields has to be chosen for this model.",
    "insecure-random": "The secure replacement depends on how the value is used (token, id or nonce).",
    "debug-enabled": "Debug mode should come from environment-specific configuration.",
}
DUPLICATE_REASON = ("Duplicate refactors span several files and imports; Evo Code points at the shared "
                    "implementation but never auto-applies multi-file changes.")


class NoFix(Exception):
    """The code does not match a pattern that can be rewritten safely."""


@dataclass(frozen=True)
class Rewrite:
    """Replace lines ``line_start .. line_start + len(after) - 1`` with ``after`` (same line count)."""

    line_start: int
    after: tuple[str, ...]
    strategy: str
    explanation: str
    notes: tuple[str, ...] = ()


def env_name(identifier: str) -> str:
    parts = split_identifier(identifier)
    return "_".join(part.upper() for part in parts) or "SECRET_VALUE"


def _line(file: SourceFile, number: int) -> str:
    if number < 1 or number > len(file.lines):
        raise NoFix("The cited line is outside the file.")
    return file.lines[number - 1]


def _is_browser_code(file: SourceFile) -> bool:
    return any(part.lower() in BROWSER_DIRS for part in PurePosixPath(file.path).parts[:-1])


def _single(finding: dict[str, Any], new_line: str, strategy: str, explanation: str,
            *notes: str) -> Rewrite:
    return Rewrite(finding["line"], (new_line,), strategy, explanation, tuple(notes))


# --------------------------------------------------------------------------- strategies
def fix_secret(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    if file.kind != "source":
        raise NoFix("Credentials in configuration files belong in an environment-specific secret store.")
    if file.language in JS_LANGUAGES and _is_browser_code(file):
        raise NoFix("Browser code cannot read server environment variables; move the secret to the backend.")
    match = SECRET_ASSIGNMENT.search(line)
    if match is None:
        raise NoFix("The credential is not a simple quoted assignment.")
    name = env_name(match.group(1))
    if file.language in JS_LANGUAGES:
        expression = f"process.env.{name}"
    elif file.language == "python":
        if not re.search(r"^\s*(?:import os\b|from os import)", file.text, re.MULTILINE):
            raise NoFix("The file does not import os; add the environment lookup by hand.")
        expression = f'os.environ["{name}"]'
    else:
        raise NoFix(f"No environment-variable rewrite for {file.language}.")
    new_line = line[: match.start(2)] + expression + line[match.end():]
    return _single(finding, new_line, "secret-to-env",
                   f"Loads the credential from the {name} environment variable instead of source code.",
                   f"Set {name} in the environment (or a secret manager) and rotate the value that was committed.")


def fix_insecure_http(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    rule = _RULES["insecure-http"]
    match = rule.pattern.search(line) if rule.pattern else None
    if match is None:
        raise NoFix("No plain-HTTP URL literal on the cited line.")
    start = match.start() + 1  # after the opening quote
    new_line = line[:start] + "https://" + line[start + len("http://"):]
    return _single(finding, new_line, "http-to-https", "Uses HTTPS so data and credentials are encrypted in transit.",
                   "Confirm that the endpoint serves HTTPS before deploying.")


def fix_cors(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    if file.language not in JS_LANGUAGES:
        raise NoFix("Only Express-style cors() calls are rewritten automatically.")
    wildcard = re.compile(r"""origin\s*:\s*(['"`])\*\1""")
    if wildcard.search(line):
        new_line = wildcard.sub(f"origin: {CORS_ALLOW_LIST}", line, count=1)
    elif re.search(r"\bcors\(\s*\)", line):
        new_line = re.sub(r"\bcors\(\s*\)", f"cors({{ origin: {CORS_ALLOW_LIST} }})", line, count=1)
    else:
        raise NoFix("The CORS configuration is not a literal wildcard.")
    return _single(finding, new_line, "cors-allow-list", "Restricts cross-origin requests to an explicit allow-list.",
                   "Set CORS_ORIGINS to a comma-separated list of trusted origins (for example your storefront URL).")


def fix_exposed_env(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    response = re.compile(r"(res\.(?:json|send)\s*\(\s*)process\.env(\s*\))")
    log = re.compile(r"console\.log\s*\(\s*process\.env\s*\)")
    if response.search(line):
        new_line = response.sub(r"\1{ nodeEnv: process.env.NODE_ENV || 'development' }\2", line, count=1)
        note = "The endpoint now returns an explicit allow-list of safe values; consider removing it entirely."
    elif log.search(line):
        new_line = log.sub("console.log('Environment variables loaded:', Object.keys(process.env).length)", line,
                           count=1)
        note = "Only the number of variables is logged now."
    else:
        raise NoFix("No direct process.env response or log on the cited line.")
    return _single(finding, new_line, "env-allow-list", "Stops sending the full environment (and its secrets).", note)


def fix_dom_xss(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    pattern = re.compile(r"\.innerHTML(\s*=)")
    if not pattern.search(line):
        raise NoFix("Only innerHTML assignments are rewritten automatically.")
    new_line = pattern.sub(r".textContent\1", line, count=1)
    return _single(finding, new_line, "innerhtml-to-textcontent",
                   "Renders the value as text, so injected markup is never interpreted as HTML.",
                   "textContent shows HTML tags literally; use a sanitizer if markup is really needed.")


def fix_jwt_decode(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    call = re.search(r"\bjwt\.decode\s*\(\s*([^,()]+?)\s*\)", line)
    if call is None or file.language not in JS_LANGUAGES:
        raise NoFix("No single-argument jwt.decode() call on the cited line.")
    secret = re.search(r"\bjwt\.verify\s*\(\s*[^,()]+,\s*([A-Za-z_$][\w$.]*)", file.text) or re.search(
        r"\b(?:const|let|var)\s+([A-Za-z_$]*SECRET[\w$]*)\s*=", file.text, re.IGNORECASE)
    if secret is None:
        raise NoFix("No signing secret found in this file to verify the token with.")
    new_line = line[: call.start()] + f"jwt.verify({call.group(1)}, {secret.group(1)})" + line[call.end():]
    return _single(finding, new_line, "jwt-verify",
                   f"Checks the token signature with {secret.group(1)} instead of only decoding it.",
                   "jwt.verify throws on invalid or expired tokens; make sure callers handle the exception.")


_CONCAT = re.compile(
    r"""(["'])([^"'`\n]*?)'\1\s*\+\s*([A-Za-z_$][\w$.]*)\s*\+\s*(["'])'([^"'`\n]*)\4"""
)


def fix_sql_injection(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    number = finding["line"]
    line = _line(file, number)
    if file.language not in JS_LANGUAGES:
        raise NoFix("Only JavaScript/TypeScript string concatenation is parameterized automatically.")
    match = _CONCAT.search(line)
    if match is None:
        raise NoFix("The query is not a single quoted value concatenated into a string literal.")
    placeholder = "$1" if re.search(r"""require\(\s*['"]pg['"]\s*\)|from\s+['"]pg['"]""", file.text) else "?"
    value = match.group(3)
    literal = f"'{match.group(2)}{placeholder}{match.group(5)}'"
    params = f"[{value}]"
    inline = re.search(r"\.(query|execute)\(\s*$", line[: match.start()])
    if inline:  # db.query("... '" + value + "'")
        new_line = line[: match.start()] + literal + ", " + params + line[match.end():]
        return _single(finding, new_line, "parameterized-query",
                       f"Passes {value} as a bound parameter ({placeholder}) instead of concatenating it into SQL.")
    variable = re.match(r"\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", line)
    if variable is None:
        raise NoFix("The concatenated query is neither assigned to a variable nor passed to query().")
    name = variable.group(1)
    call = re.compile(rf"\.(query|execute)\(\s*{re.escape(name)}\s*\)")
    for offset in range(1, 5):
        if number + offset > len(file.lines):
            break
        candidate = file.lines[number + offset - 1]
        if call.search(candidate):
            new_call = call.sub(lambda m: f".{m.group(1)}({name}, {params})", candidate, count=1)
            new_line = line[: match.start()] + literal + line[match.end():]
            after = (new_line, *file.lines[number: number + offset - 1], new_call)
            return Rewrite(number, tuple(after), "parameterized-query",
                           f"Uses a {placeholder} placeholder and passes {value} as a bound parameter.")
    raise NoFix(f"Could not find where {name} is executed within the next lines.")


def fix_prompt_injection(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    comment = _COMMENT.match(line)
    if comment:
        indent, prefix = comment.group(1), comment.group(2)
        closing = " -->" if prefix == "<!--" else ""
        new_line = f"{indent}{prefix} NOTE: text addressed to AI tools was removed here (flagged by Evo Code).{closing}"
    else:
        rule = _RULES["prompt-injection"]
        new_line = rule.pattern.sub("[instruction for AI tools removed]", line) if rule.pattern else line
    return _single(finding, new_line, "remove-ai-instruction",
                   "Removes the instruction aimed at AI tools; repository text must never steer automated reviewers.",
                   "Check the history of this line to find out who added the instruction.")


def fix_tls(file: SourceFile, finding: dict[str, Any]) -> Rewrite:
    line = _line(file, finding["line"])
    for pattern, replacement in ((r"(rejectUnauthorized\s*:\s*)false", r"\1true"),
                                 (r"(\bverify\s*=\s*)False", r"\1True"),
                                 (r"(InsecureSkipVerify:\s*)true", r"\1false")):
        if re.search(pattern, line):
            return _single(finding, re.sub(pattern, replacement, line, count=1), "enable-tls-verification",
                           "Turns certificate verification back on.",
                           "Configure a trusted CA bundle if the server uses a private certificate.")
    raise NoFix("TLS verification is disabled in a way that is not rewritten automatically.")


Strategy = Callable[[SourceFile, dict[str, Any]], Rewrite]
STRATEGIES: dict[str, Strategy] = {
    "hardcoded-secret": fix_secret,
    "known-key-format": fix_secret,
    "insecure-http": fix_insecure_http,
    "insecure-cors": fix_cors,
    "exposed-env": fix_exposed_env,
    "dom-xss": fix_dom_xss,
    "jwt-no-verify": fix_jwt_decode,
    "sql-injection": fix_sql_injection,
    "prompt-injection": fix_prompt_injection,
    "tls-disabled": fix_tls,
}


def manual_reason(finding: dict[str, Any]) -> str:
    if finding["category"] == "duplicate":
        return DUPLICATE_REASON
    return MANUAL_REASONS.get(finding.get("rule_id") or "", "No safe automatic rewrite exists for this rule.")
