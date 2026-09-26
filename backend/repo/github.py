"""Download public GitHub repositories as ZIP archives (no git, no code execution).

Only ``https://github.com/<owner>/<repo>`` URLs are accepted. The download URL
is constructed by Evo Code (never taken from user input verbatim) and every
request, including redirects, is restricted to GitHub hosts over HTTPS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

ALLOWED_HOSTS = frozenset({"api.github.com", "codeload.github.com", "github.com"})
_GITHUB_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100})(?:/(?:tree|commit)/(?P<ref>[A-Za-z0-9._/-]{1,200}))?/?$"
)


class GitHubError(RuntimeError):
    """The GitHub URL is invalid or the repository could not be downloaded."""


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str
    ref: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def archive_url(self, authenticated: bool = False) -> str:
        """Archive download URL.

        Without a token the public web archive (github.com -> codeload.github.com)
        is used because it is not subject to the 60 requests/hour REST API limit.
        With a token the REST zipball endpoint is used (supports private repos).
        """
        if authenticated:
            base = f"https://api.github.com/repos/{self.owner}/{self.repo}/zipball"
            return f"{base}/{quote(self.ref, safe='')}" if self.ref else base
        return f"https://github.com/{self.owner}/{self.repo}/archive/{quote(self.ref or 'HEAD', safe='/')}.zip"


def parse_github_url(url: str) -> GitHubRepoRef:
    candidate = (url or "").strip()
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    match = _GITHUB_URL.match(candidate)
    if not match or match.group("repo") in {".", ".."}:
        raise GitHubError("Enter a public GitHub repository URL such as https://github.com/owner/repo")
    return GitHubRepoRef(match.group("owner"), match.group("repo"), match.group("ref"))


async def _enforce_host(request: httpx.Request) -> None:
    if request.url.scheme != "https" or request.url.host not in ALLOWED_HOSTS:
        raise GitHubError(f"Refusing to download from unexpected host: {request.url.host}")


async def download_repository_zip(
    ref: GitHubRepoRef,
    destination: Path,
    *,
    max_bytes: int,
    token: str = "",
    timeout: float = 60.0,
) -> int:
    """Stream the repository archive to ``destination``; returns the byte count."""
    headers = {"User-Agent": "evo-code-mvp"}
    if token:
        headers["Accept"] = "application/vnd.github+json"
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            event_hooks={"request": [_enforce_host]},
        ) as client:
            async with client.stream("GET", ref.archive_url(authenticated=bool(token)), headers=headers) as response:
                if response.status_code == 404:
                    raise GitHubError(
                        "Repository or branch not found. Private repositories require GITHUB_TOKEN."
                    )
                if response.status_code in (403, 429):
                    raise GitHubError("GitHub rate limit reached. Try again later or set GITHUB_TOKEN.")
                if response.status_code >= 400:
                    raise GitHubError(f"GitHub returned HTTP {response.status_code}.")
                total = 0
                with destination.open("wb") as sink:
                    async for chunk in response.aiter_bytes(65536):
                        total += len(chunk)
                        if total > max_bytes:
                            raise GitHubError(
                                f"Repository archive exceeds the {max_bytes // (1024 * 1024)} MB limit."
                            )
                        sink.write(chunk)
                return total
    except httpx.HTTPError as exc:
        raise GitHubError(f"Could not download the repository from GitHub ({exc.__class__.__name__}).") from exc
