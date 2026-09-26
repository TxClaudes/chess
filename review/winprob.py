"""Evaluation -> win percentage, and per-move accuracy.

Evaluations are dicts {"type": "cp"|"mate", "value": int} from White's point of view.
A mate value of 0 means the side to move in that position is checkmated.
"""

import math


def white_win_pct(ev: dict, white_to_move: bool, k: float) -> float:
    """White's win% (0-100) for an evaluation (lichess formula for centipawns)."""
    if ev["type"] == "mate":
        value = ev["value"]
        if value == 0:
            return 0.0 if white_to_move else 100.0
        return 100.0 if value > 0 else 0.0
    return 100.0 / (1.0 + math.exp(-k * ev["value"]))


def pov(white_pct: float, color: str) -> float:
    return white_pct if color == "white" else 100.0 - white_pct


def subjective(ev: dict, color: str) -> int:
    """Eval value from `color`'s point of view (positive = good for `color`)."""
    return ev["value"] if color == "white" else -ev["value"]


def move_accuracy(win_drop: float) -> float:
    """lichess per-move accuracy from the win% lost by the move (includes lichess's +1 bonus)."""
    raw = 103.1668100711649 * math.exp(-0.04354415386753951 * max(0.0, win_drop)) - 3.166924740191411
    return min(100.0, max(0.0, raw + 1))
