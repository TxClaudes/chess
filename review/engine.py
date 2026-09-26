"""Stockfish discovery and position analysis."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
import queue
import shutil
import threading
from typing import Callable

import chess
import chess.engine

from config import EngineConfig

# Checked after STOCKFISH_PATH and PATH. Debian/Ubuntu install to /usr/games,
# which is often not on PATH.
_FALLBACK_LOCATIONS = [
    "/usr/games/stockfish",
    "/usr/local/bin/stockfish",
    "/opt/homebrew/bin/stockfish",
]

INSTALL_HINT = """Stockfish was not found.

Install it and make sure the `stockfish` binary is on your PATH, or point
STOCKFISH_PATH at it:
  Ubuntu/Debian: sudo apt install stockfish
  macOS:         brew install stockfish
  Windows:       download from https://stockfishchess.org/download/ and set
                 STOCKFISH_PATH=C:\\path\\to\\stockfish.exe"""


class StockfishNotFound(RuntimeError):
    pass


def find_stockfish() -> str:
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path:
        if os.path.isfile(env_path) and os.access(env_path, os.X_OK):
            return env_path
        raise StockfishNotFound(f"STOCKFISH_PATH is set to {env_path!r}, but that is not an executable file.")

    found = shutil.which("stockfish")
    if found:
        return found

    for candidate in _FALLBACK_LOCATIONS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    raise StockfishNotFound(INSTALL_HINT)


@dataclass(frozen=True)
class Eval:
    """Engine evaluation from White's point of view.

    kind "cp": value is centipawns. kind "mate": value is moves to mate, positive
    when White mates. A value of 0 means the side to move is already checkmated.
    """
    kind: str
    value: int

    def to_dict(self) -> dict:
        return {"type": self.kind, "value": self.value}

    @classmethod
    def from_pov(cls, score: chess.engine.PovScore) -> "Eval":
        white = score.white()
        if white.is_mate():
            return cls("mate", white.mate())
        return cls("cp", white.score())


@dataclass
class Line:
    uci: str
    san: str
    eval: Eval
    pv: list[str]          # UCI moves, starting with `uci`


@dataclass
class PositionAnalysis:
    fen: str
    lines: list[Line] = field(default_factory=list)   # best first; empty if game over
    eval: Eval = Eval("cp", 0)                         # eval of the best line, or terminal result
    legal_moves: int = 0


def _terminal_analysis(board: chess.Board) -> PositionAnalysis:
    if board.is_checkmate():
        ev = Eval("mate", 0)
    else:
        ev = Eval("cp", 0)
    return PositionAnalysis(board.fen(), [], ev, 0)


class Analyzer:
    """Pool of single-threaded Stockfish processes, safe to use from any thread."""

    def __init__(self, config: EngineConfig, path: str | None = None):
        self.config = config
        self.path = path or find_stockfish()
        self._idle: queue.SimpleQueue[chess.engine.SimpleEngine] = queue.SimpleQueue()
        self._engines: list[chess.engine.SimpleEngine] = []
        self._lock = threading.Lock()

    def _checkout(self) -> chess.engine.SimpleEngine:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        engine = chess.engine.SimpleEngine.popen_uci(self.path)
        engine.configure({"Threads": 1, "Hash": self.config.hash_mb})
        with self._lock:
            self._engines.append(engine)
        return engine

    def _limit(self, depth: int | None = None) -> chess.engine.Limit:
        if depth is not None:
            return chess.engine.Limit(depth=depth)
        if self.config.nodes:
            return chess.engine.Limit(nodes=self.config.nodes)
        return chess.engine.Limit(depth=self.config.depth)

    def analyse(self, fen: str, depth: int | None = None) -> PositionAnalysis:
        board = chess.Board(fen)
        legal = board.legal_moves.count()
        if board.is_game_over(claim_draw=False):
            return _terminal_analysis(board)

        engine = self._checkout()
        try:
            # A fresh `game` sends ucinewgame, clearing the hash so results do not
            # depend on which positions this process happened to search before.
            infos = engine.analyse(board, self._limit(depth), multipv=min(self.config.multipv, legal), game=object())
        except chess.engine.EngineTerminatedError:
            # Drop the dead process; the next call starts a fresh one.
            with self._lock:
                self._engines.remove(engine)
            raise
        self._idle.put(engine)
        lines = []
        for info in infos:
            pv = info.get("pv") or []
            if not pv:
                continue
            lines.append(Line(pv[0].uci(), board.san(pv[0]), Eval.from_pov(info["score"]), [m.uci() for m in pv]))

        return PositionAnalysis(fen, lines, lines[0].eval, legal)

    def analyse_many(self, fens: list[str], progress: Callable[[int, int], None] | None = None) -> list[PositionAnalysis]:
        results: list[PositionAnalysis | None] = [None] * len(fens)
        done = 0

        def work(i: int):
            results[i] = self.analyse(fens[i])

        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            for _ in pool.map(work, range(len(fens))):
                done += 1
                if progress:
                    progress(done, len(fens))
        return results  # type: ignore[return-value]

    def close(self):
        with self._lock:
            while True:
                try:
                    self._idle.get_nowait()
                except queue.Empty:
                    break
            for engine in self._engines:
                try:
                    engine.quit()
                except chess.engine.EngineError:
                    pass
            self._engines.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
