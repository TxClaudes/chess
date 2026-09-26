import pytest

from review.pgn import PGNError, parse_pgn

SCHOLARS_MATE = """[Event "Test"]
[White "A"]
[Black "B"]
[Result "1-0"]

1. e4 e5 2. Bc4 {a comment} Nc6 (2... Nf6) 3. Qh5 Nf6?? 4. Qxf7# 1-0
"""


def test_parses_mainline_and_skips_variations():
    game = parse_pgn(SCHOLARS_MATE)
    assert [p.san for p in game.plies] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    assert game.headers["White"] == "A"


def test_ply_metadata():
    game = parse_pgn(SCHOLARS_MATE)
    last = game.plies[-1]
    assert (last.index, last.move_number, last.color, last.uci) == (6, 4, "white", "h5f7")
    assert game.plies[1].fen_before == game.plies[0].fen_after


def test_custom_start_position():
    pgn = '[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"]\n\n1. e4 *'
    game = parse_pgn(pgn)
    assert game.start_fen.startswith("4k3/8/8/8/8/8/4P3/4K3 w")
    assert game.plies[0].san == "e4"


@pytest.mark.parametrize("text", ["", "   ", "[Event \"x\"]\n\n*"])
def test_rejects_empty(text):
    with pytest.raises(PGNError):
        parse_pgn(text)


def test_rejects_illegal_move():
    with pytest.raises(PGNError):
        parse_pgn("1. e4 e5 2. Ke3 *")
