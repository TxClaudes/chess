"""Move classification, modelled on chess.com Game Review.

Works purely on analysis data (positions with engine lines), so it can be tested
without running Stockfish. All win percentages are from the mover's point of view.
"""

from dataclasses import dataclass

import chess

from config import ClassificationConfig
from review.material import PIECE_VALUES, leaves_piece_en_prise, line_sacrifice
from review.openings import is_book, opening_name
from review.winprob import move_accuracy, pov, subjective, white_win_pct

BRILLIANT = "brilliant"
GREAT = "great"
BEST = "best"
EXCELLENT = "excellent"
GOOD = "good"
BOOK = "book"
FORCED = "forced"
INACCURACY = "inaccuracy"
MISTAKE = "mistake"
MISS = "miss"
BLUNDER = "blunder"

ALL_CLASSES = [BRILLIANT, GREAT, BEST, EXCELLENT, GOOD, BOOK, FORCED, INACCURACY, MISTAKE, MISS, BLUNDER]

# Severity scale for the basic (point-loss) classes, mildest first.
_SEVERITY = [BEST, EXCELLENT, GOOD, INACCURACY, MISTAKE, BLUNDER]


def _milder(a: str, b: str) -> str:
    return a if _SEVERITY.index(a) <= _SEVERITY.index(b) else b


def _harsher(a: str, b: str) -> str:
    return a if _SEVERITY.index(a) >= _SEVERITY.index(b) else b


@dataclass
class MoveContext:
    """Everything the classifier needs about one move."""
    board: chess.Board          # position before the move
    move: chess.Move
    before: dict                # analysis of the position before the move
    after: dict                 # analysis of the position after the move
    prev_move: dict | None      # classified result of the previous ply (opponent's move)
    prev_uci: str | None


def drop_band(drop: float, cfg: ClassificationConfig) -> str:
    if drop <= 0:
        return BEST
    if drop <= cfg.excellent:
        return EXCELLENT
    if drop <= cfg.good:
        return GOOD
    if drop <= cfg.inaccuracy:
        return INACCURACY
    if drop <= cfg.mistake:
        return MISTAKE
    return BLUNDER


def point_loss_class(ev_before: dict, ev_after: dict, color: str, drop: float, cfg: ClassificationConfig) -> str:
    """Classify a non-top move by how much it lost, with special handling for mates."""
    before_mate = ev_before["type"] == "mate"
    after_mate = ev_after["type"] == "mate"
    sb = subjective(ev_before, color)
    sa = subjective(ev_after, color)

    if before_mate and after_mate:
        if sb > 0:
            if sa < 0:
                return BLUNDER
            # Ideal is mating one move sooner; count how many moves were wasted.
            wasted = sa - (sb - 1)
            if wasted <= 0:
                return BEST
            if wasted <= 2:
                return EXCELLENT
            if wasted <= 6:
                return GOOD
            return INACCURACY
        # Being mated: the best defence keeps the mate as far away as possible.
        if sa > 0:
            return BEST
        shortened = abs(sb) - abs(sa)
        if shortened <= 0:
            return BEST
        # Turning a long mate (which weaker players often fail to find) into an
        # easy short one is punished; shortening an already long mate is not.
        if abs(sa) <= cfg.easy_mate and shortened >= cfg.easy_mate_shortened:
            return MISTAKE
        return EXCELLENT if shortened <= 2 else GOOD

    if before_mate and not after_mate:
        if sb < 0:
            return BEST  # escaped a forced mate
        # Had a forced mate and let it go.
        if sa >= 800:
            return EXCELLENT
        if sa >= 400:
            return GOOD
        if sa >= 200:
            return INACCURACY
        if sa >= 0:
            return MISTAKE
        return BLUNDER

    if not before_mate and after_mate:
        if sa > 0:
            return BEST  # found a forced mate
        # Allowed a forced mate. In a position that was already hopeless only the
        # (small) win% drop counts; otherwise take the harsher of the two verdicts.
        if sa >= -2:
            table = BLUNDER
        elif sa >= -5:
            table = MISTAKE
        else:
            table = INACCURACY
        band = drop_band(drop, cfg)
        if sb <= -cfg.hopeless_cp:
            return band
        return _harsher(table, band)

    return drop_band(drop, cfg)


