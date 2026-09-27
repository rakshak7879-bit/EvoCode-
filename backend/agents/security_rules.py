"""Deterministic security rules.

Each rule matches a single source line, so every finding it produces carries
concrete evidence (file + line + text) that the Brain can verify. Secret values
are masked in evidence and never sent to an LLM.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

SOURCE = frozenset({"source"})
SOURCE_CONFIG = frozenset({"source", "config", "manifest"})
EVERYTHING = frozenset({"source", "config", "manifest", "doc"})
JS = frozenset({"javascript", "typescript", "vue", "svelte"})
PY = frozenset({"python"})
_HASH_COMMENT_LANGUAGES = frozenset(
    {"python", "shell", "yaml", "toml", "ruby", "dockerfile", "makefile", "ini", "dotenv", "r", "elixir",
     "powershell", "terraform", "properties"}
)

# --------------------------------------------------------------------------- patterns
SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret|passwd|password|pwd|token|private[_-]?key|access[_-]?key|"""
    r"""client[_-]?secret|auth[_-]?key)[A-Za-z0-9_]*)["']?\s*[:=]\s*(['"`])([^'"`\s]{6,})\2"""
)
KNOWN_KEY = re.compile(
    r"(sk-(?:live|proj|test)-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{35}|sk_live_[0-9a-zA-Z]{20,})"
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(?:\$\{|<|\{\{|your|xxx|\*{3,}|changeme|change_me|placeholder|example|dummy|todo|none|null|"
    r"undefined|process\.env|os\.environ|replace)"
)
_NON_SECRET_NAME = re.compile(
    r"(?i)(url|uri|path|header|field|name|label|type|pattern|regex|prefix|length|message|msg|endpoint|file|"
    r"dir|column|placeholder|key_name|storage_key|param|query)$"
)
_SQL_KEYWORDS = re.compile(r"(?i)\b(select\s.+\sfrom|insert\s+into|update\s+\w+\s+set|delete\s+from)\b")
_CONCAT = re.compile(r"""['"]\s*\+\s*[A-Za-z_$(]|[A-Za-z_$)\]]\s*\+\s*['"]""")
_TEMPLATE_INTERPOLATION = re.compile(r"`[^`]*\$\{[^`]*`")
_PY_FSTRING = re.compile(r"""\bf(['"]).*\{[^}]+\}.*\1""")
_PY_FORMAT = re.compile(r"""(['"])\s*%\s*[\(\w]|\.format\(""")
_JS_EXEC = re.compile(
    r"(?:child_process|childProcess|cp)\.(?:exec|execSync|spawn|spawnSync)\s*\(|(?<![\w.$])(?:exec|execSync)\s*\("
)
_PY_SHELL = re.compile(r"\bos\.(?:system|popen)\s*\(")
_PY_SHELL_TRUE = re.compile(r"\bsubprocess\.\w+\([^)]*shell\s*=\s*True")


def _dynamic_string(line: str) -> bool:
    return bool(
        _CONCAT.search(line)
        or _TEMPLATE_INTERPOLATION.search(line)
        or _PY_FSTRING.search(line)
        or _PY_FORMAT.search(line)
    )


def _sql_injection(line: str) -> bool:
    return bool(_SQL_KEYWORDS.search(line)) and _dynamic_string(line)


def _js_command_injection(line: str) -> bool:
    return bool(_JS_EXEC.search(line)) and (_dynamic_string(line) or "${" in line)


def _py_command_injection(line: str) -> bool:
    if _PY_SHELL_TRUE.search(line):
        return True
    return bool(_PY_SHELL.search(line)) and (_dynamic_string(line) or "+" in line)


def _secret_title(match: re.Match[str]) -> str:
    name = match.group(1).lower()
    if "jwt" in name:
        return "Hardcoded JWT secret"
    if "api" in name and "key" in name:
        return "Hardcoded API key"
    if "pass" in name or "pwd" in name:
        return "Hardcoded password"
    if "private" in name:
        return "Hardcoded private key"
    if "access" in name:
        return "Hardcoded access key"
    if "token" in name:
        return "Hardcoded token"
    return "Hardcoded secret"


_VERSION_VALUE = re.compile(r"^[~^<>=v]*\d+(?:\.[\dx*]+)*(?:[-+][\w.]+)?$")
# Sentinel/marker constants such as "###AGENT-RETRY###" or "<<<END_OF_BLOCK>>>": wrapped in a run of
# punctuation, or shouted with no lowercase letters and no digits. Real credentials have entropy.
_MARKER_VALUE = re.compile(r"^([^\w\s])\1+.*\1+$")


def _valid_secret(match: re.Match[str]) -> bool:
    name, value = match.group(1), match.group(3)
    if _PLACEHOLDER_VALUE.search(value) or _NON_SECRET_NAME.search(name) or _VERSION_VALUE.match(value):
        return False
    if _MARKER_VALUE.match(value):
        return False
    if not any(c.islower() for c in value) and not any(c.isdigit() for c in value):
        return False  # e.g. RETRY_TOKEN = "SWE-AGENT-RETRY-WITH-OUTPUT"
    return any(c.isdigit() or c in "-_./+=" for c in value) or len(value) >= 16


def mask_secret(value: str) -> str:
    return f"{value[:4]}[REDACTED]" if len(value) > 4 else "[REDACTED]"


# --------------------------------------------------------------------------- rule model
@dataclass(frozen=True)
class SecurityRule:
    id: str
    family: str
    title: str
    severity: str
    description: str
    recommendation: str
    confidence: float
    cwe: str
    kinds: frozenset[str] = SOURCE
    languages: frozenset[str] | None = None
    pattern: re.Pattern[str] | None = None
    matcher: Callable[[str], bool] | None = None
    validator: Callable[[re.Match[str]], bool] | None = None
    title_for: Callable[[re.Match[str]], str] | None = None
    secret_group: int | None = None
    skip_comments: bool = True

    def applies_to(self, kind: str, language: str) -> bool:
        if kind not in self.kinds:
            return False
        return self.languages is None or language in self.languages

    def evaluate(self, line: str) -> tuple[bool, re.Match[str] | None]:
        if self.pattern is not None:
            match = self.pattern.search(line)
            if match is None or (self.validator is not None and not self.validator(match)):
                return False, None
            return True, match
        if self.matcher is not None:
            return self.matcher(line), None
        return False, None

    def finding_title(self, match: re.Match[str] | None) -> str:
        return self.title_for(match) if self.title_for and match else self.title

    def evidence(self, line: str, match: re.Match[str] | None) -> str:
        text = line.strip()
        if match is not None and self.secret_group is not None:
            secret = match.group(self.secret_group)
            text = text.replace(secret, mask_secret(secret))
        return text if len(text) <= 220 else f"{text[:217]}…"


def is_comment_line(line: str, language: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("//", "/*", "*", "<!--")):
        return True
    if stripped.startswith("#") and language in _HASH_COMMENT_LANGUAGES:
        return True
    return stripped.startswith("--") and language == "sql"


RULES: tuple[SecurityRule, ...] = (
    SecurityRule(
        id="hardcoded-secret", family="secret", title="Hardcoded secret", severity="high",
        description="A credential is assigned directly in source code. Anyone with repository access (and every "
                    "build artifact) can read it, and rotating it requires a code change.",
        recommendation="Move the value to environment configuration or a secret manager and rotate the exposed "
                       "credential.",
        confidence=0.9, cwe="CWE-798", kinds=frozenset({"source", "config"}), pattern=SECRET_ASSIGNMENT,
        validator=_valid_secret, title_for=_secret_title, secret_group=3, skip_comments=False,
    ),
    SecurityRule(
        id="known-key-format", family="secret", title="Credential with a known key format", severity="high",
        description="The value matches the format of a real provider credential (OpenAI, AWS, GitHub, Slack or "
                    "Google).",
        recommendation="Revoke the key with the provider, remove it from history and load it from the environment.",
        confidence=0.95, cwe="CWE-798", kinds=EVERYTHING, pattern=KNOWN_KEY, secret_group=1, skip_comments=False,
    ),
    SecurityRule(
        id="private-key", family="secret", title="Private key committed to the repository", severity="critical",
        description="A PEM private key block is stored in the repository.",
        recommendation="Remove the key, rotate it, and store keys outside version control.",
        confidence=0.97, cwe="CWE-321", kinds=EVERYTHING,
        pattern=re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"),
        skip_comments=False,
    ),
    SecurityRule(
        id="sql-injection", family="injection", title="SQL query built with string concatenation", severity="high",
        description="User-controlled values are concatenated into a SQL statement, allowing SQL injection.",
        recommendation="Use parameterized queries (placeholders such as $1 or ?) instead of building SQL strings.",
        confidence=0.85, cwe="CWE-89", matcher=_sql_injection,
    ),
    SecurityRule(
        id="command-injection", family="injection", title="Shell command built from dynamic input", severity="high",
        description="A shell command is assembled from variables and executed, enabling command injection.",
        recommendation="Use execFile/spawn with an argument array and validate inputs against an allow-list.",
        confidence=0.8, cwe="CWE-78", languages=JS, matcher=_js_command_injection,
    ),
    SecurityRule(
        id="command-injection-py", family="injection", title="Shell command built from dynamic input",
        severity="high",
        description="A shell command is executed through the system shell with dynamic input.",
        recommendation="Call subprocess.run with a list of arguments and shell=False.",
        confidence=0.8, cwe="CWE-78", languages=PY, matcher=_py_command_injection,
    ),
    SecurityRule(
        id="unsafe-eval", family="injection", title="Dynamic code execution (eval)", severity="high",
        description="eval/new Function executes strings as code. If any part of the string comes from a request, "
                    "attackers can run arbitrary code on the server.",
        recommendation="Replace eval with explicit parsing or a safe, whitelisted rule engine.",
        confidence=0.88, cwe="CWE-95", languages=JS,
        pattern=re.compile(r"(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\(|\bset(?:Timeout|Interval)\s*\(\s*['\"`]"),
    ),
    SecurityRule(
        id="unsafe-eval-py", family="injection", title="Dynamic code execution (eval/exec)", severity="high",
        description="eval/exec runs strings as Python code and can lead to remote code execution.",
        recommendation="Use ast.literal_eval for data or explicit parsing; never evaluate request input.",
        confidence=0.85, cwe="CWE-95", languages=PY, pattern=re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
    ),
    SecurityRule(
        id="insecure-cors", family="cors", title="Permissive CORS policy", severity="medium",
        description="The API accepts cross-origin requests from any origin, so any website can call it from a "
                    "victim's browser.",
        recommendation="Restrict allowed origins to the known frontend domains.",
        confidence=0.82, cwe="CWE-942",
        pattern=re.compile(
            r"""(?i)(?:origin\s*:\s*['"`]\*['"`]|Access-Control-Allow-Origin['"]?\s*[,:]\s*['"]\*['"]|"""
            r"""allow_origins\s*=\s*\[\s*['"]\*['"]|\bcors\(\s*\)|CORS\(\s*app\s*\))"""
        ),
    ),
    SecurityRule(
        id="jwt-no-verify", family="jwt", title="JWT decoded without signature verification", severity="high",
        description="jwt.decode() only base64-decodes the token; it does not check the signature, so forged "
                    "tokens are accepted.",
        recommendation="Use jwt.verify() with the signing secret and an explicit algorithm list.",
        confidence=0.86, cwe="CWE-347", languages=JS,
        pattern=re.compile(r"""\bjwt\.decode\s*\(|algorithms\s*:\s*\[\s*['"]none['"]""", re.IGNORECASE),
    ),
    SecurityRule(
        id="jwt-no-verify-py", family="jwt", title="JWT signature verification disabled", severity="high",
        description="Token signature verification is disabled, so forged tokens are accepted.",
        recommendation="Keep signature verification enabled and pin the expected algorithms.",
        confidence=0.86, cwe="CWE-347", languages=PY,
        pattern=re.compile(r"""verify_signature['"]?\s*:\s*False|algorithms\s*=\s*\[\s*['"]none['"]"""),
    ),
    SecurityRule(
        id="exposed-env", family="exposure", title="Environment variables exposed", severity="high",
        description="The full process environment (which usually contains secrets) is returned or logged.",
        recommendation="Remove the endpoint/log statement or return an explicit allow-list of safe values.",
        confidence=0.9, cwe="CWE-200",
        pattern=re.compile(
            r"""res\.(?:json|send)\s*\(\s*process\.env\s*\)|console\.log\s*\(\s*process\.env\s*\)|"""
            r"""jsonify\(\s*(?:dict\()?os\.environ|return\s+(?:dict\()?os\.environ\b|print\(\s*os\.environ\s*\)"""
        ),
    ),
    SecurityRule(
        id="path-traversal", family="file", title="File path built from request input", severity="medium",
        description="A filesystem operation uses request parameters directly, enabling path traversal.",
        recommendation="Resolve paths against a fixed base directory and reject '..' segments.",
        confidence=0.75, cwe="CWE-22",
        pattern=re.compile(
            r"""\b(?:fs\.(?:readFile|readFileSync|createReadStream|writeFile|writeFileSync|unlink|unlinkSync|readdir)|"""
            r"""res\.sendFile|res\.download|send_file|open)\s*\([^)]*\b(?:req\.(?:params|query|body)|"""
            r"""request\.(?:args|form|json|GET|POST|files))"""
        ),
    ),
    SecurityRule(
        id="insecure-http", family="transport", title="Unencrypted HTTP endpoint", severity="medium",
        description="Data is sent over plain HTTP, exposing it (and any credentials) to network interception.",
        recommendation="Use HTTPS for every non-local endpoint.",
        confidence=0.8, cwe="CWE-319",
        pattern=re.compile(
            r"""['"`]http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|www\.w3\.org|schemas\.|json-schema\.org|"""
            r"""example\.(?:com|org|net))[^'"`\s]+"""
        ),
    ),
    SecurityRule(
        id="weak-hash", family="crypto", title="Weak hash algorithm (MD5/SHA-1)", severity="medium",
        description="MD5 and SHA-1 are broken for security purposes; password hashes built with them can be "
                    "cracked quickly.",
        recommendation="Use bcrypt, scrypt or Argon2 for passwords and SHA-256+ for integrity checks.",
        confidence=0.85, cwe="CWE-327",
        pattern=re.compile(r"""createHash\(\s*['"](?:md5|sha1)['"]|hashlib\.(?:md5|sha1)\s*\(""", re.IGNORECASE),
    ),
    SecurityRule(
        id="insecure-random", family="crypto", title="Insecure randomness for security values", severity="low",
        description="Math.random() is predictable and must not generate tokens, IDs or secrets.",
        recommendation="Use crypto.randomBytes / crypto.randomUUID (Node) or secrets (Python).",
        confidence=0.7, cwe="CWE-338", languages=JS,
        pattern=re.compile(r"(?i)(?:token|session|secret|password|nonce|otp)\w*\s*=.*Math\.random\(\)"),
    ),
    SecurityRule(
        id="dom-xss", family="xss", title="Unsanitized HTML injection (XSS)", severity="medium",
        description="Dynamic content is written into the DOM as HTML, allowing cross-site scripting.",
        recommendation="Use textContent or sanitize the HTML before inserting it.",
        confidence=0.78, cwe="CWE-79", languages=JS,
        pattern=re.compile(
            r"""\.innerHTML\s*=\s*(?!\s*['"][^'"$]*['"]\s*;?\s*$)|\.outerHTML\s*=|document\.write\s*\(|"""
            r"""dangerouslySetInnerHTML|insertAdjacentHTML\s*\("""
        ),
    ),
    SecurityRule(
        id="token-localstorage", family="storage", title="Auth token stored in localStorage", severity="low",
        description="Tokens in localStorage are readable by any script on the page, so one XSS bug leaks sessions.",
        recommendation="Prefer HttpOnly, Secure, SameSite cookies for session tokens.",
        confidence=0.7, cwe="CWE-922", languages=JS,
        pattern=re.compile(
            r"""localStorage\.setItem\(\s*['"](?:token|jwt|auth|authToken|access_token|accessToken|id_token|session)['"]""",
            re.IGNORECASE,
        ),
    ),
    SecurityRule(
        id="tls-disabled", family="transport", title="TLS certificate verification disabled", severity="medium",
        description="Certificate verification is turned off, enabling man-in-the-middle attacks.",
        recommendation="Keep verification enabled; configure a trusted CA bundle instead.",
        confidence=0.82, cwe="CWE-295", kinds=SOURCE_CONFIG,
        pattern=re.compile(
            r"""rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['"]?0|verify\s*=\s*False|"""
            r"""InsecureSkipVerify:\s*true"""
        ),
    ),
    SecurityRule(
        id="debug-enabled", family="config", title="Debug mode enabled", severity="low",
        description="Debug mode exposes stack traces and internals to users.",
        recommendation="Disable debug mode outside local development.",
        confidence=0.7, cwe="CWE-489", kinds=SOURCE_CONFIG,
        languages=frozenset({"python", "yaml", "ini", "toml", "dotenv"}),
        pattern=re.compile(r"\b(?:debug|DEBUG)\s*=\s*True\b"),
    ),
    SecurityRule(
        id="mass-assignment", family="validation", title="Request body passed directly to the data layer",
        severity="medium",
        description="The raw request body is persisted without validation, allowing unexpected fields to be set.",
        recommendation="Validate the payload and copy only allowed fields.",
        confidence=0.72, cwe="CWE-915",
        pattern=re.compile(
            r"""\.(?:create|insert|insertOne|update|updateOne|findOneAndUpdate|findByIdAndUpdate|save)\s*\(\s*req\.body\s*[,)]"""
        ),
    ),
    SecurityRule(
        id="prompt-injection", family="prompt-injection", title="Prompt-injection text targeting AI tools",
        severity="low",
        description="This text tries to give instructions to AI assistants that read the repository. Evo Code "
                    "treats repository content as untrusted data and did not follow it.",
        recommendation="Remove the instruction text and review who added it. Never let automated tools act on "
                       "repository-provided instructions.",
        confidence=0.8, cwe="CWE-1427", kinds=EVERYTHING, skip_comments=False,
        pattern=re.compile(
            r"""(?i)\b(?:ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+instructions|"""
            r"""disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|reveal\s+(?:your|the)\s+system\s+prompt|"""
            r"""send\s+(?:the|your)\s+(?:api\s+keys?|secrets?|credentials)\s+to)"""
        ),
    ),
)

FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "secret": ("secret", "api key", "hardcoded", "credential", "password", "token"),
    "injection": ("injection", "eval", "exec", "command", "sql"),
    "cors": ("cors", "origin"),
    "jwt": ("jwt", "signature"),
    "exposure": ("environment", "exposed", "leak"),
    "xss": ("xss", "innerhtml", "html"),
    "transport": ("http", "tls", "https", "certificate"),
    "crypto": ("md5", "sha1", "hash", "random"),
    "prompt-injection": ("prompt",),
}


def infer_family(title: str) -> str | None:
    lowered = title.lower()
    for family, keywords in FAMILY_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            return family
    return None
