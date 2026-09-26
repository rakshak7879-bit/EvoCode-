from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import analyze_demo


def test_health_reports_local_mode(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["llm"]["mode"] == "local" and body["llm"]["label"] == "DEMO / LOCAL ANALYSIS"
    assert body["fts5"] is True and body["demo_repo_available"] is True


def test_analyze_zip_upload_end_to_end(client: TestClient, demo_zip: Path) -> None:
    with demo_zip.open("rb") as handle:
        response = client.post("/api/repository/analyze", files={"file": ("shoplite.zip", handle, "application/zip")})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing" and body["repository_id"]
    status = client.get(f"/api/repository/{body['repository_id']}").json()
    assert status["status"] == "completed", status["error"]
    assert status["files"] == 9 and status["agents_completed"] == 4 and status["findings"] > 0
    assert status["name"] == "shoplite" and status["source"] == "zip"
    metrics = status["metrics"]
    assert metrics["security_findings"] >= 10 and metrics["duplicate_clusters"] == 4
    assert metrics["verified_findings"] == metrics["total_findings"]
    assert metrics["memory_entries"] > metrics["files_analyzed"]
    stages = [entry["stage"] for entry in status["timeline"]]
    for stage in ("scanning", "indexing", "understanding", "retrieving", "routing", "running", "cross_validating",
                  "verifying", "memorizing", "completed"):
        assert stage in stages
    assert status["brain"]["state"] == "complete"


def test_analyze_rejects_bad_input(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/api/repository/analyze", data={}).status_code == 400
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    with bad.open("rb") as handle:
        response = client.post("/api/repository/analyze", files={"file": ("bad.zip", handle, "application/zip")})
    assert response.status_code == 400 and "not a valid ZIP" in response.json()["detail"]
    with bad.open("rb") as handle:
        response = client.post("/api/repository/analyze", files={"file": ("repo.tar", handle)})
    assert response.status_code == 400
    response = client.post("/api/repository/analyze", data={"github_url": "https://gitlab.com/a/b"})
    assert response.status_code == 400
    response = client.post("/api/repository/analyze", data={"github_url": "https://github.com/a/b", "use_demo": "true"})
    assert response.status_code == 400


def test_unsupported_repository_fails_gracefully(client: TestClient, tmp_path: Path) -> None:
    archive = tmp_path / "assets.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("logo.png", b"\x89PNG\r\n\x1a\n\x00\x00binary")
        zf.writestr("node_modules/lib/index.js", "module.exports = 1")
    with archive.open("rb") as handle:
        response = client.post("/api/repository/analyze", files={"file": ("assets.zip", handle, "application/zip")})
    assert response.status_code == 202
    status = client.get(f"/api/repository/{response.json()['repository_id']}").json()
    assert status["status"] == "failed"
    assert status["error"] == "No supported source files were found in this repository."
    assert status["brain"]["state"] == "error"


def test_status_and_unknown_repositories(client: TestClient) -> None:
    assert client.get("/api/repository/0123456789abcdef").status_code == 404
    assert client.get("/api/repository/not-a-valid-id!").status_code == 422
    assert client.get("/api/findings/0123456789abcdef").status_code == 404


def test_findings_contract(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    body = client.get(f"/api/findings/{repo_id}").json()
    assert set(body) >= {"security", "duplicates", "architecture", "verification", "cross_validation"}
    secret = next(f for f in body["security"] if f["title"] == "Hardcoded API key")
    assert secret["file"] == "backend/payments.js" and secret["severity"] == "high"
    assert secret["status"] == "verified" and len(secret["sha256"]) == 64 and len(secret["file_sha256"]) == 64
    assert secret["verification"]["message"] == "Source confirmed against current repository."
    duplicate = next(f for f in body["duplicates"] if f["title"].startswith("Email validation"))
    assert len(duplicate["locations"]) == 4 and all(l["verification"]["status"] == "verified"
                                                    for l in duplicate["locations"])
    assert body["verification"]["verified"] == body["verification"]["total"]
    assert body["architecture"]["architecture"]["database"]["evidence"][0]["verification"]["status"] == "verified"


def test_memory_search_answers_with_verified_citations(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    response = client.post("/api/memory/search",
                           json={"repository_id": repo_id, "query": "Where is authentication implemented?"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "local" and body["intent"] == "location"
    assert body["answer"].startswith("Authentication is implemented primarily in:")
    assert "backend/auth.js" in body["answer"]
    assert body["citations"][0]["file"] == "backend/auth.js"
    assert body["verification"]["verified"] == body["verification"]["total"] > 0
    assert "sk-demo-secret" not in response.text

    identifier = client.post("/api/memory/search", json={"repository_id": repo_id, "query": "API_KEY"}).json()
    assert identifier["answer"].startswith("`API_KEY` appears in")
    assert identifier["citations"][0]["file"] == "backend/payments.js"

    invalid = client.post("/api/memory/search", json={"repository_id": repo_id, "query": ""})
    assert invalid.status_code == 422


def test_walkthrough_endpoint(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    body = client.get(f"/api/walkthrough/{repo_id}").json()
    assert body["total_duration"] == 120 and len(body["steps"]) >= 6
    assert body["verification"]["verified"] == body["verification"]["total"] > 0
    assert body["steps"][0]["title"] == "Project Overview"


def test_source_viewer_and_path_safety(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    source = client.get(f"/api/source/{repo_id}", params={"path": "backend/payments.js"}).json()
    assert "const API_KEY" in source["content"] and source["changed"] is False
    assert source["sha256_current"] == source["sha256_indexed"]
    assert client.get(f"/api/source/{repo_id}", params={"path": "../../etc/passwd"}).status_code == 400
    assert client.get(f"/api/source/{repo_id}", params={"path": "/etc/passwd"}).status_code == 400
    assert client.get(f"/api/source/{repo_id}", params={"path": "backend/nope.js"}).status_code == 404


def test_stale_detection_and_memory_of_resolved_findings(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    before = client.get(f"/api/findings/{repo_id}").json()
    secret = next(f for f in before["security"] if f["title"] == "Hardcoded API key")
    edit = client.post(f"/api/source/{repo_id}/edit", json={
        "path": secret["file"], "line": secret["line"], "content": "const API_KEY = process.env.PAYMENT_API_KEY;"})
    assert edit.status_code == 200 and edit.json()["sha256_before"] != edit.json()["sha256_after"]

    after = client.get(f"/api/findings/{repo_id}").json()
    stale = next(f for f in after["security"] if f["id"] == secret["id"])
    assert stale["status"] == "stale" and stale["verification"]["lines_changed"] is True
    same_file = [f for f in after["security"] if f["file"] == secret["file"] and f["id"] != secret["id"]]
    assert same_file and all(f["status"] == "verified" for f in same_file)
    assert after["verification"]["stale"] == 1

    source = client.get(f"/api/source/{repo_id}", params={"path": secret["file"]}).json()
    assert source["changed"] is True

    assert client.post(f"/api/repository/{repo_id}/reanalyze").status_code == 202
    status = client.get(f"/api/repository/{repo_id}").json()
    assert status["status"] == "completed" and status["analysis_count"] == 2
    resolved = status["report"]["history"]["resolved"]
    assert [r["title"] for r in resolved] == ["Hardcoded API key"]
    refreshed = client.get(f"/api/findings/{repo_id}").json()
    assert all(f["title"] != "Hardcoded API key" for f in refreshed["security"])
    assert refreshed["verification"]["stale"] == 0
    history = client.post("/api/memory/search", json={"repository_id": repo_id, "query": "resolved API key"}).json()
    assert any("Resolved since the previous analysis" in r["snippet"] for r in history["related"])


def test_source_edit_validation(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    url = f"/api/source/{repo_id}/edit"
    assert client.post(url, json={"path": "backend/auth.js", "line": 9999, "content": "x"}).status_code == 400
    assert client.post(url, json={"path": "backend/auth.js", "line": 1, "content": "a\nb"}).status_code == 400
    assert client.post(url, json={"path": "../x.js", "line": 1, "content": "x"}).status_code == 400


def test_source_edits_can_be_disabled(client_factory) -> None:  # type: ignore[no-untyped-def]
    client: TestClient = client_factory(allow_source_edits=False)
    repo_id = analyze_demo(client)
    response = client.post(f"/api/source/{repo_id}/edit", json={"path": "backend/auth.js", "line": 1, "content": "x"})
    assert response.status_code == 403


def test_simulated_agent_failure_degrades_gracefully(client_factory) -> None:  # type: ignore[no-untyped-def]
    client: TestClient = client_factory(simulate_agent_failure=("walkthrough",))
    repo_id = analyze_demo(client)
    status = client.get(f"/api/repository/{repo_id}").json()
    assert status["status"] == "completed"
    walkthrough = next(a for a in status["agents"] if a["name"] == "walkthrough")
    assert walkthrough["status"] == "failed"
    assert status["agents_completed"] == 3
    assert client.get(f"/api/walkthrough/{repo_id}").status_code == 404
    assert client.get(f"/api/findings/{repo_id}").json()["security"]


def test_task_routing_limits_agents(client: TestClient) -> None:
    repo_id = analyze_demo(client, task="Only run a security audit")
    status = client.get(f"/api/repository/{repo_id}").json()
    agents = {a["name"]: a["status"] for a in status["agents"]}
    assert agents == {"security": "complete", "duplicate": "skipped", "explainer": "skipped", "walkthrough": "skipped"}
    assert status["agents_total"] == 1


def test_repositories_list(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    listing = client.get("/api/repositories").json()
    assert listing[0]["repository_id"] == repo_id and listing[0]["status"] == "completed"
