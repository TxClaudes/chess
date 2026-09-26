"""Classifier tests on hand-made analysis data (no engine needed)."""

import chess
import pytest

from config import ClassificationConfig
from review import classify as C
from review.material import leaves_piece_en_prise, line_sacrifice
from review.winprob import move_accuracy, white_win_pct

CFG = ClassificationConfig()


def cp(v):
    return {"type": "cp", "value": v}


def mate(v):
    return {"type": "mate", "value": v}


def line(uci, ev, pv=None):
    return {"uci": uci, "san": "", "eval": ev, "pv": pv or [uci]}


def pos(fen, ev, lines=()):
    return {"fen": fen, "eval": ev, "lines": list(lines)}


def run(fen, uci, before_lines, after_eval, after_pv=None, prev=None, prev_uci=None, in_book=False, board=None):
    board = board or chess.Board(fen)
    move = chess.Move.from_uci(uci)
    after = board.copy()
    after.push(move)
    before = pos(fen, before_lines[0]["eval"], before_lines)
    after_lines = [line(after_pv[0], after_eval, after_pv)] if after_pv else []
    after_pos = pos(after.fen(), after_eval, after_lines)
    ctx = C.MoveContext(board, move, before, after_pos, prev, prev_uci)
    return C.classify_move(ctx, CFG, in_book)


# --- win% and accuracy ------------------------------------------------------

def test_win_pct_formula():
    assert white_win_pct(cp(0), True, CFG.win_k) == pytest.approx(50)
    assert white_win_pct(cp(300), True, CFG.win_k) == pytest.approx(75.1, abs=0.1)
    assert white_win_pct(mate(3), False, CFG.win_k) == 100
    assert white_win_pct(mate(-2), True, CFG.win_k) == 0
    # mate 0: the side to move is checkmated
    assert white_win_pct(mate(0), True, CFG.win_k) == 0
    assert white_win_pct(mate(0), False, CFG.win_k) == 100


def test_move_accuracy_bounds():
    assert move_accuracy(0) == 100
    assert 0 <= move_accuracy(80) < 5
    assert move_accuracy(5) > move_accuracy(10)


# --- basic bands ------------------------------------------------------------

@pytest.mark.parametrize("drop,expected", [
    (0, C.BEST), (1.5, C.EXCELLENT), (4, C.GOOD), (7, C.INACCURACY), (15, C.MISTAKE), (30, C.BLUNDER),
])
def test_drop_bands(drop, expected):
    assert C.drop_band(drop, CFG) == expected


def test_mate_tables():
    # White had mate in 3, still mates in 2: ideal.
    assert C.point_loss_class(mate(3), mate(2), "white", 0, CFG) == C.BEST
    # White had mate in 2, now mate in 7: wasted moves.
    assert C.point_loss_class(mate(2), mate(7), "white", 0, CFG) == C.GOOD
    # Black (mated in 4 from White's view is +4) lets go of a forced mate (from Black's side it's -).
    assert C.point_loss_class(mate(-3), cp(-250), "black", 10, CFG) == C.INACCURACY
    # Allowing mate in 1 from an equal position is a blunder.
    assert C.point_loss_class(cp(0), mate(-1), "white", 50, CFG) == C.BLUNDER
    # ...but not when the position was already lost (win% drop tiny).
    assert C.point_loss_class(cp(-1500), mate(-1), "white", 0.4, CFG) == C.EXCELLENT


# --- special classes -------------------------------------------------------

START = chess.STARTING_FEN


def test_top_move_is_best_despite_eval_noise():
    res = run(START, "e2e4", [line("e2e4", cp(40)), line("d2d4", cp(35))], cp(10))
    assert res["classification"] == C.BEST


def test_forced_and_book():
    fen = "k7/8/8/8/8/8/r7/7K w - - 0 1"  # rook on the 2nd rank leaves only Kg1
    board = chess.Board(fen)
    only = list(board.legal_moves)
    assert len(only) == 1
    res = run(fen, only[0].uci(), [line(only[0].uci(), cp(-900))], cp(-900))
    assert res["classification"] == C.FORCED

    res = run(START, "e2e4", [line("d2d4", cp(40)), line("e2e4", cp(35))], cp(35), in_book=True)
    assert res["classification"] == C.BOOK


def test_great_only_move():
    # Quiet move; the alternative is far worse and the position was not already won.
    res = run(START, "g1f3", [line("g1f3", cp(50)), line("b1c3", cp(-300))], cp(50))
    assert res["classification"] == C.GREAT


def test_no_great_when_alternative_already_winning():
    res = run(START, "g1f3", [line("g1f3", cp(2000)), line("b1c3", cp(1100))], cp(2000))
    assert res["classification"] == C.BEST


def test_no_great_for_taking_free_piece():
    fen = "rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"  # queen hangs to Nxh4
    res = run(fen, "f3h4", [line("f3h4", cp(800)), line("d2d4", cp(-50))], cp(800))
    assert res["classification"] == C.BEST


def test_brilliant_piece_left_en_prise():
    # Opera Game before 10.Nxb5: the knight lands where the c6 pawn can take it.
    fen = "rn2kb1r/p3qppp/2p2n2/1p2p1B1/2B1P3/1QN5/PPP2PPP/R3K2R w KQkq - 0 10"
    board = chess.Board(fen)
    move = chess.Move.from_uci("c3b5")
    assert leaves_piece_en_prise(board, move) == 3
    res = run(fen, "c3b5", [line("c3b5", cp(300)), line("c4d5", cp(150))], cp(300),
              after_pv=["c6b5", "c4b5", "b8d7", "e1c1"])
    assert res["classification"] == C.BRILLIANT


