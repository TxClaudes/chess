"""Flask server: serves the UI and runs analyses as background jobs.

Run:  python app.py            (then open http://127.0.0.1:5000)
"""

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import uuid

import chess
from flask import Flask, jsonify, request, send_from_directory

from config import Config
from review.analyze import analyze_pgn
from review.classify import BEST, EXCELLENT, point_loss_class
from review.engine import Analyzer, StockfishNotFound, find_stockfish
from review.openings import load_book
from review.pgn import PGNError
from review.winprob import pov, white_win_pct

MIN_DEPTH, MAX_DEPTH = 6, 30
RETRY_MAX_DEPTH = 18
JOB_TTL_SECONDS = 3600

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

# One analysis at a time: each already uses several engine processes.
_executor = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _prune_jobs():
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        for job_id in [k for k, j in _jobs.items() if j["created"] < cutoff]:
            del _jobs[job_id]


def _run_job(job_id: str, pgn_text: str, config: Config):
    job = _jobs[job_id]
    job["status"] = "running"

    def progress(done, total):
        job["done"], job["total"] = done, total

    try:
        job["result"] = analyze_pgn(pgn_text, config, progress)
        job["status"] = "done"
    except PGNError as exc:
        job["status"], job["error"] = "error", str(exc)
    except StockfishNotFound as exc:
        job["status"], job["error"] = "error", str(exc)
    except Exception as exc:  # engine crash etc.; report instead of hanging the UI
        app.logger.exception("analysis failed")
        job["status"], job["error"] = "error", f"Analysis failed: {exc}"


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    try:
        path = find_stockfish()
        return jsonify({"stockfish": True, "path": path, "default_depth": Config().engine.depth})
    except StockfishNotFound as exc:
        return jsonify({"stockfish": False, "error": str(exc), "default_depth": Config().engine.depth})


@app.post("/api/analyze")
def analyze():
    pgn_text = request.form.get("pgn", "")
    upload = request.files.get("file")
    if upload and upload.filename:
        pgn_text = upload.read().decode("utf-8", errors="replace")
    if not pgn_text.strip():
        return _error("Paste a PGN or choose a file.", 400)

    try:
        depth = int(request.form.get("depth") or Config().engine.depth)
    except ValueError:
        return _error("Depth must be a number.", 400)
    if not MIN_DEPTH <= depth <= MAX_DEPTH:
        return _error(f"Depth must be between {MIN_DEPTH} and {MAX_DEPTH}.", 400)

    try:
        find_stockfish()
    except StockfishNotFound as exc:
        return _error(str(exc), 503)

    config = Config()
    config.engine.depth = depth

    _prune_jobs()
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "done": 0, "total": 0, "created": time.time()}
    _executor.submit(_run_job, job_id, pgn_text, config)
    return jsonify({"job_id": job_id}), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        return _error("Unknown or expired job.", 404)
    body = {k: job.get(k) for k in ("status", "done", "total", "error")}
    if job["status"] == "done":
        body["result"] = job["result"]
    return jsonify(body)


@app.post("/api/evaluate-move")
def evaluate_move():
    """Grade a move tried in Retry mode against the original analysis of the position."""
    data = request.get_json(silent=True) or {}
    try:
        board = chess.Board(data["fen"])
        move = chess.Move.from_uci(data["uci"])
        eval_before = data["eval_before"]
        best_uci = data.get("best_uci")
        depth = max(MIN_DEPTH, min(RETRY_MAX_DEPTH, int(data.get("depth") or RETRY_MAX_DEPTH)))
    except (KeyError, ValueError, TypeError):
        return _error("Bad request.", 400)

    # Auto-queen pawn moves to the last rank.
    if move not in board.legal_moves:
        piece = board.piece_at(move.from_square)
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(move.to_square) in (0, 7):
            move = chess.Move(move.from_square, move.to_square, promotion=chess.QUEEN)
    if move not in board.legal_moves:
        return jsonify({"legal": False})

    san = board.san(move)
    color = "white" if board.turn == chess.WHITE else "black"
    after = board.copy()
    after.push(move)

    # A short-lived engine per request: a persistent one would keep python-chess's
    # non-daemon thread alive and stop the server from exiting cleanly.
    config = Config()
    config.engine.workers = 1
    config.engine.multipv = 1
    try:
        with Analyzer(config.engine) as analyzer:
            after_eval = analyzer.analyse(after.fen(), depth=depth).eval.to_dict()
    except StockfishNotFound as exc:
        return _error(str(exc), 503)

    k = Config().classify.win_k
    win_before = pov(white_win_pct(eval_before, board.turn == chess.WHITE, k), color)
    win_after = pov(white_win_pct(after_eval, after.turn == chess.WHITE, k), color)
    drop = max(0.0, win_before - win_after)

    if after.is_checkmate() or move.uci() == best_uci:
        classification = BEST
    else:
        classification = point_loss_class(eval_before, after_eval, color, drop, Config().classify)

    return jsonify({
        "legal": True,
        "san": san,
        "uci": move.uci(),
        "fen_after": after.fen(),
        "classification": classification,
        "win_drop": round(drop, 2),
        "correct": classification in (BEST, EXCELLENT),
    })


def main():
    try:
        print(f"Using Stockfish at {find_stockfish()}")
    except StockfishNotFound as exc:
        print(f"WARNING: {exc}\nThe UI will load, but analysis will fail until Stockfish is installed.")
    # Parse the opening book now so the first analysis doesn't pay for it.
    threading.Thread(target=load_book, daemon=True).start()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
