"""PGN parsing: turn PGN text into a flat list of plies along the main line."""

from dataclasses import dataclass
import io

import chess
import chess.pgn


class PGNError(ValueError):
    pass


@dataclass
class Ply:
    index: int            # 0-based ply number
    move_number: int      # fullmove number as printed in the PGN
    color: str            # "white" or "black" (the side that played the move)
    san: str
    uci: str
    fen_before: str
    fen_after: str


@dataclass
class ParsedGame:
    headers: dict[str, str]
    start_fen: str
    plies: list[Ply]


def parse_pgn(text: str) -> ParsedGame:
    """Parse the first game in `text`. Variations are ignored."""
    if not text or not text.strip():
        raise PGNError("The PGN is empty.")

    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        raise PGNError("No game found in the PGN.")
    if game.errors:
        raise PGNError(f"Could not parse the PGN: {game.errors[0]}")

    board = game.board()
    start_fen = board.fen()
    plies: list[Ply] = []

    for index, move in enumerate(game.mainline_moves()):
        fen_before = board.fen()
        color = "white" if board.turn == chess.WHITE else "black"
        move_number = board.fullmove_number
        san = board.san(move)
        board.push(move)
        plies.append(Ply(index, move_number, color, san, move.uci(), fen_before, board.fen()))

    if not plies:
        raise PGNError("The PGN contains no moves.")

    return ParsedGame(dict(game.headers), start_fen, plies)
