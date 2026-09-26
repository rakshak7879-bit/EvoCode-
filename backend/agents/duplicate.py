"""Duplicate Code Agent: finds repeated or near-identical implementations.

Method (deterministic):
1. Extract functions/methods from the parser (tests and tiny functions skipped).
2. Tokenize each body string-aware, drop comments, and normalize: local
   identifiers -> ``I``, strings -> ``S``, numbers -> ``N``; keywords, property
   and method names are kept because they carry meaning (``trim``, ``test``).
3. Compare 5-token shingles with Jaccard similarity; pairs above the threshold
   are clustered with union-find.
4. Prefer an existing shared implementation (utils/, lib/, shared/ ...) as the
   canonical copy, so the recommendation can point at code that already exists.

With an LLM configured, cluster explanations are optionally enriched.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import PurePosixPath

from agents.base import AgentResult, BaseAgent, FindingDraft, Location
from agents.context import AgentContext, SourceFile
from llm.prompts import redact_secrets, wrap_repository_content
from llm.provider import LLMError
from memory.tokens import split_identifier
from repo.parser import Symbol

SIMILARITY_THRESHOLD = 0.72
SHINGLE = 5
MIN_TOKENS = 25
MIN_LINES = 4
MAX_FUNCTIONS = 1500

_TOKEN = re.compile(
    r"""//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|"""
    r"""\d+(?:\.\d+)?|===|!==|==|!=|<=|>=|=>|&&|\|\||\+=|-=|\*=|\S"""
)
_PY_COMMENT = re.compile(r"#[^\n]*")
_REGEX_LITERAL = re.compile(r"[=(,:]\s*(/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\n\\\[])+/[gimsuy]*)")
KEYWORDS = frozenset(
    """async await break case catch class const continue default def del delete do elif else except export extends
    false finally for from function if import in instanceof is lambda let new none not null of or pass raise return
    self static super switch this throw true try typeof undefined var void while with yield and as assert global
    nonlocal True False None""".split()
)
KEEP_GLOBALS = frozenset(
    """String Number Boolean Object Array JSON Math Date Promise Error RegExp Map Set parseInt parseFloat console
    document window fetch localStorage sessionStorage require process Buffer len str int float dict list set tuple
    print isinstance range enumerate zip open sorted""".split()
)
SHARED_DIRS = frozenset({"utils", "util", "lib", "libs", "shared", "common", "helpers", "helper", "core"})
SHARED_STEMS = frozenset({"utils", "util", "helpers", "validation", "validators", "common", "shared"})
NAME_STOPWORDS = frozenset(
    """get set is has check validate valid validation verify handle do make create build send fetch post put request
    to from by on with the of for and new update process run compute calc calculate load save parse format strength
    helper util data value json api http sanitize""".split()
)
VALIDATION_WORDS = frozenset({"validate", "valid", "validation", "check", "is", "verify", "sanitize"})
REQUEST_WORDS = frozenset({"request", "fetch", "post", "send", "api", "http", "json", "get", "call"})
SENSITIVE_WORDS = frozenset({"password", "auth", "token", "card", "payment", "credential", "session", "secret"})
TRIVIAL_LITERALS = frozenset({"string", "number", "object", "function", "boolean", "undefined", "utf-8", "utf8"})


@dataclass
class FunctionSample:
    symbol: Symbol
    file: SourceFile
    tokens: list[str]
    shingles: frozenset[tuple[str, ...]]
    literals: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.symbol.file}:{self.symbol.line_start}"


@dataclass
class Cluster:
    members: list[FunctionSample]
    edges: list[tuple[FunctionSample, FunctionSample, float]] = field(default_factory=list)

    @property
    def similarity(self) -> float:
        return round(sum(e[2] for e in self.edges) / len(self.edges), 2) if self.edges else 1.0


def normalize(text: str, language: str) -> tuple[list[str], frozenset[str]]:
    if language == "python":
        text = _PY_COMMENT.sub("", text)
    raw = [t for t in _TOKEN.findall(text) if not t.startswith(("//", "/*"))]
    normalized: list[str] = []
    literals: set[str] = set(_REGEX_LITERAL.findall(text))
    previous = ""
    for token in raw:
        first = token[0]
        if first in "\"'`":
            normalized.append("S")
            if len(token) > 4:
                literals.add(token)
        elif first.isdigit():
            normalized.append("N")
        elif first.isalpha() or first in "_$":
            if token in KEYWORDS or token in KEEP_GLOBALS or previous == ".":
                normalized.append(token)
            else:
                normalized.append("I")
        else:
            normalized.append(token)
        previous = token
    return normalized, frozenset(literals)


