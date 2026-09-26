import pytest

from config import Config, EngineConfig
from review import engine as engine_mod
from review.analyze import analyze_pgn

try:
    engine_mod.find_stockfish()
    HAVE_STOCKFISH = True
except engine_mod.StockfishNotFound:
    HAVE_STOCKFISH = False

needs_stockfish = pytest.mark.skipif(not HAVE_STOCKFISH, reason="Stockfish not installed")


def test_missing_stockfish_gives_clear_error(monkeypatch):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.setattr(engine_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(engine_mod, "_FALLBACK_LOCATIONS", [])
    with pytest.raises(engine_mod.StockfishNotFound, match="apt install stockfish"):
        engine_mod.find_stockfish()


def test_bad_stockfish_path(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKFISH_PATH", str(tmp_path / "nope"))
    with pytest.raises(engine_mod.StockfishNotFound, match="STOCKFISH_PATH"):
        engine_mod.find_stockfish()


@needs_stockfish
def test_scholars_mate_pipeline():
    config = Config(engine=EngineConfig(depth=10, workers=2))
    result = analyze_pgn("1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7#", config)

    assert len(result["positions"]) == 8
    assert len(result["moves"]) == 7

    # After 3...Nf6?? White has mate in 1.
    assert result["moves"][5]["eval_after"] == {"type": "mate", "value": 1}
    assert result["moves"][5]["best_move"] is not None
    # Final position: Black is checkmated, no engine lines.
    assert result["positions"][-1]["eval"] == {"type": "mate", "value": 0}
    assert result["positions"][-1]["lines"] == []
    # Before the mate the engine suggests Qxf7#.
    assert result["moves"][6]["best_move"]["san"] == "Qxf7#"
    # MultiPV=2 gives two lines where there is a choice.
    assert len(result["positions"][0]["lines"]) == 2
