/* providerbench charts: a small, dependency-free SVG chart kit for comparing two providers.
 *
 *   PBCharts.dashboard(el, {targets: [{name, key}], records: [[...A], [...B]], money: fn})
 *       renders the standard set: four metric comparisons (TTFT, output speed, end-to-end, cost per
 *       request) and two per-request charts (speed vs latency, TTFT over the run).
 *   PBCharts.metric(el, spec) · PBCharts.scatter(el, spec) · PBCharts.timeline(el, spec)
 *       the individual charts, if a page wants only one.
 *
 * Design rules (kept on purpose):
 *   - identity colour only on marks (bars, dots, lines); every text uses text tokens;
 *   - one legend for the whole dashboard, plus selective direct labels (medians only);
 *   - bars <= 24px, rounded only at the data end; 2px lines; dots >= 8px with a 2px surface ring;
 *   - hairline solid gridlines one step off the surface; round tick values; no dual axes;
 *   - every chart has hover/focus tooltips with values first, and a table view;
 *   - colours come from CSS variables --pa / --pb (provider A / B) and the page's text/surface tokens,
 *     validated for colour-vision deficiency in light and dark (OKLab dE >= 15 under protan/deutan/tritan).
 * All labels are inserted with textContent; nothing from the data is parsed as HTML.
 */
