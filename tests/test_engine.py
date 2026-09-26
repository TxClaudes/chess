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
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", "")
    monkeypatch.setattr(engine_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(engine_mod, "_FALLBACK_LOCATIONS", [])
    with pytest.raises(engine_mod.StockfishNotFound, match="config.py"):
        engine_mod.find_stockfish()


def test_bad_stockfish_path(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKFISH_PATH", str(tmp_path / "nope"))
    with pytest.raises(engine_mod.StockfishNotFound, match="STOCKFISH_PATH"):
        engine_mod.find_stockfish()


def _fake_engine(tmp_path):
    exe = tmp_path / "stockfish"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return str(exe)


def test_config_path_is_used(monkeypatch, tmp_path):
    exe = _fake_engine(tmp_path)
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", exe)
    assert engine_mod.find_stockfish() == exe


def test_env_var_overrides_config(monkeypatch, tmp_path):
    exe = _fake_engine(tmp_path)
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", "/somewhere/else")
    monkeypatch.setenv("STOCKFISH_PATH", exe)
    assert engine_mod.find_stockfish() == exe


def test_config_path_pointing_at_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", str(tmp_path))
    with pytest.raises(engine_mod.StockfishNotFound, match="folder"):
        engine_mod.find_stockfish()


def test_missing_config_path_falls_back_to_search(monkeypatch, tmp_path):
    exe = _fake_engine(tmp_path)
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(engine_mod.shutil, "which", lambda name: exe)
    assert engine_mod.find_stockfish() == exe


def test_missing_config_path_named_in_error_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.setattr(engine_mod.config, "STOCKFISH_PATH", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(engine_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(engine_mod, "_FALLBACK_LOCATIONS", [])
    with pytest.raises(engine_mod.StockfishNotFound, match="nope.exe"):
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
