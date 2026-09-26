"""Fit the accuracy curve to chess.com's own accuracy numbers.

Downloads your recent games that were reviewed on chess.com (the public API includes
chess.com's accuracy for those), analyses each one with this tool, and finds the
`accuracy_decay` value that makes our accuracy match chess.com's most closely.

Run it on your own computer (chess.com blocks requests from cloud servers):

    python tools/calibrate.py YourUsername --games 40 --depth 18

Analyses are cached in .calibration/, so re-running only analyses new games.
"""

import argparse
from dataclasses import replace
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
from review.summary import aggregate_accuracy, volatility_weights  # noqa: E402
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
                "win_k": config.classify.win_k,
                "white_wins": [p["white_win"] for p in review["positions"]],
                "moves": [{"color": m["color"], "win_drop": m["win_drop"]} for m in review["moves"]],
            }
            path.write_text(json.dumps(entry))
            results.append(entry)
    return results


# ------------------------------------------------------------------ fitting
#
# Parameters fitted (all in ClassificationConfig): accuracy_decay, accuracy_win_k,
# accuracy_power (None = lichess blend), accuracy_floor, accuracy_offset. Everything is
# recomputed from the cached win% values, so changing these never needs re-analysis.

GRIDS = {
    "accuracy_decay": [round(0.02 + 0.0025 * i, 4) for i in range(53)],             # 0.02 .. 0.15
    "accuracy_win_k": [None] + [round(0.0018 + 0.0001 * i, 4) for i in range(29)],  # .. 0.0046
    "accuracy_power": [None] + [round(-4 + 0.25 * i, 2) for i in range(23)],        # -4 .. 1.5
    "accuracy_floor": [2.5 * i for i in range(25)],                                   # 0 .. 60
    "accuracy_offset": [round(-12 + 0.5 * i, 1) for i in range(49)],                  # -12 .. 12
}


class Game:
    """A cached game prepared for fast re-scoring under different parameters."""

    def __init__(self, entry: dict):
        self.entry = entry
        self.k0 = entry.get("win_k", 0.00368208)
        self.wins = entry["white_wins"]
        self.colors = [m["color"] for m in entry["moves"]]
        self.weights = volatility_weights(self.wins, len(self.colors))
        self.chesscom = entry["chesscom"]
        self._drops: dict = {}

    def drops(self, k: float | None) -> list[float]:
        """Per-move win% lost, on a win% curve with slope k (None = as analysed)."""
        key = k or self.k0
        if key not in self._drops:
            ratio = key / self.k0
            wins = [w if w <= 0 or w >= 100 else 100 / (1 + math.exp(-ratio * math.log(w / (100 - w))))
                    for w in self.wins]
            self._drops[key] = [
                max(0.0, (wins[i] - wins[i + 1]) if c == "white" else (wins[i + 1] - wins[i]))
                for i, c in enumerate(self.colors)
            ]
        return self._drops[key]

    def accuracy(self, params: dict) -> dict:
        cfg = replace(Config().classify, **params)
        accs = [move_accuracy(d, cfg.accuracy_decay) for d in self.drops(cfg.accuracy_win_k)]
        out = {}
        for color in ("white", "black"):
            idx = [i for i, c in enumerate(self.colors) if c == color]
            out[color] = aggregate_accuracy([accs[i] for i in idx], [self.weights[i] for i in idx], cfg) if idx else None
        return out


def errors(games: list[Game], params: dict) -> list[float]:
    """Signed errors (ours minus chess.com) over every player in every game."""
    out = []
    for g in games:
        ours = g.accuracy(params)
        for color in ("white", "black"):
            if ours[color] is not None and g.chesscom.get(color) is not None:
                out.append(ours[color] - g.chesscom[color])
    return out


def mae(games: list[Game], params: dict) -> float:
    e = errors(games, params)
    return sum(abs(x) for x in e) / len(e) if e else math.inf


