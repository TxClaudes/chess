"""Material counting and sacrifice detection for the Brilliant heuristic."""

import chess

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def material_balance(board: chess.Board, color: chess.Color) -> int:
    """Material of `color` minus material of the opponent, in pawn units."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += value * len(board.pieces(piece_type, color))
        total -= value * len(board.pieces(piece_type, not color))
    return total


def line_sacrifice(board: chess.Board, move: chess.Move, reply_pv: list[str], max_plies: int) -> int:
    """Material the mover is down after `move` and the engine's best continuation.

    The line is followed until it goes quiet (two plies in a row without a capture),
    ends in mate, runs out, or reaches `max_plies`. Returns how many pawn units the
    mover has given up compared with before the move (0 if none).
    """
    mover = board.turn
    start = material_balance(board, mover)
    work = board.copy(stack=False)
    work.push(move)
    quiet = 0 if board.is_capture(move) else 1

    for uci in reply_pv[:max_plies]:
        if quiet >= 2 or work.is_game_over():
            break
        reply = chess.Move.from_uci(uci)
        if reply not in work.legal_moves:
            break
        quiet = 0 if work.is_capture(reply) else quiet + 1
        work.push(reply)

    return max(0, start - material_balance(work, mover))


def _is_hanging(board: chess.Board, square: chess.Square) -> bool:
    """A piece is en prise if a cheaper piece attacks it, or it is attacked more than defended."""
    piece = board.piece_at(square)
    if piece is None:
        return False
    attackers = board.attackers(not piece.color, square)
    if not attackers:
        return False
    value = PIECE_VALUES[piece.piece_type]
    if any(PIECE_VALUES[board.piece_type_at(a)] < value for a in attackers):
        return True
    defenders = board.attackers(piece.color, square)
    return len(attackers) > len(defenders)


def hanging_pieces(board: chess.Board, color: chess.Color) -> set[chess.Square]:
    """Squares of `color`'s knights, bishops, rooks and queens that are en prise."""
    squares = set()
    for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        for square in board.pieces(piece_type, color):
            if _is_hanging(board, square):
                squares.add(square)
    return squares


def leaves_piece_en_prise(board: chess.Board, move: chess.Move) -> int:
    """Value of the most valuable piece the move newly leaves en prise (0 if none).

    Pieces that were already hanging before the move do not count, and a piece
    worth no more than what the move captured is treated as a trade, not a sacrifice.
    """
    mover = board.turn
    before = hanging_pieces(board, mover)
    captured = board.piece_at(move.to_square) if not board.is_en_passant(move) else chess.Piece(chess.PAWN, not mover)
    captured_value = PIECE_VALUES[captured.piece_type] if captured else 0

    after_board = board.copy(stack=False)
    after_board.push(move)
    after = hanging_pieces(after_board, mover)

    # Moving a hanging piece to safety is not a sacrifice.
    if len(after) < len(before):
        return 0

    best = 0
    for square in after:
        was_hanging = square in before or (square == move.to_square and move.from_square in before)
        if was_hanging:
            continue
        value = PIECE_VALUES[after_board.piece_type_at(square)]
        if value > captured_value:
            best = max(best, value)
    return best
