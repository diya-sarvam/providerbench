/* providerbench Markdown renderer: a small, safe subset for reading model replies.
 * Everything is HTML-escaped first; only these constructs become markup:
 *   fenced code (```lang), indented headings (#..######), blockquotes (>), ordered and unordered
 *   lists (nested by indentation), GFM tables, horizontal rules, paragraphs and line breaks,
 *   inline `code`, **bold**, *italic* / _italic_, ~~strike~~, [text](http(s) url), bare http(s) URLs.
 * Math: $…$, $$…$$, \(…\), \[…\] and bare \boxed{…} are kept away from Markdown and emitted as
 *   <span class="math"> / <div class="math display"> holding the raw TeX; pbRenderMath(el) typesets
 *   them with KaTeX (MIT), loaded once from a pinned CDN version. Offline, the TeX stays readable.
 * Raw HTML in the input is shown as text. The renderer itself needs no network.
 * Usage: pbMarkdown(text) -> HTML string; pbRenderMath(element). Exposed on window.
 */
(function (root) {
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function boxed(s) {
    // bare \boxed{...} with balanced braces -> $\boxed{...}$ (models often skip the dollars)
    var out = "", i = 0, k;
    while ((k = s.indexOf("\\boxed{", i)) >= 0) {
      var before = s.slice(0, k), open = (before.match(/\$/g) || []).length % 2 === 1;
      var depth = 0, j = k + 6;   // k + 6 is the opening brace of \boxed{
      for (; j < s.length; j++) { if (s[j] === "{") depth++; else if (s[j] === "}") { depth--; if (depth === 0) break; } }
      if (depth !== 0 || open) { out += s.slice(i, k + 7); i = k + 7; continue; }
      out += s.slice(i, k) + "$" + s.slice(k, j + 1) + "$";
      i = j + 1;
    }
    return out + s.slice(i);
  }

  function inline(s) {
    // s is already escaped. Protect code spans and math first.
    var codes = [], maths = [];
    s = s.replace(/`([^`\n]+)`/g, function (_, c) { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = boxed(s);
    function m(disp) { return function (_, tex) { maths.push([tex, disp]); return "\u0001" + (maths.length - 1) + "\u0001"; }; }
    s = s.replace(/\$\$([^\n]+?)\$\$/g, m(true)).replace(/\\\[([^\n]+?)\\\]/g, m(true)).replace(/\\\(([^\n]+?)\\\)/g, m(false));
    s = s.replace(/\$(?=\S)([^$\n]*?\S)\$(?!\d)/g, m(false));
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, t, u) {
      return '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + "</a>";
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, function (_, pre, u) {
      return pre + '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + "</a>";
    });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = s.replace(/\u0001(\d+)\u0001/g, function (_, i) {
      var x = maths[+i];
      return x[1] ? '<span class="math display">' + x[0] + "</span>" : '<span class="math">' + x[0] + "</span>";
    });
    return s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return "<code>" + codes[+i] + "</code>"; });
  }

  function isTableSep(line) { return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line); }
  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return c.trim(); });
  }

  function render(text) {
    var lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
    var out = [], i = 0, para = [];
    function flush() {
      if (para.length) { out.push("<p>" + para.map(function (l) { return inline(esc(l)); }).join("<br>") + "</p>"); para = []; }
    }
    while (i < lines.length) {
      var line = lines[i];
      var fence = line.match(/^\s*(```+|~~~+)\s*([\w+#.-]*)\s*$/);
      if (fence) {
        flush();
        var close = fence[1], body = [];
        i++;
        while (i < lines.length && lines[i].trim().indexOf(close) !== 0) { body.push(lines[i]); i++; }
        i++;
        out.push('<pre class="md-code"' + (fence[2] ? ' data-lang="' + esc(fence[2]) + '"' : "") + "><code>" + esc(body.join("\n")) + "</code></pre>");
        continue;
      }
      if (!line.trim()) { flush(); i++; continue; }
      var dm = line.trim().match(/^(\$\$|\\\[)(.*)$/);
      if (dm) {                                 // display math block, possibly over several lines
        var closer = dm[1] === "$$" ? "$$" : "\\]", rest = dm[2], tex = [];
        var endAt = rest.indexOf(closer);
        if (endAt >= 0 && rest.slice(endAt + closer.length).trim() === "") {
          flush(); out.push('<div class="math display">' + esc(rest.slice(0, endAt)) + "</div>"); i++; continue;
        }
        if (endAt < 0) {
          var j = i + 1, found = -1;
          tex.push(rest);
          while (j < lines.length && j < i + 60) {
            var e = lines[j].indexOf(closer);
            if (e >= 0) { tex.push(lines[j].slice(0, e)); found = j; break; }
            tex.push(lines[j]); j++;
          }
          if (found >= 0) { flush(); out.push('<div class="math display">' + esc(tex.join("\n")) + "</div>"); i = found + 1; continue; }
        }
      }
      var h = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (h) { flush(); out.push("<h" + h[1].length + ' class="md-h">' + inline(esc(h[2])) + "</h" + h[1].length + ">"); i++; continue; }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
      if (/^\s{0,3}>/.test(line)) {
        flush();
        var q = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) { q.push(lines[i].replace(/^\s{0,3}>\s?/, "")); i++; }
        out.push("<blockquote>" + render(q.join("\n")) + "</blockquote>");
        continue;
      }
      if (line.indexOf("|") >= 0 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        flush();
        var head = cells(line), rows = [];
        i += 2;
        while (i < lines.length && lines[i].indexOf("|") >= 0 && lines[i].trim()) { rows.push(cells(lines[i])); i++; }
        out.push('<div class="md-table"><table><thead><tr>' + head.map(function (c) { return "<th>" + inline(esc(c)) + "</th>"; }).join("") +
          "</tr></thead><tbody>" + rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + inline(esc(c)) + "</td>"; }).join("") + "</tr>"; }).join("") +
          "</tbody></table></div>");
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        flush();
        var items = [];
        while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          var m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (m) items.push({ indent: m[1].replace(/\t/g, "    ").length, ordered: /\d/.test(m[2]), text: m[3] });
          else items[items.length - 1].text += "\n" + lines[i].trim();
          i++;
        }
        out.push(listHtml(items, 0, items.length ? items[0].indent : 0).html);
        continue;
      }
      para.push(line);
      i++;
    }
    flush();
    return out.join("\n");
  }

  function listHtml(items, start, indent) {
    var ordered = items[start] && items[start].ordered, h = ordered ? "<ol>" : "<ul>", i = start;
    while (i < items.length && items[i].indent >= indent) {
      if (items[i].indent > indent) {
        var sub = listHtml(items, i, items[i].indent);
        h = h.replace(/<\/li>$/, sub.html + "</li>");
        i = sub.next;
        continue;
      }
      h += "<li>" + items[i].text.split("\n").map(function (l) { return inline(esc(l)); }).join("<br>") + "</li>";
      i++;
    }
    return { html: h + (ordered ? "</ol>" : "</ul>"), next: i };
  }

  root.pbMarkdown = render;

  var KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/", loading = null;
  function loadKatex() {
    if (root.katex) return Promise.resolve(root.katex);
    if (loading) return loading;
    loading = new Promise(function (ok, bad) {
      var css = document.createElement("link");
      css.rel = "stylesheet"; css.href = KATEX + "katex.min.css"; document.head.appendChild(css);
      var js = document.createElement("script");
      js.src = KATEX + "katex.min.js"; js.async = true;
      js.onload = function () { ok(root.katex); };
      js.onerror = function () { loading = null; bad(new Error("KaTeX not reachable")); };
      document.head.appendChild(js);
    });
    return loading;
  }
  // Typeset every .math element under `el` (no-op when there is none; raw TeX stays if offline).
  root.pbRenderMath = function (el) {
    var nodes = (el || document).querySelectorAll(".math:not([data-tex-done])");
    if (!nodes.length) return Promise.resolve(0);
    return loadKatex().then(function (k) {
      nodes.forEach(function (n) {
        var tex = n.textContent;
        try { k.render(tex, n, { displayMode: n.classList.contains("display"), throwOnError: false, strict: "ignore" }); }
        catch (e) { n.textContent = tex; }
        n.setAttribute("data-tex-done", "1");
        n.title = tex;
      });
      return nodes.length;
    }).catch(function () { nodes.forEach(function (n) { n.classList.add("math-raw"); }); return 0; });
  };
})(typeof window !== "undefined" ? window : this);
