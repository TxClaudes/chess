"""Opening book from lichess's chess-openings dataset (CC0, data/openings/*.tsv).

A position counts as "book" if it occurs anywhere along a named opening line.
Positions are keyed by piece placement plus side to move, so move-order
transpositions are recognised.
"""

from functools import lru_cache
from pathlib import Path
import re

import chess

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "openings"
_MOVE_NUMBER = re.compile(r"^\d+\.+$")


def position_key(board: chess.Board) -> str:
    return f"{board.board_fen()} {'w' if board.turn else 'b'}"


@lru_cache(maxsize=1)
def load_book() -> tuple[frozenset[str], dict[str, tuple[str, str]]]:
    """Returns (all book positions, {final position of a named line: (eco, name)})."""
    positions: set[str] = set()
    names: dict[str, tuple[str, str]] = {}
    for path in sorted(DATA_DIR.glob("*.tsv")):
        with path.open(encoding="utf-8") as f:
            next(f)  # header: eco, name, pgn
            for row in f:
                eco, name, pgn = row.rstrip("\n").split("\t")
                board = chess.Board()
                for token in pgn.split():
                    if _MOVE_NUMBER.match(token):
                        continue
                    board.push_san(token)
                    positions.add(position_key(board))
                # Where several lines end in the same position keep the first (most general) name.
                names.setdefault(position_key(board), (eco, name))
    return frozenset(positions), names


def is_book(board: chess.Board) -> bool:
    return position_key(board) in load_book()[0]


def opening_name(board: chess.Board) -> tuple[str, str] | None:
    return load_book()[1].get(position_key(board))
