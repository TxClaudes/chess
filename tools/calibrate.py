"""Fit the accuracy curve to chess.com's own accuracy numbers.

Downloads your recent games that were reviewed on chess.com (the public API includes
chess.com's accuracy for those), analyses each one with this tool, and finds the
`accuracy_decay` value that makes our accuracy match chess.com's most closely.

Run it on your own computer (chess.com blocks requests from cloud servers):

    python tools/calibrate.py YourUsername --games 40 --depth 18

Analyses are cached in .calibration/, so re-running only analyses new games.
"""

import argparse
import json
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from review.analyze import analyze_pgn  # noqa: E402
from review.engine import Analyzer  # noqa: E402
from review.summary import game_accuracy  # noqa: E402
from review.winprob import move_accuracy  # noqa: E402

API = "https://api.chess.com/pub/player/{user}/games/archives"
HEADERS = {"User-Agent": "chess-review calibrate (https://github.com/TxClaudes/chess)"}
CACHE = Path(__file__).resolve().parent.parent / ".calibration"


def fetch_json(url: str) -> dict:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:  # rate limited: back off and retry
                time.sleep(2 ** attempt * 5)
                continue
            raise
    raise RuntimeError("unreachable")


def reviewed_games(user: str, limit: int) -> list[dict]:
    """Newest first: standard-chess games that have chess.com accuracies."""
    archives = fetch_json(API.format(user=user.lower()))["archives"]
    games = []
    for url in reversed(archives):
        for g in reversed(fetch_json(url)["games"]):
            if g.get("rules") == "chess" and g.get("accuracies") and g.get("pgn"):
                games.append(g)
                if len(games) >= limit:
                    return games
    return games


def analyse_cached(games: list[dict], config: Config) -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    results = []
    with Analyzer(config.engine) as analyzer:
        for i, g in enumerate(games, 1):
            game_id = g["url"].rstrip("/").split("/")[-1]
            path = CACHE / f"{game_id}-d{config.engine.depth}.json"
            if path.exists():
                results.append(json.loads(path.read_text()))
                continue
            print(f"  analysing {i}/{len(games)}: {g['url']}", file=sys.stderr)
            try:
                review = analyze_pgn(g["pgn"], config, analyzer=analyzer)
            except Exception as exc:  # skip games we cannot parse, keep going
                print(f"    skipped: {exc}", file=sys.stderr)
                continue
            entry = {
                "url": g["url"],
                "white_elo": g["white"].get("rating"),
                "black_elo": g["black"].get("rating"),
                "chesscom": g["accuracies"],
                "white_wins": [p["white_win"] for p in review["positions"]],
                "moves": [{"color": m["color"], "win_drop": m["win_drop"]} for m in review["moves"]],
            }
            path.write_text(json.dumps(entry))
            results.append(entry)
    return results


def our_accuracy(entry: dict, decay: float) -> dict:
    moves = [{**m, "accuracy": move_accuracy(m["win_drop"], decay)} for m in entry["moves"]]
    return game_accuracy(entry["white_wins"], moves)


def mean_abs_error(entries: list[dict], decay: float) -> float:
    errors = []
    for e in entries:
        ours = our_accuracy(e, decay)
        for color in ("white", "black"):
            if ours[color] is not None and e["chesscom"].get(color) is not None:
                errors.append(abs(ours[color] - e["chesscom"][color]))
    return sum(errors) / len(errors) if errors else math.inf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("--games", type=int, default=40, help="how many reviewed games to use (default 40)")
    parser.add_argument("--depth", type=int, default=None, help="engine depth (default: config.py)")
    args = parser.parse_args()

    config = Config()
    if args.depth:
        config.engine.depth = args.depth

    print(f"Fetching reviewed games for {args.username}...", file=sys.stderr)
    games = reviewed_games(args.username, args.games)
    if not games:
        print("No reviewed games found. Only games opened in Game Review have chess.com accuracies.")
        return 1
    entries = analyse_cached(games, config)

    current = config.classify.accuracy_decay
    grid = [round(0.03 + 0.0025 * i, 4) for i in range(69)]  # 0.03 .. 0.20
    best = min(grid, key=lambda d: mean_abs_error(entries, d))

    print(f"\n{len(entries)} games, depth {config.engine.depth}\n")
    print(f"{'game':<48} {'chess.com W/B':>14} {'current W/B':>14} {'fitted W/B':>14}")
    for e in entries:
        cur, fit, cc = our_accuracy(e, current), our_accuracy(e, best), e["chesscom"]
        fmt = lambda a: f"{a['white'] or 0:5.1f}/{a['black'] or 0:5.1f}"
        print(f"{e['url'][-48:]:<48} {fmt(cc):>14} {fmt(cur):>14} {fmt(fit):>14}")

    print(f"\nlichess curve (0.0435): average error {mean_abs_error(entries, 0.04354415386753951):.1f} points")
    print(f"current ({current}):       average error {mean_abs_error(entries, current):.1f} points")
    print(f"best fit ({best}):       average error {mean_abs_error(entries, best):.1f} points")
    print(f"\nTo use it, set  accuracy_decay: float = {best}  in config.py (ClassificationConfig).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