def _line_win(line: dict, white_to_move: bool, color: str, cfg: ClassificationConfig) -> float:
    return pov(white_win_pct(line["eval"], white_to_move, cfg.win_k), color)


def _accuracy_drop(ctx: MoveContext, cfg: ClassificationConfig, drop: float) -> float:
    """Win% lost by the move on the accuracy curve (which may be flatter than win_k's)."""
    if cfg.accuracy_win_k is None:
        return drop
    color = "white" if ctx.board.turn == chess.WHITE else "black"
    white_to_move = ctx.board.turn == chess.WHITE
    before = pov(white_win_pct(ctx.before["eval"], white_to_move, cfg.accuracy_win_k), color)
    after = pov(white_win_pct(ctx.after["eval"], not white_to_move, cfg.accuracy_win_k), color)
    return max(0.0, before - after)


def _is_recapture(ctx: MoveContext) -> bool:
    if not ctx.prev_uci:
        return False
    prev = chess.Move.from_uci(ctx.prev_uci)
    if prev.to_square != ctx.move.to_square or not ctx.board.is_capture(ctx.move):
        return False
    # The previous move must itself have been a capture on that square.
    last = ctx.board.copy()
    try:
        last.pop()
    except IndexError:
        return True  # no move stack (custom start); assume it was
    return last.is_capture(prev)


def _is_easy_capture(board: chess.Board, move: chess.Move) -> bool:
    """Taking an undefended piece, or a piece worth more than the capturer, is easy to find."""
    if not board.is_capture(move) or board.is_en_passant(move):
        return False
    captured = board.piece_type_at(move.to_square)
    capturer = board.piece_type_at(move.from_square)
    if PIECE_VALUES[captured] > PIECE_VALUES[capturer]:
        return True
    return not board.attackers(not board.turn, move.to_square)


def _escapes_cheaper_attacker(board: chess.Board, move: chess.Move) -> bool:
    """Moving a piece away from an attack by a cheaper piece is an obvious reaction."""
    piece = board.piece_at(move.from_square)
    value = PIECE_VALUES[piece.piece_type]
    return any(
        PIECE_VALUES[board.piece_type_at(sq)] < value
        for sq in board.attackers(not board.turn, move.from_square)
    )


