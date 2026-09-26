"""Pipeline: PGN text -> engine analysis of every position -> per-move JSON."""

import argparse
import json
import sys
from typing import Callable

import chess

from config import Config
from review.classify import classify_game
from review.engine import Analyzer, PositionAnalysis
from review.pgn import parse_pgn
from review.summary import summarize
from review.winprob import white_win_pct

PV_SAN_PLIES = 10


def _pv_san(fen: str, pv: list[str]) -> list[str]:
    board = chess.Board(fen)
    out = []
    for uci in pv[:PV_SAN_PLIES]:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        out.append(board.san(move))
        board.push(move)
    return out


def _position_dict(pa: PositionAnalysis) -> dict:
    return {
        "fen": pa.fen,
        "eval": pa.eval.to_dict(),
        "lines": [
            {"uci": l.uci, "san": l.san, "eval": l.eval.to_dict(), "pv": l.pv, "pv_san": _pv_san(pa.fen, l.pv)}
            for l in pa.lines
        ],
    }


def analyze_pgn(
    pgn_text: str,
    config: Config | None = None,
    progress: Callable[[int, int], None] | None = None,
    analyzer: Analyzer | None = None,
) -> dict:
    config = config or Config()
    game = parse_pgn(pgn_text)

    # One search per position: the start position plus the position after each move.
    fens = [game.start_fen] + [p.fen_after for p in game.plies]

    if analyzer is None:
        with Analyzer(config.engine) as own:
            positions = own.analyse_many(fens, progress)
    else:
        positions = analyzer.analyse_many(fens, progress)

    position_dicts = [_position_dict(p) for p in positions]
    white_wins = [
        round(white_win_pct(p["eval"], chess.Board(p["fen"]).turn == chess.WHITE, config.classify.win_k), 2)
        for p in position_dicts
    ]
    for p, w in zip(position_dicts, white_wins):
        p["white_win"] = w

    standard_start = game.start_fen == chess.STARTING_FEN
    classified = classify_game(game.plies, position_dicts, config.classify, standard_start)

    moves = []
    for ply, cls in zip(game.plies, classified):
        before = position_dicts[ply.index]
        after = position_dicts[ply.index + 1]
        best = before["lines"][0] if before["lines"] else None
        moves.append({
            "ply": ply.index,
            "move_number": ply.move_number,
            "color": ply.color,
            "san": ply.san,
            "uci": ply.uci,
            "fen_before": ply.fen_before,
            "fen_after": ply.fen_after,
            "eval_before": before["eval"],
            "eval_after": after["eval"],
            "best_move": {"uci": best["uci"], "san": best["san"], "line": best["pv_san"]} if best else None,
            **cls,
        })

    return {
        "headers": game.headers,
        "settings": {"depth": config.engine.depth, "nodes": config.engine.nodes},
        "positions": position_dicts,
        "moves": moves,
        "summary": summarize(moves, white_wins, game.headers, config.classify),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse a PGN with Stockfish and print JSON.")
    parser.add_argument("pgn", help="PGN file path, or - for stdin")
    parser.add_argument("--depth", type=int, default=None)
    parser.add_argument("--nodes", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.pgn == "-" else open(args.pgn, encoding="utf-8").read()
    config = Config()
    if args.depth is not None:
        config.engine.depth = args.depth
    if args.nodes is not None:
        config.engine.nodes = args.nodes
    if args.workers is not None:
        config.engine.workers = args.workers

    def progress(done, total):
        print(f"\ranalysed {done}/{total} positions", end="", file=sys.stderr, flush=True)

    result = analyze_pgn(text, config, progress)
    print(file=sys.stderr)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
