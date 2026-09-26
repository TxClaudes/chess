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
    assert "1 games (1 to fit, 0 held out), depth 8" in out
    assert "current config.py" in out
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_fit_recovers_known_settings():
    """Fake games whose 'chess.com' accuracy comes from known settings: fitting should
    find settings that reproduce them far better than the defaults do."""
    import random
    rng = random.Random(1)
    target = {"accuracy_decay": 0.08, "accuracy_win_k": None, "accuracy_power": -1.5,
              "accuracy_floor": 20.0, "accuracy_offset": -3.0}
    games = []
    for n in range(24):
        wins, w = [50.0], 50.0
        for i in range(60):
            w = min(99.0, max(1.0, w + rng.gauss(0, 6)))
            wins.append(round(w, 2))
        entry = {"url": f"https://x/{n}", "chesscom": {}, "white_wins": wins,
                 "moves": [{"color": "white" if i % 2 == 0 else "black"} for i in range(60)]}
        g = calibrate.Game(entry)
        g.chesscom = g.accuracy(target)
        games.append(g)
    current = {k: getattr(calibrate.Config().classify, k) for k in calibrate.GRIDS}
    fitted = calibrate.fit_multi(games[:18], current, list(calibrate.GRIDS))
    assert calibrate.mae(games[18:], fitted) < 0.5
    assert calibrate.mae(games[18:], current) > 2 * calibrate.mae(games[18:], fitted)