def classify_move(ctx: MoveContext, cfg: ClassificationConfig, in_book: bool, trace: dict | None = None) -> dict:
    """Classify one move. If `trace` is a dict, the Great/Brilliant checks are recorded in it."""
    trace = {} if trace is None else trace
    board, move = ctx.board, ctx.move
    color = "white" if board.turn == chess.WHITE else "black"
    white_to_move = board.turn == chess.WHITE

    after_board = board.copy(stack=False)
    after_board.push(move)

    win_before = pov(white_win_pct(ctx.before["eval"], white_to_move, cfg.win_k), color)
    win_after = pov(white_win_pct(ctx.after["eval"], not white_to_move, cfg.win_k), color)
    drop = max(0.0, win_before - win_after)

    lines = ctx.before["lines"]
    best = lines[0] if lines else None
    played_uci = move.uci()
    top_played = best is not None and best["uci"] == played_uci
    legal_count = board.legal_moves.count()

    result = {
        "win_before": round(win_before, 2),
        "win_after": round(win_after, 2),
        "win_drop": round(drop, 2),
        "accuracy": round(move_accuracy(_accuracy_drop(ctx, cfg, drop), cfg.accuracy_decay), 1),
        "sacrifice": 0,
    }

    if legal_count == 1:
        return {**result, "classification": FORCED}
    if in_book:
        return {**result, "classification": BOOK}
    if after_board.is_checkmate():
        return {**result, "classification": BEST}

    base = BEST if top_played else point_loss_class(ctx.before["eval"], ctx.after["eval"], color, drop, cfg)
    classification = base

    # Great and Brilliant: the best (or nearly best) move, in a position that was not
    # already won anyway, and not a reflexive move out of check or a queen promotion.
    alt = next((l for l in lines if l["uci"] != played_uci), None)
    trace.update({
        "engine top move": best["san"] if best else None,
        "base class": base,
        f"loses at most {cfg.special_max_drop}%": drop <= cfg.special_max_drop,
        "has an alternative line": alt is not None,
        "not escaping check": not board.is_check(),
        "not a queen promotion": move.promotion != chess.QUEEN,
        f"win after >= {cfg.special_min_win_after}%": win_after >= cfg.special_min_win_after,
    })
    candidate = base in (BEST, EXCELLENT) and all(v for k, v in trace.items() if isinstance(v, bool))
    if candidate:
        alt_win = _line_win(alt, white_to_move, color, cfg)
        played_line = next((l for l in lines if l["uci"] == played_uci), None)
        played_win = _line_win(played_line, white_to_move, color, cfg) if played_line else win_after
        candidate = alt_win < cfg.already_winning
        trace.update({
            "alternative": f"{alt['san']} ({alt_win:.1f}%)",
            f"alternative < {cfg.already_winning}% (not already winning)": candidate,
        })

    if candidate:
        reply_pv = ctx.after["lines"][0]["pv"] if ctx.after["lines"] else []
        sacrifice = max(
            line_sacrifice(board, move, reply_pv, cfg.brilliant_pv_plies),
            leaves_piece_en_prise(board, move),
        )
        result["sacrifice"] = sacrifice
        # Keeping a forced mate when the alternative only keeps an advantage is critical
        # even though win% puts "+6" and "mate" only ~10% apart.
        played_ev = played_line["eval"] if played_line else ctx.after["eval"]
        keeps_mate = played_ev["type"] == "mate" and subjective(played_ev, color) > 0
        alt_mates = alt["eval"]["type"] == "mate" and subjective(alt["eval"], color) > 0
        gap = played_win - alt_win
        # When both moves are already winning, win% squeezes even "wins the queen vs
        # wins nothing" into a few percent, so a large centipawn gap also counts.
        both_cp = played_ev["type"] == "cp" and alt["eval"]["type"] == "cp"
        cp_gap = subjective(played_ev, color) - subjective(alt["eval"], color) if both_cp else None
        great_checks = {
            f"gap to alternative >= {cfg.great_gap}% (is {gap:.1f}), or >= {cfg.great_cp_gap} centipawns"
            f" (is {cp_gap if cp_gap is not None else 'n/a'}), or only move keeping a forced mate":
                gap >= cfg.great_gap
                or (cp_gap is not None and cp_gap >= cfg.great_cp_gap)
                or (keeps_mate and not alt_mates),
            "not a recapture": not _is_recapture(ctx),
            "not an easy capture": not _is_easy_capture(board, move),
            "not escaping a cheaper attacker": not _escapes_cheaper_attacker(board, move),
        }
        trace[f"sacrifice >= {cfg.brilliant_min_sacrifice} (is {sacrifice})"] = sacrifice >= cfg.brilliant_min_sacrifice
        trace.update(great_checks)
        if sacrifice >= cfg.brilliant_min_sacrifice:
            classification = BRILLIANT
        elif all(great_checks.values()):
            classification = GREAT

    # Miss: failed to punish the opponent's mistake, ending up roughly where
    # things stood before it (a worse result stays a Mistake/Blunder).
    prev = ctx.prev_move
    if (
        classification in (INACCURACY, MISTAKE, BLUNDER)
        and prev is not None
        and prev["win_drop"] >= cfg.miss_opponent_drop
        and drop >= cfg.miss_gave_back
    ):
        level_before_mistake = 100.0 - prev["win_before"]
        if win_after >= level_before_mistake - cfg.miss_tolerance:
            classification = MISS

    return {**result, "classification": classification}


def classify_game(plies, positions: list[dict], cfg: ClassificationConfig, standard_start: bool) -> list[dict]:
    """Classify every ply. `positions[i]` is the analysis before ply i (len = plies + 1)."""
    results = []
    board = chess.Board(positions[0]["fen"])
    in_book = standard_start
    prev_result = None
    prev_uci = None

    for ply in plies:
        move = chess.Move.from_uci(ply.uci)
        after_board = board.copy()
        after_board.push(move)
        in_book = in_book and is_book(after_board)

        ctx = MoveContext(board, move, positions[ply.index], positions[ply.index + 1], prev_result, prev_uci)
        res = classify_move(ctx, cfg, in_book)
        name = opening_name(after_board)
        if name:
            res["opening"] = {"eco": name[0], "name": name[1]}
        results.append(res)

        prev_result, prev_uci = res, ply.uci
        board = after_board

    return results
