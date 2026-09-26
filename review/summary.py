"""Game-level summary: accuracy, estimated game rating, phase grades, counts."""

import math
import statistics

import chess

from config import ClassificationConfig

from review.classify import (ALL_CLASSES, BEST, BLUNDER, EXCELLENT, GOOD, INACCURACY, MISTAKE)

COLORS = ("white", "black")


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else 0.0


def _harmonic_mean(values: list[float]) -> float:
    return len(values) / sum(1.0 / v for v in values) if values else 0.0


def volatility_weights(white_wins: list[float], n_moves: int) -> list[float]:
    """lichess move weights: the standard deviation of White's win% in a sliding window,
    so moves in sharp phases count more. `white_wins` has one entry per position."""
    window = max(2, min(8, math.ceil(len(white_wins) / 10)))
    windows = [white_wins[:window]] * max(0, window - 2)
    windows += [white_wins[i:i + window] for i in range(0, max(1, len(white_wins) - window + 1))]
    windows = windows[:n_moves]
    while len(windows) < n_moves:
        windows.append(white_wins[-window:])
    return [min(12.0, max(0.5, statistics.pstdev(w) if len(w) > 1 else 0.0)) for w in windows]


def aggregate_accuracy(accs: list[float], weights: list[float], cfg: ClassificationConfig | None = None) -> float:
    """One player's game accuracy from their per-move accuracies (see config.py)."""
    if cfg is None or cfg.accuracy_power is None:
        weighted = _weighted_mean(accs, weights)
        harmonic = _harmonic_mean([max(a, 10.0) for a in accs])
        return round((weighted + harmonic) / 2, 1)
    p = cfg.accuracy_power
    vals = [max(cfg.accuracy_floor, a, 0.1) for a in accs]
    total = sum(weights)
    if abs(p) < 1e-9:  # geometric mean
        mean = math.exp(sum(w * math.log(v) for v, w in zip(vals, weights)) / total)
    else:
        mean = (sum(w * v ** p for v, w in zip(vals, weights)) / total) ** (1 / p)
    return round(min(100.0, max(0.0, mean + cfg.accuracy_offset)), 1)


def game_accuracy(white_wins: list[float], moves: list[dict], cfg: ClassificationConfig | None = None) -> dict[str, float | None]:
    """Game accuracy per player: lichess's method by default, or the fitted power mean."""
    weights = volatility_weights(white_wins, len(moves))
    out: dict[str, float | None] = {}
    for color in COLORS:
        idx = [i for i, m in enumerate(moves) if m["color"] == color]
        out[color] = aggregate_accuracy([moves[i]["accuracy"] for i in idx], [weights[i] for i in idx], cfg) if idx else None
    return out


def _clamped_cp(ev: dict, white_to_move: bool) -> int:
    if ev["type"] == "mate":
        if ev["value"] == 0:  # side to move is checkmated
            return -1000 if white_to_move else 1000
        return 1000 if ev["value"] > 0 else -1000
    return max(-1000, min(1000, ev["value"]))


def _elo_from_acpl(acpl: float) -> float:
    return 3100 * math.exp(-0.01 * acpl)


def estimated_ratings(moves: list[dict], headers: dict[str, str]) -> dict[str, int | None]:
    """Rough single-game rating from average centipawn loss, pulled toward the PGN Elo.

    Formula as used by Chesskit (from a lichess forum rule of thumb). Noisy by nature.
    """
    losses = {c: [] for c in COLORS}
    for m in moves:
        white_moved = m["color"] == "white"
        before = _clamped_cp(m["eval_before"], white_moved)
        after = _clamped_cp(m["eval_after"], not white_moved)
        sign = 1 if white_moved else -1
        losses[m["color"]].append(max(0, min(1000, (before - after) * sign)))

    def pgn_elo(key: str) -> int | None:
        try:
            return int(headers.get(key, ""))
        except ValueError:
            return None

    elos = {"white": pgn_elo("WhiteElo"), "black": pgn_elo("BlackElo")}
    out: dict[str, int | None] = {}
    for color in COLORS:
        if len(losses[color]) < 5:
            out[color] = None
            continue
        acpl = sum(losses[color]) / len(losses[color])
        rating = elos[color] or elos["black" if color == "white" else "white"]
        estimate = _elo_from_acpl(acpl)
        if rating:
            expected_acpl = -100 * math.log(min(rating, 3100) / 3100)
            estimate = rating * math.exp(-0.005 * (acpl - expected_acpl))
        out[color] = int(round(max(100, min(3200, estimate))))
    return out


def _majors_minors(board: chess.Board) -> int:
    return sum(len(board.pieces(pt, c)) for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) for c in chess.COLORS)


def _backrank_sparse(board: chess.Board) -> bool:
    white = sum(1 for sq in chess.SquareSet(chess.BB_RANK_1) if board.color_at(sq) == chess.WHITE)
    black = sum(1 for sq in chess.SquareSet(chess.BB_RANK_8) if board.color_at(sq) == chess.BLACK)
    return white < 4 or black < 4


def move_phases(moves: list[dict]) -> list[str]:
    """Phase of each move, judged from the position before it (lichess Divider, simplified).

    Middlegame: few pieces left, a thinned-out back rank, or move 15 reached.
    Endgame: six or fewer queens/rooks/minor pieces left in total.
    """
    phases = []
    phase = "opening"
    for m in moves:
        board = chess.Board(m["fen_before"])
        if phase != "endgame" and _majors_minors(board) <= 6:
            phase = "endgame"
        elif phase == "opening" and (
            _majors_minors(board) <= 10 or _backrank_sparse(board) or board.fullmove_number >= 15
        ):
            phase = "middlegame"
        phases.append(phase)
    return phases


def _grade(accuracy: float) -> str:
    if accuracy >= 90:
        return BEST
    if accuracy >= 80:
        return EXCELLENT
    if accuracy >= 70:
        return GOOD
    if accuracy >= 55:
        return INACCURACY
    if accuracy >= 40:
        return MISTAKE
    return BLUNDER


def phase_grades(moves: list[dict], phases: list[str]) -> dict:
    out = {}
    for phase in ("opening", "middlegame", "endgame"):
        out[phase] = {}
        for color in COLORS:
            accs = [m["accuracy"] for m, p in zip(moves, phases) if p == phase and m["color"] == color]
            if accs:
                acc = round(sum(accs) / len(accs), 1)
                out[phase][color] = {"accuracy": acc, "grade": _grade(acc)}
            else:
                out[phase][color] = None
    return out


def classification_counts(moves: list[dict]) -> dict:
    counts = {c: {k: 0 for k in ALL_CLASSES} for c in COLORS}
    for m in moves:
        counts[m["color"]][m["classification"]] += 1
    return counts


def summarize(moves: list[dict], white_wins: list[float], headers: dict[str, str],
              cfg: ClassificationConfig | None = None) -> dict:
    phases = move_phases(moves)
    for m, p in zip(moves, phases):
        m["phase"] = p
    opening = None
    for m in moves:
        if "opening" in m:
            opening = m["opening"]
    return {
        "accuracy": game_accuracy(white_wins, moves, cfg),
        "estimated_rating": estimated_ratings(moves, headers),
        "phases": phase_grades(moves, phases),
        "counts": classification_counts(moves),
        "opening": opening,
    }
