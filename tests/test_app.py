import time

import pytest

import app as app_module
from tests.test_engine import needs_stockfish


@pytest.fixture
def client():
    return app_module.app.test_client()


def test_rejects_empty_pgn(client):
    r = client.post("/api/analyze", data={"pgn": "  "})
    assert r.status_code == 400


def test_rejects_bad_depth(client):
    r = client.post("/api/analyze", data={"pgn": "1. e4", "depth": "99"})
    assert r.status_code == 400


def test_missing_stockfish_returns_503(client, monkeypatch):
    monkeypatch.setenv("STOCKFISH_PATH", "/nonexistent/stockfish")
    r = client.post("/api/analyze", data={"pgn": "1. e4"})
    assert r.status_code == 503
    assert "STOCKFISH_PATH" in r.get_json()["error"]


def test_unknown_job(client):
    assert client.get("/api/jobs/nope").status_code == 404


def _wait(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").get_json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.2)
    raise AssertionError("job did not finish")


@needs_stockfish
def test_analysis_job_end_to_end(client):
    r = client.post("/api/analyze", data={"pgn": "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7#", "depth": "8"})
    assert r.status_code == 202
    body = _wait(client, r.get_json()["job_id"])
    assert body["status"] == "done"
    result = body["result"]
    assert [m["san"] for m in result["moves"]][-1] == "Qxf7#"
    assert result["moves"][5]["classification"] == "blunder"   # 3...Nf6?? allows mate in 1
    assert result["summary"]["counts"]["black"]["blunder"] == 1


@needs_stockfish
def test_analysis_job_reports_bad_pgn(client):
    r = client.post("/api/analyze", data={"pgn": "1. e4 e5 2. Ke3", "depth": "8"})
    body = _wait(client, r.get_json()["job_id"])
    assert body["status"] == "error"
    assert "illegal" in body["error"]


@needs_stockfish
def test_evaluate_move(client):
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    good = client.post("/api/evaluate-move", json={
        "fen": fen, "uci": "g1f3", "eval_before": {"type": "cp", "value": 30}, "best_uci": "g1f3", "depth": 8})
    assert good.get_json()["correct"] is True
    illegal = client.post("/api/evaluate-move", json={"fen": fen, "uci": "e1e3", "eval_before": {"type": "cp", "value": 30}})
    assert illegal.get_json() == {"legal": False}