def shingles(tokens: list[str], size: int = SHINGLE) -> frozenset[tuple[str, ...]]:
    if len(tokens) < size:
        return frozenset({tuple(tokens)})
    return frozenset(tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_shared_location(path: str) -> bool:
    pure = PurePosixPath(path.lower())
    return any(part in SHARED_DIRS for part in pure.parts[:-1]) or pure.stem in SHARED_STEMS


def collect_samples(context: AgentContext) -> list[FunctionSample]:
    samples: list[FunctionSample] = []
    for file in context.source_files:
        if file.parsed is None or "test" in file.tags:
            continue
        for symbol in file.parsed.symbols:
            if symbol.kind not in {"function", "method"} or symbol.length < MIN_LINES:
                continue
            body = "\n".join(file.lines[symbol.line_start - 1 : symbol.line_end])
            tokens, literals = normalize(body, file.language)
            if len(tokens) < MIN_TOKENS:
                continue
            samples.append(FunctionSample(symbol, file, tokens, shingles(tokens), literals))
            if len(samples) >= MAX_FUNCTIONS:
                return samples
    return samples


def find_clusters(samples: list[FunctionSample], threshold: float = SIMILARITY_THRESHOLD) -> list[Cluster]:
    parent = list(range(len(samples)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges: list[tuple[int, int, float]] = []
    for i, j in combinations(range(len(samples)), 2):
        a, b = samples[i], samples[j]
        if a.symbol.file == b.symbol.file and not (
            a.symbol.line_end < b.symbol.line_start or b.symbol.line_end < a.symbol.line_start
        ):
            continue  # nested / overlapping spans in the same file
        shorter, longer = sorted((len(a.tokens), len(b.tokens)))
        if shorter / longer < 0.6:
            continue
        score = jaccard(a.shingles, b.shingles)
        if score >= threshold:
            edges.append((i, j, score))
            parent[find(i)] = find(j)

    linked = {i for i, _, _ in edges} | {j for _, j, _ in edges}
    groups: dict[int, Cluster] = {}
    for index, sample in enumerate(samples):
        if index in linked:
            groups.setdefault(find(index), Cluster(members=[])).members.append(sample)
    for i, j, score in edges:
        groups[find(i)].edges.append((samples[i], samples[j], score))
    clusters = [c for c in groups.values() if len(c.members) >= 2]
    clusters.sort(key=lambda c: (-len(c.members), -c.similarity))
    return clusters


def describe_cluster(cluster: Cluster) -> tuple[str, str, bool]:
    """Return (title, purpose, sensitive) derived from member names."""
    word_sets = [set(split_identifier(m.symbol.name)) for m in cluster.members]
    common = set.intersection(*word_sets) if word_sets else set()
    nouns = [w for w in sorted(common) if w not in NAME_STOPWORDS and len(w) > 2]
    all_words = set().union(*word_sets) if word_sets else set()
    count = len(cluster.members)
    if all(ws & VALIDATION_WORDS for ws in word_sets):
        purpose = "validation"
    elif all(ws & REQUEST_WORDS for ws in word_sets):
        purpose = "request helper"
    else:
        purpose = "logic"
    if nouns:
        subject = nouns[0].capitalize()
        title = f"{subject} {purpose} duplicated in {count} places"
    elif purpose == "request helper":
        title = f"API request helper duplicated in {count} places"
    else:
        title = f"Similar {purpose} duplicated in {count} places"
    sensitive = bool(all_words & SENSITIVE_WORDS)
    return title, purpose, sensitive


def choose_canonical(cluster: Cluster) -> FunctionSample | None:
    shared = [m for m in cluster.members if is_shared_location(m.symbol.file)]
    if not shared:
        return None
    shared.sort(key=lambda m: (m.symbol.name not in (m.file.parsed.exports if m.file.parsed else ()), m.symbol.file))
    return shared[0]


class DuplicateAgent(BaseAgent):
    name = "duplicate"
    title = "Duplicate Code Agent"
    description = "Finds duplicated functions, repeated validation and copy-pasted helpers; points to existing shared code."

    async def run(self, context: AgentContext) -> AgentResult:
        samples = await asyncio.to_thread(collect_samples, context)
        clusters = await asyncio.to_thread(find_clusters, samples)
        cluster_payloads = [self._cluster_payload(index, cluster) for index, cluster in enumerate(clusters, start=1)]
        cluster_payloads.extend(self._identical_files(context, start=len(cluster_payloads) + 1))

        notes: list[str] = []
        mode = "local"
        if context.llm.available and cluster_payloads:
            try:
                await self._llm_enrich(context, cluster_payloads, clusters)
                mode = "llm"
                notes.append("LLM added semantic explanations; similarity scores remain deterministic.")
            except LLMError as exc:
                notes.append(f"LLM enrichment unavailable ({exc}); explanations are template-based.")
        else:
            notes.append("Structural token similarity (local analysis); explanations are template-based.")

        findings = [self._finding(payload) for payload in cluster_payloads]
        pairs = [pair for payload in cluster_payloads for pair in payload.pop("_pairs", [])]
        return AgentResult(
            agent=self.name,
            mode=mode,  # type: ignore[arg-type]
            summary=f"{len(cluster_payloads)} duplicate clusters · {len(samples)} functions compared",
            findings=findings,
            data={
                "clusters": cluster_payloads,
                "duplicates": pairs,
                "functions_compared": len(samples),
                "threshold": SIMILARITY_THRESHOLD,
            },
            notes=notes,
            metrics={"clusters": len(cluster_payloads), "functions_compared": len(samples)},
        )

    # ------------------------------------------------------------------ helpers
    def _cluster_payload(self, index: int, cluster: Cluster) -> dict:
        title, purpose, sensitive = describe_cluster(cluster)
        canonical = choose_canonical(cluster)
        ordered = sorted(cluster.members, key=lambda m: (m is not canonical, m.symbol.file, m.symbol.line_start))
        copies = [m for m in ordered if m is not canonical]
        shared_literals = sorted(
            (lit for lit in frozenset.intersection(*(m.literals for m in cluster.members))
             if lit.strip("'\"`") not in TRIVIAL_LITERALS),
            key=lambda lit: (not lit.startswith("/"), -len(lit)),  # distinctive regexes first
        )
        percent = round(cluster.similarity * 100)
        description = (
            f"{len(cluster.members)} functions share {percent}% of their normalized structure "
            "(identifiers renamed, comments ignored)"
        )
        if shared_literals:
            description += f" and the same literal {shared_literals[0][:60]}"
        description += ". Every copy must be fixed separately when the logic changes."
        exported = bool(canonical and canonical.file.parsed and canonical.symbol.name in canonical.file.parsed.exports)
        if canonical:
            copy_list = ", ".join(f"{m.symbol.file}:{m.symbol.line_start}" for m in copies)
            recommendation = (
                f"Reuse the existing `{canonical.symbol.name}` in {canonical.symbol.file}:{canonical.symbol.line_start}"
                f" (already implemented{' and exported' if exported else ''}). Replace the {len(copies)} local "
                f"cop{'y' if len(copies) == 1 else 'ies'}: {copy_list}."
            )
        else:
            recommendation = (
                f"Extract one shared helper (for example in a utils/ module) and replace the {len(cluster.members)} copies."
            )
        severity = "medium" if len(cluster.members) >= 3 or sensitive else "low"
        members = [
            {
                "file": m.symbol.file,
                "line_start": m.symbol.line_start,
                "line_end": m.symbol.line_end,
                "symbol": m.symbol.name,
                "role": "canonical" if m is canonical else "copy",
                "evidence": m.file.lines[m.symbol.line_start - 1].strip()[:200],
            }
            for m in ordered
        ]
        anchor = canonical or ordered[0]
        pairs = [
            {
                "file_a": anchor.symbol.file,
                "line_a": anchor.symbol.line_start,
                "file_b": other.symbol.file,
                "line_b": other.symbol.line_start,
                "similarity": round(self._pair_similarity(cluster, anchor, other), 2),
                "reason": f"`{anchor.symbol.name}` and `{other.symbol.name}` implement the same {purpose}.",
                "recommendation": recommendation,
            }
            for other in ordered
            if other is not anchor
        ]
        return {
            "id": f"dup-{index}",
            "kind": "function",
            "title": title,
            "purpose": purpose,
            "similarity": cluster.similarity,
            "severity": severity,
            "members": members,
            "canonical": members[0] if canonical else None,
            "shared_literals": [lit[:80] for lit in shared_literals[:3]],
            "reason": description,
            "recommendation": recommendation,
            "explanation_source": "template",
            "_pairs": pairs,
        }

    @staticmethod
    def _pair_similarity(cluster: Cluster, a: FunctionSample, b: FunctionSample) -> float:
        for x, y, score in cluster.edges:
            if {x.key, y.key} == {a.key, b.key}:
                return score
        return jaccard(a.shingles, b.shingles)

    def _identical_files(self, context: AgentContext, start: int) -> list[dict]:
        groups: dict[str, list[SourceFile]] = {}
        for file in context.source_files:
            if file.size >= 200 and "test" not in file.tags:
                groups.setdefault(file.sha256, []).append(file)
        payloads = []
        for offset, files in enumerate(g for g in groups.values() if len(g) > 1):
            members = [
                {"file": f.path, "line_start": 1, "line_end": max(1, len(f.lines)), "symbol": None,
                 "role": "copy", "evidence": (f.lines[0].strip() if f.lines else "")[:200]}
                for f in files
            ]
            payloads.append(
                {
                    "id": f"dup-{start + offset}",
                    "kind": "file",
                    "title": f"Identical file duplicated in {len(files)} places",
                    "purpose": "file",
                    "similarity": 1.0,
                    "severity": "low",
                    "members": members,
                    "canonical": None,
                    "shared_literals": [],
                    "reason": "These files have the same SHA-256 hash: they are byte-for-byte copies.",
                    "recommendation": "Keep one copy and import or symlink it from the other locations.",
                    "explanation_source": "template",
                    "_pairs": [],
                }
            )
        return payloads

    def _finding(self, payload: dict) -> FindingDraft:
        primary = next((m for m in payload["members"] if m["role"] == "copy"), payload["members"][0])
        return FindingDraft(
            agent=self.name,
            category="duplicate",
            severity=payload["severity"],
            title=payload["title"],
            description=payload["reason"],
            file=primary["file"],
            line=primary["line_start"],
            line_end=primary["line_start"],
            evidence=primary["evidence"],
            recommendation=payload["recommendation"],
            confidence=round(min(0.97, 0.55 + payload["similarity"] * 0.4), 2),
            source="heuristic",
            rule_id=f"duplicate-{payload['kind']}",
            family="duplicate",
            locations=[
                Location(file=m["file"], line=m["line_start"], line_end=m["line_start"], symbol=m["symbol"],
                         role=m["role"], evidence=m["evidence"])
                for m in payload["members"]
            ],
            similarity=payload["similarity"],
            detectors=["token-shingles"],
        )

    async def _llm_enrich(self, context: AgentContext, payloads: list[dict], clusters: list[Cluster]) -> None:
        blocks = []
        for payload, cluster in zip(payloads, clusters):
            for member in cluster.members[:3]:
                body = "\n".join(member.file.lines[member.symbol.line_start - 1 : member.symbol.line_start + 39])
                blocks.append((f"{payload['id']} · {member.symbol.file}:{member.symbol.line_start}", redact_secrets(body)))
        if not blocks:
            return
        system = (
            'For each duplicate cluster id, explain in one sentence what the functions do and why they are '
            'duplicates, and give a one-sentence refactoring recommendation. Return JSON: {"clusters": '
            '[{"id": str, "reason": str, "recommendation": str}]}'
        )
        result = await context.llm.complete_json(system=system, prompt=wrap_repository_content(blocks), max_tokens=900)
        by_id = {p["id"]: p for p in payloads}
        for item in result.get("clusters") or []:
            if not isinstance(item, dict):
                continue
            target = by_id.get(str(item.get("id")))
            reason = str(item.get("reason") or "").strip()
            if target and reason:
                target["reason"] = f"{reason[:400]} (Structural similarity {round(target['similarity'] * 100)}%.)"
                target["explanation_source"] = "llm"