def fit(games: list[Game], start: dict, names: list[str], rounds: int = 6) -> dict:
    """Coordinate descent: improve one parameter at a time over its grid until stable."""
    best = dict(start)
    best_err = mae(games, best)
    for _ in range(rounds):
        improved = False
        for name in names:
            for value in GRIDS[name]:
                trial = {**best, name: value}
                err = mae(games, trial)
                if err < best_err - 1e-9:
                    best, best_err, improved = trial, err, True
        if not improved:
            break
    return best


def fit_multi(games: list[Game], start: dict, names: list[str]) -> dict:
    """Run the descent from several starting points (the parameters trade off against
    each other, so a single start can get stuck) and keep the best result."""
    starts = [start] + [
        {**start, "accuracy_power": p, "accuracy_floor": f}
        for p in (None, 1.0, -0.5, -1.0, -2.0, -3.0) for f in (0.0, 20.0, 40.0)
        if not (p is None and f)  # the floor does nothing for the lichess blend
    ]
    return min((fit(games, s, names) for s in starts), key=lambda p: mae(games, p))


def config_lines(params: dict) -> str:
    return "\n".join(f"    {k}: {'float | None' if k in ('accuracy_win_k', 'accuracy_power') else 'float'} = {v}"
                      for k, v in params.items())


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
    games_raw = reviewed_games(args.username, args.games)
    if not games_raw:
        print("No reviewed games found. Only games opened in Game Review have chess.com accuracies.")
        return 1
    games = [Game(e) for e in analyse_cached(games_raw, config)]

    # Hold out every 4th game: parameters are fitted on the rest and judged on these.
    test = games[3::4]
    train = [g for i, g in enumerate(games) if i % 4 != 3]
    if len(test) < 5:
        print("Warning: fewer than 20 games, so the hold-out check is weak. Use --games 100.\n")

    cls = config.classify
    current = {k: getattr(cls, k) for k in GRIDS}
    lichess = {**current, "accuracy_decay": 0.04354415386753951, "accuracy_win_k": None,
               "accuracy_power": None, "accuracy_floor": 0.0, "accuracy_offset": 0.0}
    all_names = list(GRIDS)
    no_k = [n for n in all_names if n != "accuracy_win_k"]
    print("Fitting (this takes a minute or two)...", file=sys.stderr)
    candidates = {
        "curve + averaging": fit_multi(train, current, no_k),
        "curve + averaging + flatter win%": fit_multi(train, current, all_names),
    }

    print(f"\n{len(games)} games ({len(train)} to fit, {len(test)} held out), depth {config.engine.depth}\n")
    rows = [("lichess method", lichess), ("current config.py", current)] + list(candidates.items())
    print(f"{'':<34} {'fit games':>10} {'held-out':>10} {'bias':>7}")
    for name, params in rows:
        e = errors(games, params)
        bias = sum(e) / len(e) if e else 0
        print(f"{name:<34} {mae(train, params):>9.1f}  {mae(test, params):>9.1f}  {bias:>+6.1f}")
    print("(average error in accuracy points; bias > 0 means ours reads higher than chess.com)")

    best_name, best = min(candidates.items(), key=lambda kv: mae(test, kv[1]))
    print(f"\nPer game, current vs {best_name}:")
    print(f"{'game':<30} {'chess.com W/B':>14} {'current W/B':>14} {'fitted W/B':>14}")
    fmt = lambda a: f"{a['white'] or 0:5.1f}/{a['black'] or 0:5.1f}"
    for g in games:
        tag = " (held out)" if g in test else ""
        print(f"{g.entry['url'][-30:]:<30} {fmt(g.chesscom):>14} {fmt(g.accuracy(current)):>14} {fmt(g.accuracy(best)):>14}{tag}")

    if mae(test, best) < mae(test, current) - 0.3:
        print(f"\nRecommended ({best_name}). In config.py, ClassificationConfig, set:\n")
        print(config_lines(best))
    else:
        print("\nThe fitted settings don't beat the current ones on the held-out games; keep config.py as it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
