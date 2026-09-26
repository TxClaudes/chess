"""Tunable settings for the analysis pipeline.

Everything that affects move labels lives here so it can be adjusted in one place.
Win percentages are on a 0-100 scale throughout.
"""

from dataclasses import dataclass, field
import os

# Full path to the Stockfish program file (not its folder). Leave empty to look on
# your PATH and in the usual install locations. The STOCKFISH_PATH environment
# variable, if set, takes priority over this. Examples:
#   STOCKFISH_PATH = r"C:\Tools\stockfish\stockfish-windows-x86-64-avx2.exe"
#   STOCKFISH_PATH = "/home/me/stockfish/stockfish"
STOCKFISH_PATH = ""


@dataclass
class EngineConfig:
    # Search limit per position. If `nodes` is set it takes precedence over `depth`
    # (chess.com uses a fixed node count for consistency between positions).
    depth: int = 18
    nodes: int | None = None
    # Each worker is a separate single-threaded Stockfish process analysing different
    # positions. Single-threaded search keeps results reproducible.
    workers: int = max(1, min(4, (os.cpu_count() or 2) - 1))
    hash_mb: int = 64
    # Two lines per position: the second one drives "Great" and "Brilliant".
    multipv: int = 2


@dataclass
class ClassificationConfig:
    # Slope of the logistic win% curve (lichess value).
    win_k: float = 0.00368208

    # Steepness of the per-move accuracy curve. lichess uses 0.0435; chess.com's
    # accuracy is harsher. 0.055 is the fit from tools/calibrate.py on DerTeXx's
    # reviewed games (Stockfish 19, depth 18). Refit it with that script.
    accuracy_decay: float = 0.055

    # Maximum win% drop for each class. Anything above `mistake` is a blunder.
    # Bands follow chess.com's published expected-points table.
    excellent: float = 2.0
    good: float = 5.0
    inaccuracy: float = 10.0
    mistake: float = 20.0

    # Great / Brilliant shared gates.
    special_max_drop: float = 2.0          # the move itself may lose at most this much
    special_min_win_after: float = 45.0    # mover must not be worse than roughly equal afterwards
    already_winning: float = 97.0          # second-best already this good -> nothing special
    great_gap: float = 10.0                # every alternative must be at least this much worse
    great_cp_gap: int = 300                # ...or at least this many centipawns worse (winning a piece)

    # Brilliant: minimum material (pawn units) given up over the engine line.
    brilliant_min_sacrifice: int = 2       # 2 excludes plain pawn sacrifices
    brilliant_pv_plies: int = 8            # how far into the engine line to count material

    # Mates. Allowing a mate from a position already worse than -hopeless_cp is judged
    # by win% only. A defender who turns a long mate into one of <= easy_mate moves,
    # shortening it by >= easy_mate_shortened, gets a Mistake.
    hopeless_cp: int = 600
    easy_mate: int = 3
    easy_mate_shortened: int = 3

    # Miss: the opponent's previous move lost at least `miss_opponent_drop`, this move
    # lost at least `miss_gave_back`, and the mover ended up about where they were
    # before the opponent's mistake (otherwise it stays a Mistake/Blunder).
    miss_opponent_drop: float = 10.0
    miss_gave_back: float = 10.0
    miss_tolerance: float = 5.0            # ending up at most this much below the pre-mistake level still counts


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    classify: ClassificationConfig = field(default_factory=ClassificationConfig)
