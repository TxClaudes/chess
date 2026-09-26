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


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_explore_legal_moves_without_engine(client):
    body = client.post("/api/explore", json={"fen": START, "analyse": False}).get_json()
    assert len(body["legal_moves"]) == 20
    assert "position" not in body


def test_explore_rejects_illegal_move(client):
    assert client.post("/api/explore", json={"fen": START, "uci": "e2e5"}).get_json() == {"legal": False}


def test_explore_auto_queens(client):
    fen = "7k/4P3/8/8/8/8/8/K7 w - - 0 1"
    body = client.post("/api/explore", json={"fen": fen, "uci": "e7e8", "analyse": False}).get_json()
    assert body["uci"] == "e7e8q" and body["san"].startswith("e8=Q")


def test_explore_reports_checkmate(client):
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"
    body = client.post("/api/explore", json={"fen": fen, "uci": "d8h4", "analyse": False}).get_json()
    assert body["result"] == "Checkmate" and body["legal_moves"] == []


@needs_stockfish
def test_explore_analyses_and_labels_the_move(client):
    first = client.post("/api/explore", json={"fen": START, "depth": 8}).get_json()
    assert first["position"]["lines"]
    body = client.post("/api/explore", json={
        "fen": START, "uci": "g2g4", "depth": 8, "before": first["position"]}).get_json()
    assert body["san"] == "g4"
    assert body["classification"]["classification"] in ("inaccuracy", "mistake", "blunder")
    assert body["position"]["eval"]["type"] == "cp"
