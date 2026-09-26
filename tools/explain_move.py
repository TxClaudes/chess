"""Show why one move got its classification.

    python tools/explain_move.py game.pgn 24            # White's 24th move
    python tools/explain_move.py game.pgn 22 --black    # Black's 22nd move

Prints the engine lines before the move, the evals and win% around it, and every
check behind Great/Brilliant, so a label that differs from chess.com can be traced.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess  # noqa: E402

from config import Config  # noqa: E402
from review.analyze import _position_dict  # noqa: E402
from review.classify import MoveContext, classify_move  # noqa: E402
from review.engine import Analyzer, find_stockfish  # noqa: E402
from review.openings import is_book  # noqa: E402
from review.pgn import parse_pgn  # noqa: E402


def fmt_eval(ev: dict) -> str:
    if ev["type"] == "mate":
        return f"M{ev['value']}" if ev["value"] >= 0 else f"-M{-ev['value']}"
    return f"{ev['value'] / 100:+.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pgn", help="PGN file")
    parser.add_argument("move_number", type=int)
    parser.add_argument("--black", action="store_true", help="Black's move (default: White's)")
    parser.add_argument("--depth", type=int, default=None)
    args = parser.parse_args()

    config = Config()
    if args.depth:
        config.engine.depth = args.depth
    game = parse_pgn(Path(args.pgn).read_text(encoding="utf-8"))
    color = "black" if args.black else "white"
    ply = next((p for p in game.plies if p.move_number == args.move_number and p.color == color), None)
    if ply is None:
        print(f"No {color} move {args.move_number} in this game.")
        return 1

    # Book status follows the same rule as the full analysis: book only while every
    # move so far was book.
    in_book = game.start_fen == chess.STARTING_FEN
    for p in game.plies[: ply.index + 1]:
        in_book = in_book and is_book(chess.Board(p.fen_after))

    config.engine.workers = 3
    with Analyzer(config.engine) as analyzer:
        fens = [ply.fen_before, ply.fen_after]
        prev_ply = game.plies[ply.index - 1] if ply.index > 0 else None
        if prev_ply:
            fens.insert(0, prev_ply.fen_before)
        positions = [_position_dict(p) for p in analyzer.analyse_many(fens)]

    prev_result = None
    if prev_ply:
        prev_board = chess.Board(prev_ply.fen_before)
        prev_ctx = MoveContext(prev_board, chess.Move.from_uci(prev_ply.uci), positions[0], positions[1], None, None)
        prev_result = classify_move(prev_ctx, config.classify, False)
        positions = positions[1:]

    board = chess.Board(game.start_fen)
    for p in game.plies[: ply.index]:
        board.push(chess.Move.from_uci(p.uci))
    ctx = MoveContext(board, chess.Move.from_uci(ply.uci), positions[0], positions[1], prev_result,
                      prev_ply.uci if prev_ply else None)
    trace: dict = {}
    result = classify_move(ctx, config.classify, in_book, trace)

    dots = "." if color == "white" else "..."
    print(f"\n{ply.move_number}{dots} {ply.san}  ->  {result['classification'].upper()}")
    print(f"Stockfish: {find_stockfish()}   depth {config.engine.depth}\n")
    print("Engine lines before the move:")
    for line in positions[0]["lines"]:
        print(f"  {line['san']:<8} {fmt_eval(line['eval']):>7}   {' '.join(line['pv_san'][:6])}")
    print(f"\nEval before {fmt_eval(positions[0]['eval'])}, after {fmt_eval(positions[1]['eval'])}")
    print(f"Win% for {color}: {result['win_before']:.1f} -> {result['win_after']:.1f} (lost {result['win_drop']:.1f})")
    if in_book:
        print("Still in the opening book.")
    if prev_result:
        print(f"Opponent's previous move: {prev_result['classification']} (lost {prev_result['win_drop']:.1f}%)")

    print("\nGreat / Brilliant checks:")
    for key, value in trace.items():
        mark = "yes" if value is True else "NO " if value is False else "   "
        shown = "" if isinstance(value, bool) else f": {value}"
        print(f"  [{mark}] {key}{shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