(function (root) {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";

  // ---------------------------------------------------------------- tokens
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function dark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function tokens() {
    var d = dark();
    return {
      a: css("--pa", css("--a", d ? "#a3adf0" : "#4a55b0")),
      b: css("--pb", css("--b", d ? "#e3a77c" : "#b0602a")),
      fg1: css("--fg1", d ? "#ececec" : "#141414"),
      fg2: css("--fg2", d ? "#c2c2c2" : "#4d4d4d"),
      fg3: css("--fg3", d ? "#9a9a9a" : "#6b6b6b"),
      grid: css("--border1", d ? "#2e2e2e" : "#ececec"),
      axis: css("--border2", d ? "#3d3d3d" : "#dcdcdc"),
      surface: css("--surface2", d ? "#1e1e1e" : "#ffffff"),
      wash: d ? "rgba(255,255,255,0.035)" : "rgba(0,0,0,0.028)"
    };
  }

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function div(cls, parent, text) {
    var n = document.createElement("div");
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function txt(parent, x, y, s, o) {
    o = o || {};
    var t = el("text", { x: x, y: y, fill: o.fill, "font-size": o.size || 11, "text-anchor": o.anchor || "start",
      "dominant-baseline": o.baseline || "alphabetic", "font-weight": o.weight || null, "class": "pbc-t" + (o.tick ? " tick" : "") }, parent);
    t.textContent = s;
    return t;
  }
  function q(xs, p) {
    xs = xs.filter(function (v) { return v != null && isFinite(v); }).sort(function (a, b) { return a - b; });
    if (!xs.length) return null;
    var k = (xs.length - 1) * p, lo = Math.floor(k), hi = Math.min(lo + 1, xs.length - 1);
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo);
  }
  function nice(max, count) {
    // round tick step (1-2-5) so that `count` steps cover [0, max]
    if (!(max > 0)) return { step: 1, max: 1 };
    var raw = max / (count || 4), mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    var step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
    return { step: step, max: Math.ceil(max / step - 1e-9) * step };
  }
  var fmt = {
    ms: function (v) { return v == null ? "—" : v < 1000 ? Math.round(v) + " ms" : (v / 1000).toFixed(v < 10000 ? 2 : 1) + " s"; },
    msTick: function (v) { return v === 0 ? "0" : v < 1000 ? Math.round(v) + " ms" : +(v / 1000).toFixed(2) + " s"; },
    tps: function (v) { return v == null ? "—" : Math.round(v) + " tok/s"; },
    tpsTick: function (v) { return String(Math.round(v)); },
    usd: function (v) { return v == null ? "—" : v >= 0.995 ? "$" + v.toFixed(2) : v >= 0.01 ? "$" + v.toFixed(3) : "$" + (+v.toPrecision(2)); },
    pct: function (v) { return v == null ? "—" : Math.round(v * 100) + "%"; }
  };
  function sizeOf(container) { return Math.max(280, Math.floor(container.clientWidth || 600)); }

  // one tooltip per card, positioned inside it, never overflowing it
  function tooltip(card) {
    var tip = div("pbc-tip", card);
    tip.setAttribute("role", "status");
    return {
      show: function (rows, x, y) {
        tip.textContent = "";
        rows.forEach(function (r) {
          var row = div("pbc-tip-row", tip);
          if (r.color) { var k = div("pbc-key", row); k.style.background = r.color; }
          div("pbc-tip-v", row, r.value);
          if (r.label) div("pbc-tip-l", row, r.label);
        });
        tip.style.display = "block";
        var cw = card.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
        var left = Math.min(Math.max(8, x + 14), cw - tw - 8), top = Math.max(8, y - th - 10);
        tip.style.left = left + "px"; tip.style.top = top + "px";
      },
      hide: function () { tip.style.display = "none"; }
    };
  }

  function card(container, title, subtitle, table) {
    var c = div("pbc-card", container);
    var head = div("pbc-head", c);
    var titles = div("pbc-titles", head);
    div("pbc-title", titles, title);
    if (subtitle) div("pbc-sub", titles, subtitle);
    var body = div("pbc-body", c);
    var tbl = div("pbc-table", c);
    tbl.hidden = true;
    if (table) {
      var btn = document.createElement("button");
      btn.className = "pbc-tbtn"; btn.type = "button"; btn.textContent = "Table";
      btn.setAttribute("aria-expanded", "false");
      btn.onclick = function () {
        tbl.hidden = !tbl.hidden; body.hidden = !tbl.hidden;
        btn.textContent = tbl.hidden ? "Table" : "Chart";
        btn.setAttribute("aria-expanded", String(!tbl.hidden));
      };
      head.appendChild(btn);
      var t = document.createElement("table");
      var tr = document.createElement("tr");
      table.head.forEach(function (h) { var th = document.createElement("th"); th.textContent = h; tr.appendChild(th); });
      t.appendChild(tr);
      table.rows.forEach(function (r) {
        var row = document.createElement("tr");
        r.forEach(function (v) { var td = document.createElement("td"); td.textContent = v; row.appendChild(td); });
        t.appendChild(row);
      });
      tbl.appendChild(t);
    }
    return { card: c, body: body };
  }
  function empty(body, msg) { div("pbc-empty", body, msg || "Not enough data"); }

  // ---------------------------------------------------------------- metric comparison
  // spec: {title, subtitle, better: "lower"|"higher", format, tick, series: [{name, color, values:[...]}]}
  // Draws one row per provider: a bar to the median (p50), a hairline whisker p25–p90 with end ticks,
  // the median value at the bar end, p90 beside it, and the comparison sentence in the header.
  function metric(container, spec) {
    var T = tokens();
    var stats = spec.series.map(function (s) {
      return { name: s.name, color: s.color, n: s.values.filter(function (v) { return v != null; }).length,
               p25: q(s.values, 0.25), p50: q(s.values, 0.5), p90: q(s.values, 0.9) };
    });
    var rows = stats.map(function (s) { return [s.name, spec.format(s.p50), spec.format(s.p25), spec.format(s.p90), String(s.n)]; });
    var parts = card(container, spec.title, spec.subtitle, { head: ["Provider", "Median", "p25", "p90", "Requests"], rows: rows });
    var body = parts.body;
    if (!stats.some(function (s) { return s.p50 != null; })) { empty(body); return; }
    // comparison line (text tokens only)
    var a = stats[0], b = stats[1];
    if (a && b && a.p50 && b.p50) {
      var lowWins = spec.better === "lower", win = (lowWins ? a.p50 <= b.p50 : a.p50 >= b.p50) ? a : b, lose = win === a ? b : a;
      var r = Math.max(win.p50, lose.p50) / Math.min(win.p50, lose.p50);
      var line = div("pbc-verdict", body);
      if (r < 1.05) line.textContent = "About even";
      else {
        var k = div("pbc-key", line); k.style.background = win.color;
        var s = document.createElement("span");
        s.textContent = win.name + " " + (r >= 1.95 ? r.toFixed(1) + "× " : Math.round((1 - 1 / r) * 100) + "% ") + (spec.word || (lowWins ? "lower" : "higher"));
        line.appendChild(s);
      }
    }
    var W = sizeOf(body), padL = 0, padR = 120, rowH = 44, barH = 14, top = 6, axisH = 22;
    var max = Math.max.apply(null, stats.map(function (s) { return s.p90 || s.p50 || 0; }));
    var sc = nice(max, 4), plotW = W - padL - padR, H = top + rowH * stats.length + axisH;
    var x = function (v) { return padL + plotW * v / sc.max; };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img", "aria-label": spec.title }, body);
    for (var v = 0; v <= sc.max + 1e-9; v += sc.step) {
      var gx = x(v);
      el("line", { x1: gx, x2: gx, y1: top, y2: H - axisH + 2, stroke: v === 0 ? T.axis : T.grid, "stroke-width": 1 }, svg);
      txt(svg, gx, H - 6, spec.tick(v), { tick: true, fill: T.fg3, anchor: v === 0 ? "start" : "middle", size: 11 });
    }
    var tip = tooltip(parts.card);
    stats.forEach(function (s, i) {
      var cy = top + rowH * i + rowH / 2;
      var g = el("g", { tabindex: 0, "class": "pbc-hit" }, svg);
      if (s.p50 == null) { txt(g, padL, cy + 4, s.name + ": no data", { fill: T.fg3 }); return; }
      var w = Math.max(2, x(s.p50) - padL), r4 = Math.min(4, w / 2);
      // bar: square at the baseline, 4px rounded data end
      el("path", { d: "M" + padL + "," + (cy - barH / 2) + " h" + (w - r4) + " a" + r4 + "," + r4 + " 0 0 1 " + r4 + "," + r4 +
        " v" + (barH - 2 * r4) + " a" + r4 + "," + r4 + " 0 0 1 " + (-r4) + "," + r4 + " h" + (-(w - r4)) + " z", fill: s.color }, g);
      // whisker p25–p90 above the bar
      if (s.p25 != null && s.p90 != null) {
        var wy = cy - barH / 2 - 6;
        el("line", { x1: x(s.p25), x2: x(s.p90), y1: wy, y2: wy, stroke: T.fg3, "stroke-width": 1 }, g);
        el("line", { x1: x(s.p25), x2: x(s.p25), y1: wy - 3, y2: wy + 3, stroke: T.fg3, "stroke-width": 1 }, g);
        el("line", { x1: x(s.p90), x2: x(s.p90), y1: wy - 3, y2: wy + 3, stroke: T.fg3, "stroke-width": 1 }, g);
      }
      // direct labels: median at the bar end (fg1), p90 muted, name above-left
      var lx = Math.max(x(s.p50), s.p90 != null ? x(s.p90) : 0) + 8;
      txt(g, lx, cy + 4, spec.format(s.p50), { fill: T.fg1, size: 13, weight: 600 });
      txt(svg, padL, cy - barH / 2 - 12, s.name, { fill: T.fg2, size: 11.5 });
      el("rect", { x: 0, y: cy - rowH / 2, width: W, height: rowH, fill: "transparent" }, g);
      var show = function (e) {
        var bb = svg.getBoundingClientRect(), cb = parts.card.getBoundingClientRect();
        tip.show([{ color: s.color, value: spec.format(s.p50) + " median", label: s.name },
                  { value: spec.format(s.p25) + " – " + spec.format(s.p90), label: "p25 – p90" },
                  { value: String(s.n), label: "requests" }],
          (e && e.clientX ? e.clientX : bb.left + x(s.p50)) - cb.left, bb.top - cb.top + cy);
      };
      g.addEventListener("pointermove", show); g.addEventListener("focus", show);
      g.addEventListener("pointerleave", tip.hide); g.addEventListener("blur", tip.hide);
    });
  }

  // ---------------------------------------------------------------- scatter: speed vs latency
  // spec: {title, subtitle, x: {format, tick, label}, y: {format, tick, label}, series: [{name, color, points:[{x, y, id}]}]}
  function scatter(container, spec) {
    var T = tokens();
    var all = [];
    spec.series.forEach(function (s, i) { s.points.forEach(function (p) { if (p.x != null && p.y != null) all.push({ x: p.x, y: p.y, id: p.id, s: i }); }); });
    var rows = all.map(function (p) { return [spec.series[p.s].name, p.id || "", spec.x.format(p.x), spec.y.format(p.y)]; });
    var parts = card(container, spec.title, spec.subtitle, { head: ["Provider", "Request", spec.x.label, spec.y.label], rows: rows });
    var body = parts.body;
    if (all.length < 3) { empty(body); return; }
    var W = sizeOf(body), H = Math.round(Math.min(360, Math.max(240, W * 0.5)));
    var m = { l: 46, r: 16, t: 12, b: 34 };
    var xcap = q(all.map(function (p) { return p.x; }), 0.98), ycap = q(all.map(function (p) { return p.y; }), 0.99);
    var sx = nice(xcap * 1.05, 5), sy = nice(ycap * 1.08, 4);
    var X = function (v) { return m.l + (W - m.l - m.r) * Math.min(v, sx.max) / sx.max; };
    var Y = function (v) { return H - m.b - (H - m.t - m.b) * Math.min(v, sy.max) / sy.max; };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img", "aria-label": spec.title }, body);
    // the better direction, stated once in the corner (no shaded block over the data)
    txt(svg, m.l + 8, m.t + 12, "↖ better: faster start, faster stream", { fill: T.fg3, size: 11 });
    for (var v = 0; v <= sy.max + 1e-9; v += sy.step) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: v === 0 ? T.axis : T.grid, "stroke-width": 1 }, svg);
      txt(svg, m.l - 8, Y(v) + 4, spec.y.tick(v), { tick: true, fill: T.fg3, anchor: "end" });
    }
    for (v = 0; v <= sx.max + 1e-9; v += sx.step) {
      txt(svg, X(v), H - m.b + 18, spec.x.tick(v), { tick: true, fill: T.fg3, anchor: v === 0 ? "start" : "middle" });
    }
    txt(svg, W - m.r, H - 2, spec.x.label + " →", { fill: T.fg3, anchor: "end", size: 11 });

    var dots = el("g", {}, svg), clipped = 0;
    all.forEach(function (p) {
      if (p.x > sx.max || p.y > sy.max) clipped++;
      el("circle", { cx: X(p.x), cy: Y(p.y), r: 4, fill: spec.series[p.s].color, "fill-opacity": 0.55, stroke: T.surface, "stroke-width": 2 }, dots);
    });
    // medians: larger ringed markers, direct-labelled
    spec.series.forEach(function (s, i) {
      var px = q(s.points.map(function (p) { return p.x; }), 0.5), py = q(s.points.map(function (p) { return p.y; }), 0.5);
      if (px == null || py == null) return;
      el("circle", { cx: X(px), cy: Y(py), r: 7, fill: s.color, stroke: T.surface, "stroke-width": 2.5 }, svg);
      var right = X(px) < W - 140;
      txt(svg, X(px) + (right ? 12 : -12), Y(py) - 10, s.name + " median", { fill: T.fg1, size: 12, weight: 600, anchor: right ? "start" : "end" });
    });
    if (clipped) txt(svg, W - m.r, m.t + 10, clipped + " outlier" + (clipped > 1 ? "s" : "") + " at the edge", { fill: T.fg3, anchor: "end", size: 10.5 });
    // nearest-point hover (no pinpoint targets)
    var tip = tooltip(parts.card), ring = el("circle", { r: 7, fill: "none", stroke: T.fg1, "stroke-width": 1.5, visibility: "hidden" }, svg);
    var hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", tabindex: 0 }, svg);
    hit.addEventListener("pointermove", function (e) {
      var bb = svg.getBoundingClientRect(), k = W / bb.width, px = (e.clientX - bb.left) * k, py = (e.clientY - bb.top) * k;
      var best = null, bd = 1e9;
      all.forEach(function (p) { var d = Math.hypot(X(p.x) - px, Y(p.y) - py); if (d < bd) { bd = d; best = p; } });
      if (!best || bd > 40) { tip.hide(); ring.setAttribute("visibility", "hidden"); return; }
      ring.setAttribute("cx", X(best.x)); ring.setAttribute("cy", Y(best.y)); ring.setAttribute("visibility", "visible");
      var cb = parts.card.getBoundingClientRect();
      tip.show([{ color: spec.series[best.s].color, value: spec.x.format(best.x) + " · " + spec.y.format(best.y), label: spec.series[best.s].name },
                { value: best.id || "", label: "" }], X(best.x) / k + bb.left - cb.left, Y(best.y) / k + bb.top - cb.top);
    });
    hit.addEventListener("pointerleave", function () { tip.hide(); ring.setAttribute("visibility", "hidden"); });
  }

  // ---------------------------------------------------------------- timeline: metric over the run
  // spec: {title, subtitle, format, tick, series: [{name, color, points: [{t, y, id}]}]}  (t in seconds)
  function timeline(container, spec) {
    var T = tokens();
    var all = [];
    spec.series.forEach(function (s, i) { s.points.forEach(function (p) { if (p.t != null && p.y != null) all.push({ t: p.t, y: p.y, id: p.id, s: i }); }); });
    var rows = all.slice().sort(function (a, b) { return a.t - b.t; }).map(function (p) { return [spec.series[p.s].name, p.id || "", Math.round(p.t) + " s", spec.format(p.y)]; });
    var parts = card(container, spec.title, spec.subtitle, { head: ["Provider", "Request", "Started", "Value"], rows: rows });
    var body = parts.body;
    if (all.length < 3) { empty(body); return; }
    var W = sizeOf(body), H = Math.round(Math.min(320, Math.max(220, W * 0.42)));
    var m = { l: 52, r: 16, t: 12, b: 30 };
    var tmax = Math.max.apply(null, all.map(function (p) { return p.t; })), ycap = q(all.map(function (p) { return p.y; }), 0.98);
    var st = nice(tmax, 5), sy = nice(ycap * 1.1, 4);
    var X = function (v) { return m.l + (W - m.l - m.r) * v / st.max; };
    var Y = function (v) { return H - m.b - (H - m.t - m.b) * Math.min(v, sy.max) / sy.max; };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img", "aria-label": spec.title }, body);
    for (var v = 0; v <= sy.max + 1e-9; v += sy.step) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: v === 0 ? T.axis : T.grid, "stroke-width": 1 }, svg);
      txt(svg, m.l - 8, Y(v) + 4, spec.tick(v), { tick: true, fill: T.fg3, anchor: "end" });
    }
    for (v = 0; v <= st.max + 1e-9; v += st.step) txt(svg, X(v), H - 10, v < 60 ? Math.round(v) + " s" : Math.round(v / 60) + " min", { fill: T.fg3, anchor: v === 0 ? "start" : "middle" });
    var lines = [];
    spec.series.forEach(function (s, i) {
      var pts = all.filter(function (p) { return p.s === i; }).sort(function (a, b) { return a.t - b.t; });
      var g = el("g", {}, svg);
      pts.forEach(function (p) { el("circle", { cx: X(p.t), cy: Y(p.y), r: 2.5, fill: s.color, "fill-opacity": 0.28 }, g); });
      // rolling median (window 9, centred)
      var med = pts.map(function (p, k) { return { t: p.t, y: q(pts.slice(Math.max(0, k - 4), k + 5).map(function (z) { return z.y; }), 0.5) }; });
      if (med.length > 1) {
        el("path", { d: med.map(function (p, k) { return (k ? "L" : "M") + X(p.t).toFixed(1) + "," + Y(p.y).toFixed(1); }).join(""),
          fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
        var last = med[med.length - 1];
        el("circle", { cx: X(last.t), cy: Y(last.y), r: 4, fill: s.color, stroke: T.surface, "stroke-width": 2 }, svg);
      }
      lines.push(med);
    });
    // crosshair: snaps to the pointer's time, shows each provider's rolling median there
    var tip = tooltip(parts.card), cross = el("line", { y1: m.t, y2: H - m.b, stroke: T.fg3, "stroke-width": 1, visibility: "hidden" }, svg);
    var hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", function (e) {
      var bb = svg.getBoundingClientRect(), k = W / bb.width, px = (e.clientX - bb.left) * k;
      var t = (px - m.l) / (W - m.l - m.r) * st.max;
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      var rows = lines.map(function (med, i) {
        var near = null, bd = 1e9;
        med.forEach(function (p) { var d = Math.abs(p.t - t); if (d < bd) { bd = d; near = p; } });
        return near ? { color: spec.series[i].color, value: spec.format(near.y), label: spec.series[i].name } : null;
      }).filter(Boolean);
      rows.push({ value: (t < 60 ? Math.round(t) + " s" : (t / 60).toFixed(1) + " min") + " into the run", label: "" });
      var cb = parts.card.getBoundingClientRect();
      tip.show(rows, px / k + bb.left - cb.left, (e.clientY - cb.top));
    });
    hit.addEventListener("pointerleave", function () { tip.hide(); cross.setAttribute("visibility", "hidden"); });
  }

  // ---------------------------------------------------------------- the standard dashboard
  function dashboard(container, data) {
    container.textContent = "";
    container.classList.add("pbc");
    var T = tokens(), colors = [T.a, T.b];
    var money = data.money || fmt.usd;
    var names = data.targets.map(function (t) { return t.name; });
    var recs = data.records.map(function (rs) { return (rs || []).filter(function (r) { return !r.error && r.verdict !== "skipped"; }); });
    // shared legend, once
    var legend = div("pbc-legend", container);
    names.forEach(function (n, i) { var it = div("pbc-legend-item", legend); var k = div("pbc-key", it); k.style.background = colors[i]; it.appendChild(document.createTextNode(n)); });
    var grid = div("pbc-grid", container);
    var pick = function (key) { return names.map(function (n, i) { return { name: n, color: colors[i], values: recs[i].map(function (r) { return r[key]; }) }; }); };
    metric(div("pbc-cell", grid), { title: "Time to first token", subtitle: "Median per request · whisker p25–p90 · lower is better", better: "lower", word: "faster",
      format: fmt.ms, tick: fmt.msTick, series: pick("ttft_ms") });
    metric(div("pbc-cell", grid), { title: "Output speed", subtitle: "Median tokens per second after the first token · higher is better", better: "higher", word: "faster",
      format: fmt.tps, tick: fmt.tpsTick, series: pick("tps") });
    metric(div("pbc-cell", grid), { title: "End-to-end time", subtitle: "Median request duration · lower is better", better: "lower", word: "faster",
      format: fmt.ms, tick: fmt.msTick, series: pick("e2e_ms") });
    metric(div("pbc-cell", grid), { title: "Cost per request", subtitle: "Median, at each provider's price · lower is better", better: "lower", word: "cheaper",
      format: money, tick: function (v) { return v === 0 ? "0" : money(v); }, series: pick("cost_usd") });
    var wide = div("pbc-grid pbc-wide", container);
    scatter(div("pbc-cell", wide), { title: "Output speed vs time to first token", subtitle: "Every request · y: tok/s, x: time to first token · big dots are medians",
      x: { label: "Time to first token", format: fmt.ms, tick: fmt.msTick }, y: { label: "tok/s", format: fmt.tps, tick: fmt.tpsTick },
      series: names.map(function (n, i) { return { name: n, color: colors[i], points: recs[i].map(function (r) { return { x: r.ttft_ms, y: r.tps, id: r.task_id }; }) }; }) });
    var t0 = Math.min.apply(null, recs.reduce(function (a, rs) { return a.concat(rs.map(function (r) { return r.t; })); }, []).filter(Boolean));
    timeline(div("pbc-cell", wide), { title: "Time to first token over the run", subtitle: "Rolling median of 9 requests · dots are single requests",
      format: fmt.ms, tick: fmt.msTick,
      series: names.map(function (n, i) { return { name: n, color: colors[i], points: recs[i].map(function (r) { return { t: r.t - t0, y: r.ttft_ms, id: r.task_id }; }) }; }) });
  }

  // re-render on resize and theme change, so text never scales with the SVG
  var registry = [];
  function keep(fn, container, data) {
    fn(container, data);
    registry.push([fn, container, data]);
  }
  var timer = null;
  function rerender() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      registry = registry.filter(function (r) { return document.body.contains(r[1]); });
      registry.forEach(function (r) { r[1].textContent = ""; r[0](r[1], r[2]); });
    }, 120);
  }
  if (root.addEventListener) root.addEventListener("resize", rerender);
  if (root.MutationObserver) new MutationObserver(rerender).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if (root.matchMedia) { var mq = root.matchMedia("(prefers-color-scheme: dark)"); if (mq.addEventListener) mq.addEventListener("change", rerender); }

  root.PBCharts = {
    dashboard: function (el_, data) { keep(dashboard, el_, data); },
    metric: function (el_, spec) { keep(metric, el_, spec); },
    scatter: function (el_, spec) { keep(scatter, el_, spec); },
    timeline: function (el_, spec) { keep(timeline, el_, spec); },
    format: fmt,
    css: [
      ".pbc{--pbc-gap:16px}",
      ".pbc-legend{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 12px;font-size:13px;color:var(--fg2,#4d4d4d)}",
      ".pbc-legend-item{display:flex;align-items:center;gap:7px}",
      ".pbc-key{width:10px;height:10px;border-radius:3px;flex:none}",
      ".pbc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--pbc-gap);margin-bottom:var(--pbc-gap)}",
      "@media (max-width:900px){.pbc-grid{grid-template-columns:1fr}}",
      ".pbc-card{position:relative;border:1px solid var(--border1,#ececec);border-radius:16px;padding:16px 18px 12px;background:var(--surface2,#fff);min-width:0}",
      ".pbc-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px}",
      ".pbc-title{font-size:14px;font-weight:600;color:var(--fg1,#141414);line-height:1.3}",
      ".pbc-sub{font-size:12px;color:var(--fg3,#6b6b6b);margin-top:2px;line-height:1.4}",
      ".pbc-tbtn{border:0;background:none;color:var(--fg3,#6b6b6b);font:12px inherit;cursor:pointer;padding:2px 4px;border-radius:6px}",
      ".pbc-tbtn:hover,.pbc-tbtn:focus-visible{color:var(--fg1,#141414);background:var(--bg2,#f5f5f5)}",
      ".pbc-verdict{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--fg2,#4d4d4d);margin:2px 0 6px}",
      ".pbc-body svg{display:block;overflow:visible}",
      ".pbc-t{font-family:var(--sans,system-ui,-apple-system,'Segoe UI',sans-serif);font-variant-numeric:normal;letter-spacing:0;word-spacing:0}",
      ".pbc-t.tick{font-variant-numeric:normal}",
      ".pbc-hit{outline:none;cursor:default}",
      ".pbc-hit:focus-visible path{stroke:var(--fg1,#141414);stroke-width:1.5}",
      ".pbc-empty{color:var(--fg3,#6b6b6b);font-size:13px;padding:28px 0;text-align:center}",
      ".pbc-table{max-height:260px;overflow:auto;font-size:12.5px}",
      ".pbc-table table{border-collapse:collapse;width:100%}",
      ".pbc-table th,.pbc-table td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--border1,#ececec);white-space:nowrap;font-variant-numeric:tabular-nums}",
      ".pbc-table th{position:sticky;top:0;background:var(--surface2,#fff);color:var(--fg3,#6b6b6b);font-weight:500}",
      ".pbc-tip{position:absolute;display:none;pointer-events:none;z-index:5;background:var(--surface2,#fff);border:1px solid var(--border2,#dcdcdc);border-radius:10px;padding:8px 10px;box-shadow:0 6px 24px rgba(0,0,0,.12);font-size:12px;max-width:260px}",
      ".pbc-tip-row{display:flex;align-items:center;gap:7px;line-height:1.5}",
      ".pbc-tip-row .pbc-key{width:10px;height:2px;border-radius:1px}",
      ".pbc-tip-v{color:var(--fg1,#141414);font-weight:600;font-variant-numeric:tabular-nums}",
      ".pbc-tip-l{color:var(--fg3,#6b6b6b)}"
    ].join("\n")
  };
  // inject the stylesheet once, so a page only needs <script src="/charts.js">
  if (typeof document !== "undefined" && !document.getElementById("pbc-style")) {
    var st = document.createElement("style"); st.id = "pbc-style"; st.textContent = root.PBCharts.css;
    (document.head || document.documentElement).appendChild(st);
  }
})(typeof window !== "undefined" ? window : this);
