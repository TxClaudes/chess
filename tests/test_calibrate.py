"""The calibration script, with chess.com's API replaced by canned data."""

import json
from pathlib import Path

import pytest

from tests.test_engine import needs_stockfish

calibrate = pytest.importorskip("tools.calibrate")

PGN = "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0"


@needs_stockfish
def test_fit_runs_on_canned_games(monkeypatch, tmp_path, capsys):
    def fake_fetch(url):
        if url.endswith("/archives"):
            return {"archives": ["https://api.chess.com/pub/player/x/games/2026/09"]}
        return {"games": [
            {"rules": "chess", "url": "https://www.chess.com/game/live/1", "pgn": PGN,
             "accuracies": {"white": 95.0, "black": 40.0},
             "white": {"rating": 800}, "black": {"rating": 800}},
            {"rules": "chess", "url": "https://www.chess.com/game/live/2", "pgn": PGN},  # not reviewed
        ]}

    monkeypatch.setattr(calibrate, "fetch_json", fake_fetch)
    monkeypatch.setattr(calibrate, "CACHE", tmp_path)
    monkeypatch.setattr("sys.argv", ["calibrate.py", "someone", "--games", "5", "--depth", "8"])
    assert calibrate.main() == 0
    out = capsys.readouterr().out
    assert "1 games, depth 8" in out
    assert "best fit" in out
    assert len(list(tmp_path.glob("*.json"))) == 1
