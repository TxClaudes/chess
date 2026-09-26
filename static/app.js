/* Game Review UI: board, eval bar, graph, move list, summary and Retry mode. */
(function () {
  "use strict";

  const CLASS_INFO = {
    brilliant:  { label: "Brilliant",  glyph: "!!" },
    great:      { label: "Great",      glyph: "!" },
    best:       { label: "Best",       glyph: "★" },
    excellent:  { label: "Excellent",  glyph: "✓" },
    good:       { label: "Good",       glyph: "✓" },
    book:       { label: "Book",       glyph: null },
    forced:     { label: "Forced",     glyph: "→" },
    inaccuracy: { label: "Inaccuracy", glyph: "?!" },
    mistake:    { label: "Mistake",    glyph: "?" },
    miss:       { label: "Miss",       glyph: "✕" },
    blunder:    { label: "Blunder",    glyph: "??" },
  };
  const MOVE_PHRASES = {
    brilliant: "is brilliant", great: "is a great move", best: "is best", excellent: "is excellent",
    good: "is good", book: "is a book move", forced: "is forced", inaccuracy: "is an inaccuracy",
    mistake: "is a mistake", miss: "is a miss", blunder: "is a blunder",
  };
  const CLASS_ORDER = ["brilliant", "great", "best", "excellent", "good", "book", "forced",
                       "inaccuracy", "mistake", "miss", "blunder"];
  const BOOK_SVG = '<svg viewBox="0 0 16 16"><path d="M2 3.2C3.6 2.4 5.6 2.3 7.4 3.3v10.2C5.6 12.6 3.6 12.7 2 13.4zM14 3.2c-1.6-.8-3.6-.9-5.4.1v10.2c1.8-.9 3.8-.8 5.4-.1z"/></svg>';
  // Moves that get a graph marker and a "retry" option.
  const NOTABLE = new Set(["brilliant", "great", "inaccuracy", "mistake", "miss", "blunder"]);
  const RETRYABLE = new Set(["inaccuracy", "mistake", "miss", "blunder"]);

  const EXAMPLE_PGN = `[Event "Paris"]
[Site "Paris FRA"]
[Date "1858.??.??"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0`;

  const $id = (id) => document.getElementById(id);
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const classColor = (cls) => cssVar(`--c-${cls}`);

  const state = {
    data: null,
    ply: 0,              // position index: 0 = start, i = after move i
    orientation: "white",
    preview: null,       // {fen, uci} when showing the engine's best move instead of the game move
    retry: null,         // {moveIndex, fen, status, message}
  };
  let board = null;
  let chart = null;

  // ---------------------------------------------------------------- helpers

  function badge(cls, size) {
    const info = CLASS_INFO[cls];
    const inner = info.glyph === null ? BOOK_SVG : info.glyph;
    return `<span class="badge bg-${cls}${size === "lg" ? " lg" : ""}" title="${info.label}">${inner}</span>`;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function turnOf(fen) { return fen.split(" ")[1] === "w" ? "white" : "black"; }

  function formatEval(ev, fen) {
    if (ev.type === "mate") {
      if (ev.value === 0) return turnOf(fen) === "white" ? "0-1" : "1-0";
      return (ev.value > 0 ? "M" : "-M") + Math.abs(ev.value);
    }
    const v = ev.value / 100;
    return (v > 0 ? "+" : "") + v.toFixed(2);
  }

  function moveLabel(m) {
    return `${m.move_number}${m.color === "white" ? "." : "..."} ${m.san}`;
  }

  // ---------------------------------------------------------------- input view

  function showInput() {
    $id("input-view").hidden = false;
    $id("review-view").hidden = true;
    $id("new-analysis").hidden = true;
    $id("download-json").hidden = true;
  }

  function showReview() {
    $id("input-view").hidden = true;
    $id("review-view").hidden = false;
    $id("new-analysis").hidden = false;
    $id("download-json").hidden = false;
  }

  function setError(msg) {
    const el = $id("input-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function setBusy(busy) {
    $id("analyze-btn").disabled = busy;
    $id("progress").hidden = !busy;
    if (!busy) $id("progress-bar").style.width = "0";
  }

  async function startAnalysis(ev) {
    ev.preventDefault();
    setError("");
    const form = new FormData();
    form.append("pgn", $id("pgn-text").value);
    const file = $id("pgn-file").files[0];
    if (file) form.append("file", file);
    form.append("depth", $id("depth").value);

    setBusy(true);
    $id("progress-text").textContent = "Starting…";
    try {
      const res = await fetch("/api/analyze", { method: "POST", body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `Server error ${res.status}`);
      const result = await pollJob(body.job_id);
      loadResult(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function pollJob(jobId) {
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      const res = await fetch(`/api/jobs/${jobId}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `Server error ${res.status}`);
      if (body.status === "error") throw new Error(body.error);
      if (body.status === "done") return body.result;
      const pct = body.total ? Math.round((100 * body.done) / body.total) : 0;
      $id("progress-bar").style.width = pct + "%";
      $id("progress-text").textContent = body.total
        ? `Analysing position ${body.done} of ${body.total}`
        : "Waiting for the engine…";
    }
  }

  // ---------------------------------------------------------------- review view

  function loadResult(data) {
    state.data = data;
    state.ply = 0;
    state.preview = null;
    state.retry = null;
    showReview();
    if (!board) initBoard();
    renderPlayers();
    renderSummary();
    renderMoveList();
    renderChart();
    goTo(0);
    window.scrollTo(0, 0);
  }

  function initBoard() {
    board = Chessboard("board", {
      position: "start",
      draggable: true,
      pieceTheme: "/static/vendor/img/chesspieces/wikipedia/{piece}.png",
      onDragStart: onDragStart,
      onDrop: onDrop,
    });
    window.addEventListener("resize", () => { board.resize(); drawOverlay(); sizeEvalBar(); });
  }

  function sizeEvalBar() {
    const h = $id("board").getBoundingClientRect().height;
    $id("eval-bar").style.height = h + "px";
  }

  function renderPlayers() {
    const h = state.data.headers;
    const strip = (color) => {
      const name = escapeHtml(h[color === "white" ? "White" : "Black"] || (color === "white" ? "White" : "Black"));
      const elo = h[color === "white" ? "WhiteElo" : "BlackElo"];
      return `<span class="badge" style="background:${color === "white" ? "#f1f1f1" : "#1b1a18"}"></span>${name}` +
        (elo && elo !== "?" ? ` <span class="elo">(${escapeHtml(elo)})</span>` : "");
    };
    const top = state.orientation === "white" ? "black" : "white";
    $id("player-top").innerHTML = strip(top);
    $id("player-bottom").innerHTML = strip(top === "white" ? "black" : "white");
  }

  function renderSummary() {
    const s = state.data.summary;
    const acc = (v) => (v === null || v === undefined ? "–" : v.toFixed(1));
    const rating = (v) => (v === null || v === undefined ? "–" : v);
    const h = state.data.headers;
    let html = `<div class="summary-grid">
      <div class="l head">${escapeHtml(h.White || "White")}</div><div></div><div class="r head">${escapeHtml(h.Black || "Black")}</div>
      <div class="big white-acc">${acc(s.accuracy.white)}</div>
      <div class="mid">Accuracy</div>
      <div class="big black-acc">${acc(s.accuracy.black)}</div>
      <div class="sep"></div>`;

    for (const cls of CLASS_ORDER) {
      const w = s.counts.white[cls], b = s.counts.black[cls];
      html += `<div class="l count ${w ? "" : "zero"} t-${cls}">${w}</div>
        <div class="mid">${badge(cls)}${CLASS_INFO[cls].label}</div>
        <div class="r count ${b ? "" : "zero"} t-${cls}">${b}</div>`;
    }

    html += `<div class="sep"></div>
      <div class="l count">${rating(s.estimated_rating.white)}</div>
      <div class="mid"><span class="hint" title="A rough estimate of the rating you played at in this one game, from average centipawn loss (and the players' ratings, if the PGN has them). Single games are noisy.">Game rating</span></div>
      <div class="r count">${rating(s.estimated_rating.black)}</div>`;

    for (const phase of ["opening", "middlegame", "endgame"]) {
      const cell = (g, side) => g
        ? `<div class="phase-cell ${side}" title="${g.accuracy.toFixed(1)}% accuracy">${badge(g.grade)}</div>`
        : `<div class="phase-cell ${side}"><span class="count zero">–</span></div>`;
      html += cell(s.phases[phase].white, "l") +
        `<div class="mid">${phase[0].toUpperCase() + phase.slice(1)}</div>` +
        cell(s.phases[phase].black, "r");
    }
    html += "</div>";
    $id("summary").innerHTML = html;
    $id("opening-name").textContent = s.opening ? `${s.opening.eco} · ${s.opening.name}` : "";
  }

  function renderMoveList() {
    const moves = state.data.moves;
    const list = $id("move-list");
    let html = "";
    let i = 0;
    // A game from a custom position can start with Black to move.
    while (i < moves.length) {
      const num = moves[i].move_number;
      const w = moves[i].color === "white" ? moves[i] : null;
      const b = w ? (moves[i + 1] && moves[i + 1].color === "black" ? moves[i + 1] : null) : moves[i];
      const cell = (m) => m
        ? `<button class="mv${NOTABLE.has(m.classification) ? " t-" + m.classification : ""}" data-ply="${m.ply + 1}">${badge(m.classification)}${escapeHtml(m.san)}</button>`
        : `<span></span>`;
      html += `<li><span class="num">${num}.</span>${cell(w)}${cell(b)}</li>`;
      i += (w ? 1 : 0) + (b ? 1 : 0);
    }
    list.innerHTML = html;
  }

  function renderChart() {
    const data = state.data;
    const values = data.positions.map((p) => p.white_win);
    const labels = data.positions.map((_, i) => i);
    const markerColor = (ctx) => {
      const m = data.moves[ctx.dataIndex - 1];
      return m && NOTABLE.has(m.classification) ? classColor(m.classification) : "transparent";
    };
    const markerRadius = (ctx) => {
      const m = data.moves[ctx.dataIndex - 1];
      return m && NOTABLE.has(m.classification) ? 4.5 : 0;
    };

    const currentLine = {
      id: "currentLine",
      afterDatasetsDraw(c) {
        const x = c.scales.x.getPixelForValue(state.ply);
        const { top, bottom } = c.chartArea;
        const g = c.ctx;
        g.save();
        g.strokeStyle = cssVar("--accent");
        g.lineWidth = 2;
        g.beginPath(); g.moveTo(x, top); g.lineTo(x, bottom); g.stroke();
        g.restore();
      },
    };
    const midLine = {
      id: "midLine",
      beforeDatasetsDraw(c) {
        const y = c.scales.y.getPixelForValue(50);
        const g = c.ctx;
        g.save();
        g.strokeStyle = "rgba(128,128,128,.6)";
        g.setLineDash([4, 4]);
        g.beginPath(); g.moveTo(c.chartArea.left, y); g.lineTo(c.chartArea.right, y); g.stroke();
        g.restore();
      },
    };

    if (chart) chart.destroy();
    chart = new Chart($id("eval-chart"), {
      type: "line",
      data: {
        labels,
        datasets: [{
          data: values,
          borderColor: "#d9d9d9",
          borderWidth: 2,
          backgroundColor: "#f1f1f1",
          fill: "origin",
          tension: 0.15,
          pointRadius: markerRadius,
          pointHoverRadius: 6,
          pointBackgroundColor: markerColor,
          pointBorderColor: cssVar("--panel"),
          pointBorderWidth: 2,
          pointHitRadius: 8,
        }],
      },
      options: {
        maintainAspectRatio: false,
        animation: false,
        layout: { padding: 2 },
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { display: false },
          y: { display: false, min: 0, max: 100 },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title(items) {
                const i = items[0].dataIndex;
                return i === 0 ? "Start" : moveLabel(data.moves[i - 1]);
              },
              label(item) {
                const i = item.dataIndex;
                const p = data.positions[i];
                const parts = [`${formatEval(p.eval, p.fen)}  ·  White ${p.white_win.toFixed(0)}%`];
                if (i > 0) parts.push(CLASS_INFO[data.moves[i - 1].classification].label);
                return parts;
              },
            },
          },
        },
        onClick(evt, _els, c) {
          const pts = c.getElementsAtEventForMode(evt, "index", { intersect: false }, false);
          if (pts.length) goTo(pts[0].index);
        },
        onHover(evt, els) { evt.native.target.style.cursor = els.length ? "pointer" : "default"; },
      },
      plugins: [midLine, currentLine],
    });
  }

  // ---------------------------------------------------------------- navigation

  function goTo(ply) {
    const max = state.data.moves.length;
    state.ply = Math.max(0, Math.min(max, ply));
    state.preview = null;
    state.retry = null;
    render();
  }

  function render() {
    const data = state.data;
    const pos = data.positions[state.ply];
    let fen = pos.fen;
    if (state.retry) fen = state.retry.fen;
    else if (state.preview) fen = state.preview.fen;
    board.position(fen, false);
    sizeEvalBar();

    // When previewing the best move or retrying, show the eval of the position before the game move.
    const evalPos = state.preview || state.retry ? data.positions[state.ply - 1] : pos;
    updateEvalBar(evalPos);

    document.querySelectorAll("#move-list .mv").forEach((el) => {
      el.classList.toggle("current", Number(el.dataset.ply) === state.ply);
    });
    scrollMoveIntoView(document.querySelector("#move-list .mv.current"));

    renderMoveCard();
    drawOverlay();
    if (chart) chart.draw();
  }

  // Scroll only the move list (scrollIntoView would also scroll the page).
  function scrollMoveIntoView(el) {
    const list = $id("move-list");
    if (!el) { list.scrollTop = 0; return; }
    const row = el.parentElement;
    if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
    }
  }

  function updateEvalBar(pos) {
    const w = pos.white_win;
    const fill = $id("eval-fill");
    fill.style.height = w + "%";
    const bar = $id("eval-bar");
    bar.classList.toggle("flipped", state.orientation === "black");
    // Black at the top when White is at the bottom; the fill grows from White's side.
    if (state.orientation === "black") {
      fill.style.top = "0"; fill.style.bottom = "auto";
    } else {
      fill.style.bottom = "0"; fill.style.top = "auto";
    }
    const text = $id("eval-text");
    text.textContent = formatEval(pos.eval, pos.fen).replace(/^\+/, "");
    const whiteAhead = w >= 50;
    const atBottom = whiteAhead === (state.orientation === "white");
    text.className = "eval-text " + (atBottom ? "bottom" : "top");
    text.style.color = whiteAhead ? "#403d39" : "#f1f1f1";
  }

  function renderMoveCard() {
    const card = $id("move-card");
    const data = state.data;
    if (state.retry) { renderRetryCard(card); return; }
    if (state.ply === 0) {
      card.innerHTML = `<div class="move-head">Starting position<span class="eval">${formatEval(data.positions[0].eval, data.positions[0].fen)}</span></div>
        <div class="best-line">Use ← → or click a move.</div>`;
      return;
    }
    const m = data.moves[state.ply - 1];
    const info = CLASS_INFO[m.classification];
    const pos = data.positions[state.ply];
    const phrase = MOVE_PHRASES[m.classification];
    let html = `<div class="move-head">${badge(m.classification, "lg")}<span>${escapeHtml(m.san)} ${phrase}</span>
      <span class="eval">${formatEval(pos.eval, pos.fen)}</span></div>`;

    const best = m.best_move;
    if (best && best.uci !== m.uci && !["book", "forced"].includes(m.classification)) {
      html += `<div class="best-line">Best was <b>${escapeHtml(best.san)}</b>
        <div class="pv">${escapeHtml(best.line.slice(0, 8).join(" "))}</div></div>`;
      html += `<div class="move-actions">
        <button class="btn" id="show-best">${state.preview ? "Show game move" : "Show best move"}</button>
        ${RETRYABLE.has(m.classification) ? '<button class="btn" id="retry-btn">Retry</button>' : ""}
      </div>`;
    }
    card.innerHTML = html;
    const sb = $id("show-best");
    if (sb) sb.onclick = toggleBest;
    const rb = $id("retry-btn");
    if (rb) rb.onclick = startRetry;
  }

  function toggleBest() {
    const m = state.data.moves[state.ply - 1];
    if (state.preview) {
      state.preview = null;
    } else {
      state.preview = { fen: fenAfterUci(m.fen_before, m.best_move), uci: m.best_move.uci };
    }
    render();
  }

  // chessboard.js has no move logic, so apply the best move to the piece placement
  // ourselves (castling, en passant and promotion included). Only the placement and
  // side to move are needed for display.
  function fenAfterUci(fen, best) {
    const [placement, turn] = fen.split(" ");
    const obj = placementToObj(placement);
    const uci = best.uci;
    const from = uci.slice(0, 2), to = uci.slice(2, 4), promo = uci[4];
    const piece = obj[from];
    delete obj[from];
    // en passant: pawn moves diagonally to an empty square
    if (piece && piece[1] === "P" && from[0] !== to[0] && !obj[to]) {
      delete obj[to[0] + from[1]];
    }
    // castling: king moves two files
    if (piece && piece[1] === "K" && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
      const rank = from[1];
      if (to[0] === "g") { obj["f" + rank] = obj["h" + rank]; delete obj["h" + rank]; }
      else { obj["d" + rank] = obj["a" + rank]; delete obj["a" + rank]; }
    }
    obj[to] = promo ? piece[0] + promo.toUpperCase() : piece;
    return objToPlacement(obj) + " " + (turn === "w" ? "b" : "w");
  }

  function placementToObj(placement) {
    const obj = {};
    placement.split("/").forEach((row, r) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { f += Number(ch); continue; }
        const sq = "abcdefgh"[f] + (8 - r);
        obj[sq] = (ch === ch.toUpperCase() ? "w" : "b") + ch.toUpperCase();
        f++;
      }
    });
    return obj;
  }

  function objToPlacement(obj) {
    const rows = [];
    for (let r = 8; r >= 1; r--) {
      let row = "", empty = 0;
      for (const file of "abcdefgh") {
        const p = obj[file + r];
        if (!p) { empty++; continue; }
        if (empty) { row += empty; empty = 0; }
        row += p[0] === "w" ? p[1] : p[1].toLowerCase();
      }
      rows.push(row + (empty || ""));
    }
    return rows.join("/");
  }

  // ---------------------------------------------------------------- overlay

  function squareRect(sq, size) {
    const f = sq.charCodeAt(0) - 97, r = Number(sq[1]) - 1;
    const s = size / 8;
    const x = state.orientation === "white" ? f * s : (7 - f) * s;
    const y = state.orientation === "white" ? (7 - r) * s : r * s;
    return { x, y, s };
  }

  function drawOverlay() {
    const svg = $id("overlay");
    const boardEl = document.querySelector("#board .board-b72b1");
    if (!boardEl || !state.data) return;
    const size = boardEl.getBoundingClientRect().width;
    const wrap = $id("board").getBoundingClientRect();
    const inner = boardEl.getBoundingClientRect();
    const ox = inner.left - wrap.left, oy = inner.top - wrap.top;
    svg.setAttribute("viewBox", `${-ox} ${-oy} ${wrap.width} ${wrap.height}`);
    let out = "";

    if (state.retry) {
      if (state.retry.tried) out += highlight(state.retry.tried, classColor(state.retry.cls || "good"), size);
      svg.innerHTML = out;
      return;
    }
    if (state.ply === 0) { svg.innerHTML = ""; return; }

    const m = state.data.moves[state.ply - 1];
    if (state.preview) {
      out += highlight(m.best_move.uci, classColor("best"), size);
      svg.innerHTML = out;
      return;
    }
    out += highlight(m.uci, classColor(m.classification), size);
    if (m.best_move && m.best_move.uci !== m.uci && !["book", "forced"].includes(m.classification)) {
      out += arrow(m.best_move.uci, size);
    }
    out += squareBadge(m.uci.slice(2, 4), m.classification, size);
    svg.innerHTML = out;
  }

  function highlight(uci, color, size) {
    let out = "";
    for (const sq of [uci.slice(0, 2), uci.slice(2, 4)]) {
      const { x, y, s } = squareRect(sq, size);
      out += `<rect x="${x}" y="${y}" width="${s}" height="${s}" fill="${color}" opacity=".45"/>`;
    }
    return out;
  }

  function arrow(uci, size) {
    const a = squareRect(uci.slice(0, 2), size), b = squareRect(uci.slice(2, 4), size);
    const x1 = a.x + a.s / 2, y1 = a.y + a.s / 2, x2 = b.x + b.s / 2, y2 = b.y + b.s / 2;
    const len = Math.hypot(x2 - x1, y2 - y1);
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
    const head = a.s * 0.42, w = a.s * 0.17;
    const ex = x2 - ux * head, ey = y2 - uy * head;
    const px = -uy, py = ux;
    const pts = [
      [x1 + px * w / 2, y1 + py * w / 2], [ex + px * w / 2, ey + py * w / 2],
      [ex + px * head / 1.6, ey + py * head / 1.6], [x2, y2],
      [ex - px * head / 1.6, ey - py * head / 1.6], [ex - px * w / 2, ey - py * w / 2],
      [x1 - px * w / 2, y1 - py * w / 2],
    ].map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ");
    return `<polygon points="${pts}" fill="${classColor("best")}" opacity=".85"/>`;
  }

  function squareBadge(sq, cls, size) {
    const { x, y, s } = squareRect(sq, size);
    const r = s * 0.2;
    const cx = x + s - r * 0.7, cy = y + r * 0.7;
    const info = CLASS_INFO[cls];
    const content = info.glyph === null
      ? `<g transform="translate(${cx - r * 0.6},${cy - r * 0.6}) scale(${(r * 1.2) / 16})" fill="#fff">${BOOK_SVG.replace(/<\/?svg[^>]*>/g, "")}</g>`
      : `<text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central" fill="#fff"
           font-size="${r * (info.glyph.length > 1 ? 0.95 : 1.15)}" font-weight="800" font-family="system-ui, sans-serif">${info.glyph}</text>`;
    return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${classColor(cls)}" stroke="rgba(0,0,0,.35)" stroke-width="1"/>${content}`;
  }

  // ---------------------------------------------------------------- retry mode

  function startRetry() {
    const m = state.data.moves[state.ply - 1];
    state.preview = null;
    state.retry = { moveIndex: state.ply - 1, fen: m.fen_before, status: "waiting", message: "", tried: null, cls: null, busy: false };
    render();
  }

  function renderRetryCard(card) {
    const r = state.retry;
    const m = state.data.moves[r.moveIndex];
    const side = m.color === "white" ? "White" : "Black";
    let msg = `Find a better move than <b>${escapeHtml(m.san)}</b> for ${side}. Drag a piece on the board.`;
    let cls = "";
    if (r.status === "busy") msg = "Checking…";
    if (r.status === "correct") { msg = `${badge(r.cls)} <b>${escapeHtml(r.san)}</b> is correct!`; cls = "ok"; }
    if (r.status === "wrong") { msg = `${badge(r.cls)} <b>${escapeHtml(r.san)}</b> is ${CLASS_INFO[r.cls].label.toLowerCase()}. Try again.`; cls = "bad"; }
    if (r.status === "error") { msg = escapeHtml(r.message); cls = "bad"; }
    if (r.status === "solution") { msg = `The best move was <b>${escapeHtml(m.best_move.san)}</b>.`; }
    card.innerHTML = `<div class="move-head">Retry ${escapeHtml(moveLabel(m))}</div>
      <div class="retry-msg ${cls}">${msg}</div>
      <div class="move-actions">
        <button class="btn" id="retry-solution">Show solution</button>
        <button class="btn" id="retry-exit">Back to game</button>
      </div>`;
    $id("retry-solution").onclick = () => {
      state.retry.fen = fenAfterUci(m.fen_before, m.best_move);
      state.retry.tried = m.best_move.uci;
      state.retry.cls = "best";
      state.retry.status = "solution";
      render();
    };
    $id("retry-exit").onclick = () => goTo(state.ply);
  }

  function onDragStart(source, piece) {
    const r = state.retry;
    if (!r || r.status === "busy" || r.status === "correct" || r.status === "solution") return false;
    const m = state.data.moves[r.moveIndex];
    return piece[0] === m.color[0];
  }

  function onDrop(source, target) {
    const r = state.retry;
    if (!r || target === "offboard" || source === target) return "snapback";
    const m = state.data.moves[r.moveIndex];
    r.status = "busy";
    renderMoveCard();
    fetch("/api/evaluate-move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fen: m.fen_before, uci: source + target, eval_before: m.eval_before,
        best_uci: m.best_move && m.best_move.uci, depth: state.data.settings.depth,
      }),
    })
      .then((res) => res.json().then((body) => ({ ok: res.ok, body })))
      .then(({ ok, body }) => {
        if (state.retry !== r) return; // user navigated away
        if (!ok) { r.status = "error"; r.message = body.error || "Server error"; r.fen = m.fen_before; render(); return; }
        if (!body.legal) { r.status = "waiting"; r.fen = m.fen_before; render(); return; }
        r.san = body.san;
        r.cls = body.classification;
        r.tried = body.uci;
        if (body.correct) {
          r.status = "correct";
          r.fen = body.fen_after;
        } else {
          r.status = "wrong";
          r.fen = m.fen_before;
        }
        render();
      })
      .catch((err) => { r.status = "error"; r.message = err.message; r.fen = m.fen_before; render(); });
  }

  // ---------------------------------------------------------------- wiring

  function flip() {
    state.orientation = state.orientation === "white" ? "black" : "white";
    board.orientation(state.orientation);
    renderPlayers();
    render();
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(state.data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    const h = state.data.headers;
    a.href = URL.createObjectURL(blob);
    a.download = `review-${(h.White || "white")}-vs-${(h.Black || "black")}.json`.replace(/[^\w.-]+/g, "_");
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function init() {
    $id("pgn-form").addEventListener("submit", startAnalysis);
    $id("load-example").addEventListener("click", () => { $id("pgn-text").value = EXAMPLE_PGN; $id("pgn-file").value = ""; });
    $id("new-analysis").addEventListener("click", showInput);
    $id("download-json").addEventListener("click", downloadJson);
    $id("flip").addEventListener("click", flip);
    document.querySelectorAll("[data-nav]").forEach((btn) => btn.addEventListener("click", () => {
      const n = state.data.moves.length;
      const target = { start: 0, prev: state.ply - 1, next: state.ply + 1, end: n }[btn.dataset.nav];
      goTo(target);
    }));
    $id("move-list").addEventListener("click", (e) => {
      const el = e.target.closest(".mv");
      if (el) goTo(Number(el.dataset.ply));
    });
    document.addEventListener("keydown", (e) => {
      if ($id("review-view").hidden || e.target.matches("textarea, input, select")) return;
      if (e.key === "ArrowLeft") { goTo(state.ply - 1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goTo(state.ply + 1); e.preventDefault(); }
      else if (e.key === "Home") goTo(0);
      else if (e.key === "End") goTo(state.data.moves.length);
      else if (e.key === "f" || e.key === "F") flip();
    });

    fetch("/api/status").then((r) => r.json()).then((s) => {
      if (!s.stockfish) setError(s.error);
    }).catch(() => {});

    // Console hook: gameReview.loadResult(json) shows a saved analysis without re-running it.
    window.gameReview = { loadResult };
    showInput();
  }

  init();
})();
