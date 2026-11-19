/* providerbench snap: a "Copy image" button on every table, scorecard and chart.
 *
 * Click it and the element is rendered to a PNG (2x, in the current theme, with a short caption and a
 * small "providerbench" footer) and put on the clipboard, ready to paste into a post. If the browser
 * can't write images to the clipboard, the PNG is downloaded instead.
 *
 * Rendering uses html-to-image (MIT), loaded once from a pinned CDN version on first use; the rest of
 * the UI never needs it. Targets are found by CSS selector and buttons are re-attached whenever the UI
 * re-renders, so pages only need to load this file.
 */
(function (root) {
  "use strict";
  var LIB = "https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js";
  // what gets a button, and what to call it in the file name
  var TARGETS = [
    [".scorewrap", "scorecard"],
    ["table.cmp", "all-numbers"],
    ["table.stable", "by-suite"],
    [".turns-table, table.turns", "cache-by-turn"],
    [".pbc-card", "chart"],
    ["table.ptable", "requests"],
    [".hcard", "run"],
    ["#p-table", "prices"]
  ];
  var loading = null;

  function lib() {
    if (root.htmlToImage) return Promise.resolve(root.htmlToImage);
    if (loading) return loading;
    loading = new Promise(function (ok, bad) {
      var s = document.createElement("script");
      s.src = LIB; s.async = true;
      s.onload = function () { ok(root.htmlToImage); };
      s.onerror = function () { loading = null; bad(new Error("could not load the image renderer (offline?)")); };
      document.head.appendChild(s);
    });
    return loading;
  }

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function caption(el) {
    if (el.closest(".hcard")) return "";                     // a history card already names the run
    var t = document.querySelector(".rtitle"), m = document.querySelector(".rmeta");
    if (!t) return "";
    var meta = m ? Array.prototype.slice.call(m.childNodes).map(function (n) { return n.textContent.trim(); })
      .filter(function (s) { return s && s !== "·" && !/^(done|running|stopped|paused|error|interrupted)$/i.test(s); }).slice(0, 3) : [];
    var name = (t.innerText || t.textContent).replace(/\s+/g, " ").trim().replace(/(\S)vs(\S)/, "$1 vs $2");
    return name + (meta.length ? "  ·  " + meta.join(" · ") : "");
  }

  function title(el) {
    var h = el.querySelector(".pbc-title");
    if (h) return h.textContent.trim();
    var prev = el.closest(".scroll-x") ? el.closest(".scroll-x").previousElementSibling : el.previousElementSibling;
    while (prev && !/^H[1-4]$/.test(prev.tagName) && prev.classList && prev.classList.contains("small")) prev = prev.previousElementSibling;
    return prev && /^H[1-4]$/.test(prev.tagName) ? prev.textContent.trim() : "";
  }

  // a detached frame holding: caption, title, a copy of the element at its natural width, footer
  function frame(el) {
    var bg = css("--surface2", "#ffffff"), fg2 = css("--fg2", "#555"), fg3 = css("--fg3", "#777");
    var w = Math.max(el.scrollWidth, el.offsetWidth);
    var box = document.createElement("div");
    box.className = "snap-frame";
    box.style.cssText = "position:fixed;left:-100000px;top:0;padding:22px 26px 16px;background:" + bg +
      ";width:" + (w + 52) + "px;font-family:inherit;color:" + css("--fg1", "#141414");
    var cap = caption(el), ttl = title(el);
    if (cap) { var c = document.createElement("div"); c.textContent = cap; c.style.cssText = "font-size:15px;font-weight:600;margin-bottom:" + (ttl ? "2px" : "12px"); box.appendChild(c); }
    if (ttl && !el.querySelector(".pbc-title")) { var t = document.createElement("div"); t.textContent = ttl; t.style.cssText = "font-size:13px;color:" + fg2 + ";margin-bottom:12px"; box.appendChild(t); }
    var copy = el.cloneNode(true);
    copy.style.width = w + "px"; copy.style.maxWidth = "none"; copy.style.margin = "0";
    copy.querySelectorAll(".snap-btn, .pbc-tbtn, button[data-ctl], .pbc-tip").forEach(function (n) { n.remove(); });
    box.appendChild(copy);
    var foot = document.createElement("div");
    foot.textContent = "providerbench · same requests, two providers";
    foot.style.cssText = "margin-top:12px;font-size:11px;color:" + fg3 + ";text-align:right";
    box.appendChild(foot);
    document.body.appendChild(box);
    return { box: box, bg: bg };
  }

  function fileName(kind) {
    var r = document.querySelector(".rtitle"), t = r ? (r.innerText || r.textContent) : "providerbench";
    return ("providerbench-" + kind + "-" + t).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "").slice(0, 80) + ".png";
  }

  function toast(msg) {
    if (typeof root.toast === "function") { root.toast(msg); return; }
    var n = document.createElement("div");
    n.className = "snap-toast"; n.textContent = msg;
    document.body.appendChild(n);
    setTimeout(function () { n.remove(); }, 2200);
  }

  function render(el) {
    return lib().then(function (h2i) {
      var f = frame(el);
      // the frame sits off-screen while it is laid out; render the copy at the origin, at its real size
      var opts = { pixelRatio: 2, backgroundColor: f.bg, cacheBust: false,
                   width: f.box.offsetWidth, height: f.box.offsetHeight,
                   style: { position: "static", left: "0", top: "0", margin: "0", transform: "none" },
                   filter: function (n) { return !(n.classList && (n.classList.contains("snap-btn"))); } };
      return h2i.toBlob(f.box, opts).then(function (blob) { f.box.remove(); return blob; },
                                         function (e) { f.box.remove(); throw e; });
    });
  }

  function download(blob, name) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
  }

  function snap(el, kind, btn) {
    var name = fileName(kind);
    if (btn) { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = "Rendering…"; }
    var done = function (msg) { if (btn) { btn.disabled = false; btn.textContent = btn.dataset.label || "Copy image"; } if (msg) toast(msg); };
    var blobP = render(el);
    var canClip = root.ClipboardItem && navigator.clipboard && navigator.clipboard.write && root.isSecureContext;
    if (canClip) {
      // pass the promise straight to ClipboardItem so the write stays inside the click's user gesture
      navigator.clipboard.write([new ClipboardItem({ "image/png": blobP })])
        .then(function () { done("Image copied. Paste it anywhere."); })
        .catch(function () {
          blobP.then(function (b) { download(b, name); done("Clipboard blocked, image downloaded instead."); },
                     function (e) { done(e.message); });
        });
    } else {
      blobP.then(function (b) { download(b, name); done("Image downloaded."); }, function (e) { done(e.message); });
    }
  }

  function attach() {
    TARGETS.forEach(function (t) {
      document.querySelectorAll(t[0]).forEach(function (el) {
        if (el.closest(".snap-frame") || el.closest(".pbc-table")) return;
        var host = el.tagName === "TABLE" ? (el.closest(".scroll-x") || el.parentElement) : el;
        if (!host || host.querySelector(":scope > .snap-btn")) return;
        if (getComputedStyle(host).position === "static") host.style.position = "relative";
        host.classList.add("snap-host");
        var b = document.createElement("button");
        b.type = "button"; b.className = "snap-btn"; b.textContent = "Copy image";
        b.title = "Copy this " + t[1].replace(/-/g, " ") + " as a PNG";
        b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); snap(el, t[1], b); });
        host.appendChild(b);
      });
    });
  }

  var queued = false;
  // a timer, not requestAnimationFrame: rAF is paused in background tabs, so buttons would go missing there
  function schedule() { if (queued) return; queued = true; setTimeout(function () { queued = false; attach(); }, 60); }
  if (root.MutationObserver) new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", attach);

  var st = document.createElement("style");
  st.textContent = [
    ".snap-host{}",
    ".snap-btn{position:absolute;top:8px;right:8px;z-index:4;font:500 11.5px/1 var(--sans,system-ui);padding:6px 10px;border-radius:999px;",
    "border:1px solid var(--border2,#ddd);background:var(--surface2,#fff);color:var(--fg2,#555);cursor:pointer;opacity:0;transition:opacity .15s ease-out}",
    ".snap-host:hover>.snap-btn,.snap-btn:focus-visible{opacity:1}",
    "@media (hover:none){.snap-btn{opacity:.85}}",
    ".snap-btn:hover{color:var(--fg1,#141414);border-color:var(--border3,#bbb)}",
    ".snap-btn:disabled{opacity:1;cursor:progress}",
    ".pbc-card>.snap-btn{top:12px;right:64px}",
    // the scorecard's top-right holds the win tally: put the button in the free space beside it
    ".scorewrap>.snap-btn{top:2px;right:auto;left:100%;margin-left:12px;white-space:nowrap}",
    "@media (max-width:1100px){.scorewrap>.snap-btn{left:auto;right:0;top:auto;bottom:-34px}}",
    // history cards: bottom-right, clear of the status badge and the tally
    ".hcard>.snap-btn{top:auto;bottom:10px;right:12px}",
    ".snap-toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--fg1,#141414);color:var(--surface2,#fff);",
    "padding:9px 16px;border-radius:999px;font:13px var(--sans,system-ui);z-index:99}"
  ].join("");
  document.head.appendChild(st);

  root.pbSnap = { snap: snap, attach: attach, render: render };
})(typeof window !== "undefined" ? window : this);