def test_queen_sacrifice_into_mate_counts_material():
    # Opera Game before 16.Qb8+: Qb8+ Nxb8 Rd8#.
    fen = "4kb1r/p2n1ppp/4q3/4p1B1/4P3/1Q6/PPP2PPP/2KR4 w k - 0 16"
    board = chess.Board(fen)
    assert line_sacrifice(board, chess.Move.from_uci("b3b8"), ["d7b8", "d1d8"], 8) == 9


def test_trade_is_not_a_sacrifice():
    fen = "rnbqkb1r/pppp1ppp/5n2/4p1B1/4P3/8/PPPP1PPP/RN1QKBNR w KQkq - 0 3"
    board = chess.Board(fen)
    move = chess.Move.from_uci("g5f6")
    assert line_sacrifice(board, move, ["d8f6", "g1f3", "b8c6"], 8) == 0
    assert leaves_piece_en_prise(board, move) == 0


def test_miss_after_opponent_mistake():
    # Opponent's last move dropped them from 50% to 25% (i.e. we went 50 -> 75),
    # and we gave it straight back to about 48%.
    prev = {"win_before": 50.0, "win_drop": 25.0}
    res = run(START, "a2a3", [line("e2e4", cp(400)), line("d2d4", cp(390))], cp(-10), prev=prev, prev_uci="e7e5")
    assert res["win_drop"] >= 10
    assert res["classification"] == C.MISS


def test_blunder_stays_blunder_when_worse_than_before():
    prev = {"win_before": 50.0, "win_drop": 25.0}
    res = run(START, "a2a3", [line("e2e4", cp(400)), line("d2d4", cp(390))], cp(-700), prev=prev, prev_uci="e7e5")
    assert res["classification"] == C.BLUNDER


# --- rules added after comparing with a chess.com review -------------------

def test_taking_a_more_valuable_piece_is_not_great():
    # Knight takes a queen that is defended by a pawn: obvious, so Best not Great.
    fen = "4k3/8/5p2/6q1/8/5N2/8/4K3 w - - 0 1"
    res = run(fen, "f3g5", [line("f3g5", cp(900)), line("e1e2", cp(-300))], cp(900))
    assert res["classification"] == C.BEST


def test_moving_away_from_a_cheaper_attacker_is_not_great():
    # Rook attacked by a knight steps away: the only good move, but an obvious one.
    fen = "4k3/8/8/8/8/8/5n2/3RK3 w - - 0 1"
    res = run(fen, "d1d2", [line("d1d2", cp(300)), line("e1e2", cp(-100))], cp(300))
    assert res["classification"] == C.BEST


def test_allowing_a_long_mate_from_a_playable_position_is_harsh():
    # Not yet hopeless (-5.00): the win% drop (a Mistake) beats the lenient mate table.
    assert C.point_loss_class(cp(-500), mate(-15), "white", 10.7, CFG) == C.MISTAKE


def test_making_a_long_mate_easy_is_a_mistake():
    assert C.point_loss_class(mate(-14), mate(-3), "white", 0, CFG) == C.MISTAKE
    assert C.point_loss_class(mate(-14), mate(-12), "white", 0, CFG) == C.EXCELLENT


def test_accuracy_decay_is_configurable():
    assert move_accuracy(10, 0.08) < move_accuracy(10, 0.0435)


def test_only_move_keeping_a_forced_mate_is_great():
    # Mate vs +7.00 is only ~7% apart in win%, below the 10% gap, but it is the only mate.
    res = run(START, "g1f3", [line("g1f3", mate(5)), line("b1c3", cp(700))], mate(4))
    assert res["classification"] == C.GREAT
    # If the alternative also mates, nothing special.
    res = run(START, "g1f3", [line("g1f3", mate(5)), line("b1c3", mate(6))], mate(4))
    assert res["classification"] == C.BEST


def test_only_move_winning_the_queen_is_great_even_when_already_winning():
    # 24.Ne7+ from DerTeXx vs nadael-pez with Stockfish 19's numbers: the fork wins the
    # queen (+12.92) while the best alternative Qh3 is +8.75. Only 3% apart in win%,
    # but more than 3 pawns apart.
    fen = "r4rk1/p5pp/1p1P1pq1/2p1RN2/8/2Q5/PPPR2P1/2K5 w - - 0 24"
    res = run(fen, "f5e7", [line("f5e7", cp(1292)), line("c3h3", cp(875))], cp(1618))
    assert res["classification"] == C.GREAT


def test_power_mean_aggregation():
    from dataclasses import replace
    from review.summary import aggregate_accuracy
    accs, weights = [100.0, 50.0, 0.0], [1.0, 1.0, 2.0]
    # Default: lichess blend (unchanged behaviour).
    assert aggregate_accuracy(accs, weights) == aggregate_accuracy(accs, weights, CFG)
    # p = 1 is a weighted mean; the floor lifts the 0 to 20; the offset shifts the result.
    cfg = replace(CFG, accuracy_power=1.0, accuracy_floor=20.0, accuracy_offset=-2.0)
    assert aggregate_accuracy(accs, weights, cfg) == round((100 + 50 + 2 * 20) / 4 - 2, 1)
    # Lower p punishes the bad moves more.
    harsh = replace(CFG, accuracy_power=-2.0, accuracy_floor=20.0)
    assert aggregate_accuracy(accs, weights, harsh) < aggregate_accuracy(accs, weights, replace(harsh, accuracy_power=1.0))
