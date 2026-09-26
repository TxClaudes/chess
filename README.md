# Game Review

A local chess game analyser modelled on chess.com's Game Review (without the coach
commentary). Paste or upload a PGN and it runs every position through Stockfish, labels
each move, and shows the game on a board with an eval bar, an eval graph, a move list,
accuracy, estimated game rating, phase grades and a Retry mode for your mistakes.

## Setup

1. **Python 3.10+** and the Python dependencies:

   ```
   pip install -r requirements.txt        # python-chess, Flask
   pip install -r requirements-dev.txt    # + pytest, for the tests
   ```

2. **Stockfish** (not bundled):

   | OS | Install |
   |---|---|
   | Ubuntu/Debian | `sudo apt install stockfish` (installs to `/usr/games`, which is checked automatically) |
   | macOS | `brew install stockfish` |
   | Windows | download from <https://stockfishchess.org/download/> |

   If Stockfish isn't on your `PATH` (common on Windows), set its location at the top of
   `config.py`. Use the full path to the program file, not the folder:

   ```python
   STOCKFISH_PATH = r"C:\Tools\stockfish\stockfish-windows-x86-64-avx2.exe"
   ```

   The app looks for Stockfish in this order: the `STOCKFISH_PATH` environment variable
   (handy for a one-off override), `STOCKFISH_PATH` in `config.py` (by default
   `Documents\Programme\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe`
   in your user folder, skipped if that file doesn't exist), `stockfish` on your `PATH`,
   then `/usr/games`, `/usr/local/bin` and `/opt/homebrew/bin`. If none is found you get an
   error explaining what to do. On startup the server prints which Stockfish it is using.

## Running

```
python app.py
```

Then open <http://127.0.0.1:5000>. Set `HOST`/`PORT` to change where it listens.

From the command line (prints the full JSON):

```
python -m review.analyze game.pgn --depth 18 > review.json
```

A typical 40-move game takes 30 to 60 seconds at depth 18 with the default settings.

## Using the UI

- **← / →** step through moves, **Home / End** jump, **F** flips the board.
- Click a move, or a point on the graph, to jump there.
- For any move that isn't the engine's choice, the card shows the best move and its line,
  and a green arrow on the board. **Show best move** plays it on the board.
- **Explore lines:** in any position, drag a piece for either side to try your own moves.
  Dots show where the picked-up piece can go. Each move gets the eval bar, the engine's top
  two lines, a best-move arrow and a label (Best, Mistake, ...). **Engine reply** plays
  Stockfish's answer, **Auto-play 10** lets it continue the line by itself. ← → step through
  your line, **Esc** or **Back to game** returns. Playing a different move part-way through
  replaces the rest of the line.
- **Retry** (on inaccuracies, mistakes, misses and blunders): drag a piece to try a better
  move. A move counts as correct if it is Best or Excellent.
- **Download JSON** saves the full analysis.

## How moves are classified

Evaluations are turned into a win chance using lichess's formula
`win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`, and moves are judged by how much
win chance they lose, from the mover's point of view. The bands follow chess.com's published
expected-points table:

| Class | Rule |
|---|---|
| Best | the engine's top move |
| Excellent | loses ≤ 2% |
| Good | loses ≤ 5% |
| Inaccuracy | loses ≤ 10% |
| Mistake | loses ≤ 20% |
| Blunder | loses more than 20% |
| Book | still in the opening book (lichess `chess-openings`, from move 1 without leaving it) |
| Forced | the only legal move |
| Miss | an Inaccuracy/Mistake/Blunder right after the opponent's mistake (≥ 10%) that hands back the advantage, ending roughly where things stood before that mistake. Ending up worse than that stays a Mistake/Blunder. |
| Great | best or near-best (loses ≤ 2%), and every other move is at least 10% worse, or at least 3 pawns worse (for example the only move that wins a piece), or it is the only move that keeps a forced mate. Not for recaptures, easy captures (an undefended piece, or a piece worth more than the capturer), moving a piece away from a cheaper attacker, moves out of check, or when the second-best move is already ≥ 97% winning. |
| Brilliant | best or near-best, gives up material (a piece left en prise, or ≥ 2 pawns down once the engine's line settles), the mover is not worse afterwards, and the position was not already won anyway (second-best < 97%). Pawn-only sacrifices don't count. |

Mates have their own rules. For example, allowing a mate in 1 or 2 is a blunder, unless the
position was already hopeless (worse than −6.00). A defender who turns a long forced mate into
a mate in 3 or fewer gets a Mistake, because long mates are often not found at club level.
Every threshold lives in `config.py` (`ClassificationConfig`).

**Accuracy** uses lichess's method with a steeper curve. Each move gets
`103.17·e^(-decay·win% lost) − 3.17` (plus lichess's +1 bonus). lichess uses decay 0.0435;
chess.com is harsher, and the default here is 0.055 (`accuracy_decay` in `config.py`),
fitted with `tools/calibrate.py`. The game
score is the average of a volatility-weighted mean and a harmonic mean of the per-move values.

**Game rating** is a rough single-game estimate, `3100·e^(-0.01·ACPL)`, pulled toward the
players' PGN Elo when present. One game is a small sample, so treat it as a curiosity.

**Phases:** the middlegame starts when ≤ 10 queens/rooks/minor pieces remain, a back rank
thins out, or move 15 is reached. The endgame starts at ≤ 6 of those pieces. Each phase is
graded by average move accuracy.

### How close is this to chess.com?

chess.com's exact algorithms are not public, so this is an approximation built from what
chess.com has published (the expected-points bands and the Brilliant/Great/Miss definitions)
and from open-source reimplementations. Expect differences, mainly:

- chess.com adjusts its win-chance curve to the players' ratings and is more generous with
  Brilliant/Great for lower-rated players. This tool uses one curve for everyone.
- Brilliant and Great depend on small eval differences, so they can change with engine
  depth or version. That is also true on chess.com.
- chess.com's accuracy (CAPS2) is proprietary. Plain lichess-style accuracy read clearly
  higher than chess.com's in our comparison, which is why the curve is steeper here.
- chess.com's opening book is larger than lichess's list, so a few rarer book moves show up
  as normal moves here.
- Results also depend on your Stockfish version and depth. Depth 18 matched chess.com better
  than depth 22 in our comparison.

### Why did a move get this label?

```
python tools/explain_move.py game.pgn 24            # White's 24th move
python tools/explain_move.py game.pgn 22 --black    # Black's 22nd move
```

This prints the engine's lines, the win% before and after, and every Great/Brilliant check
with yes/no, which makes it easy to see which rule or number made the difference.

### Tuning accuracy to your own games

chess.com's public API reports its accuracy for every game you've opened in Game Review.
`tools/calibrate.py` downloads those games, analyses them, and finds the `accuracy_decay`
that matches chess.com best:

```
python tools/calibrate.py YourUsername --games 40 --depth 18
```

It prints a per-game comparison and the value to put in `config.py`. Analyses are cached in
`.calibration/`, so re-running is quick. Run it on your own computer, since chess.com blocks
requests from cloud servers.

## Output format

`analyze_pgn()` returns:

- `headers`: the PGN tags
- `positions[]`: for the start position and after every move: `fen`, `eval`
  (`{"type": "cp"|"mate", "value"}` from White's side; mate 0 = side to move is mated),
  `white_win`, and the top engine `lines` (`uci`, `san`, `eval`, `pv`, `pv_san`)
- `moves[]`: `ply`, `move_number`, `color`, `san`, `uci`, `fen_before`, `fen_after`,
  `eval_before`, `eval_after`, `win_before`, `win_after`, `win_drop`, `accuracy`,
  `classification`, `best_move` (`uci`, `san`, `line`), `phase`, `sacrifice`, and `opening`
  where a named opening is reached
- `summary`: `accuracy`, `estimated_rating`, `phases`, `counts` per colour, `opening`

## Engine settings

In `config.py` (`EngineConfig`): `depth` (default 18), or `nodes` for a fixed node count per
position (this is what chess.com does), `workers` (parallel single-threaded Stockfish
processes; default is CPU count − 1, up to 4) and `hash_mb`. The hash is cleared before each
position, so the same PGN always gives the same result.

## Tests

```
pytest
```

Engine-dependent tests are skipped if Stockfish isn't installed.

## Project layout

```
app.py                 Flask server (UI, analysis jobs with progress, Retry endpoint)
config.py              engine settings and every classification threshold
review/pgn.py          PGN parsing
review/engine.py       Stockfish discovery and the engine pool
review/winprob.py      eval -> win%, per-move accuracy
review/material.py     material counting, sacrifice detection
review/openings.py     opening book
review/classify.py     move classification
review/summary.py      accuracy, game rating, phases, counts
review/analyze.py      the pipeline and the command-line entry point
tools/calibrate.py     fit the accuracy curve to your chess.com-reviewed games
tools/explain_move.py  show why a move got its label (engine lines, win%, every check)
static/                the web UI (index.html, app.js, style.css)
static/vendor/         jQuery 3.7.1, chessboard.js 1.0.0 (+ piece images), Chart.js 4.4.1
data/openings/         lichess chess-openings TSVs
samples/               example PGNs
tests/
```

## Credits and licences

- Opening names and book positions: [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings), CC0.
- Win% and accuracy formulas: lichess.
- Classification ideas drawn from [Chesskit](https://github.com/GuillaumeSD/Chesskit) and
  [WintrChess](https://github.com/WintrCat/wintrchess). The ideas are reimplemented here, and no
  code was copied (those projects are AGPL/GPL).
- chessboard.js (MIT) with the Wikipedia piece set (CC BY-SA, by Cburnett), jQuery (MIT),
  Chart.js (MIT), python-chess (GPL-3.0), Flask (BSD).
