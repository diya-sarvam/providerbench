"""Local web UI. Binds to 127.0.0.1 only; the API is described in docs/api.md."""
import json
import os
import re
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config, datasets, failures, pricing, runner
from .suites import METHOD, SCENARIOS, SUITES

UI = Path(__file__).resolve().parent / "ui" / "index.html"


class LocalServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse-DNS lookup (socket.getfqdn) that HTTPServer does on
    bind, which can hang for a long time on some machines."""
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def state():
    providers = config.load_providers()
    models = config.load_models()
    doc = pricing.load()
    return {
        "providers": [p.info() for p in providers.values()],
        "models": [{"id": k, "label": v.get("label", k), "context": v.get("context")} for k, v in models.items()],
        "suites": [s.info() for s in SUITES.values()],
        "scenarios": SCENARIOS,
        "method": METHOD,
        "currency": "USD",
        "fx": doc.get("fx") or pricing.DEFAULT_FX,
        "failure_codes": {c: {"kind": k, "label": label} for c, (k, label) in failures.CODES.items()},
    }


def clean_spec(body):
    suites = {}
    for k, v in (body.get("suites") or {}).items():
        if k not in SUITES:
            continue
        v = dict(v) if isinstance(v, dict) else {"n": v}
        if int(v.get("n") or 0) > 0:
            suites[k] = v
    spec = {"targets": list(body.get("targets") or []),
            "scenario": body.get("scenario") or "",
            "suites": runner.suite_params(suites),
            "seed": body.get("seed") if body.get("seed") not in ("", None) else None,
            "concurrency": max(1, min(int(body.get("concurrency") or 4), 256)),
            "budget_usd": float(body["budget_usd"]) if body.get("budget_usd") not in (None, "") else None,
            "thinking": "off" if body.get("thinking") == "off" else "default"}
    for k in ("temperature", "top_p"):
        if body.get(k) not in (None, ""):
            spec[k] = float(body[k])
    if not spec["suites"]:
        raise ValueError("pick at least one suite")
    return spec


class Handler(BaseHTTPRequestHandler):
    server_version = "providerbench"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, body, ctype, name):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name.replace('"', ""))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _local_only(self):
        # the API saves keys and spends money: refuse cross-site browser requests
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlsplit(origin).netloc != host:
            self._json({"error": "cross-origin request refused"}, 403)
            return False
        return True

    def do_GET(self):
        u = urlsplit(self.path)
        p, q = u.path.rstrip("/") or "/", parse_qs(u.query)
        try:
            if p == "/":
                body = UI.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif p in ("/md.js", "/charts.js"):
                data = (UI.parent / p.lstrip("/")).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif p.startswith("/api/runs/") and p.endswith("/tasks"):
                idx = runner.task_index(p.split("/")[3])
                self._json(idx if idx is not None else {"error": "no such run"}, 200 if idx is not None else 404)
            elif p.startswith("/api/runs/") and p.endswith("/compare"):
                cmp_ = runner.compare(p.split("/")[3], q.get("task", [""])[0])
                self._json(cmp_ if cmp_ else {"error": "no such request"}, 200 if cmp_ else 404)
            elif p.startswith("/api/runs/") and p.endswith("/export"):
                rid, fmt = p.split("/")[3], q.get("format", ["md"])[0]
                if fmt == "html":
                    page = runner.export_html(rid)
                    if page is None:
                        return self._json({"error": "no such run"}, 404)
                    if q.get("view"):
                        body = page.encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self._file(page.encode(), "text/html; charset=utf-8", "providerbench_%s.html" % rid)
                else:
                    md = runner.export_markdown(rid, int(q.get("limit", [6000])[0] or 0))
                    if md is None:
                        return self._json({"error": "no such run"}, 404)
                    self._file(md.encode(), "text/markdown; charset=utf-8", "providerbench_%s.md" % rid)
            elif p.startswith("/fonts/"):
                fonts = (config.HOME / "fonts").resolve()
                f = (fonts / p.split("/", 2)[2]).resolve()
                if f.parent != fonts or not f.exists() or f.suffix != ".woff2":
                    self._json({"error": "not found"}, 404)
                else:
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "font/woff2")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
            elif p == "/api/state":
                self._json(state())
            elif p == "/api/runs":
                self._json(runner.list_runs())
            elif p.startswith("/api/runs/") and p.endswith("/results"):
                rid = p.split("/")[3]
                g = lambda k, d=None: q.get(k, [d])[0]  # noqa: E731
                self._json(runner.records(rid, g("target", ""), g("only"), g("suite"), g("q"),
                                          int(g("offset", 0) or 0), g("limit")))
            elif p.startswith("/api/runs/") and p.endswith("/trace"):
                rid = p.split("/")[3]
                tr = runner.trace(rid, q.get("target", [""])[0], q.get("task", [""])[0])
                if tr is None:
                    self._json({"error": "no such request"}, 404)
                elif q.get("download"):
                    name = re.sub(r"[^A-Za-z0-9._-]+", "_", "%s_%s_%s" % (rid, q["target"][0], q["task"][0])) + ".json"
                    self._file(json.dumps(tr, indent=1).encode(), "application/json", name)
                else:
                    self._json(tr)
            elif p.startswith("/api/runs/") and p.endswith("/download"):
                rid = p.split("/")[3]
                tgt = q.get("target", [None])[0]
                if tgt:
                    recs = runner._all_records(rid, tgt)
                    body = "".join(json.dumps(r) + "\n" for r in recs).encode()
                    self._file(body, "application/x-ndjson", "%s_%s.jsonl" % (rid, tgt.replace(":", "_")))
                else:
                    blob = runner.export_zip(rid)
                    if blob is None:
                        self._json({"error": "no such run"}, 404)
                    else:
                        self._file(blob, "application/zip", "providerbench_%s.zip" % rid)
            elif p.startswith("/api/runs/"):
                snap = runner.get_run(p.split("/")[3])
                self._json(snap or {"error": "no such run"}, 200 if snap else 404)
            elif p == "/api/prices":
                self._json(pricing.rows())
            elif p == "/api/datasets":
                self._json(datasets.list_all())
            elif p == "/api/datasets/brief":
                self._json(datasets.brief())
            elif p == "/api/flags":
                self._json(runner.flags(q.get("run", [None])[0], q.get("status", [None])[0] or None))
            elif p == "/api/flags/export":
                rows = runner.export_labels()
                self._file("".join(json.dumps(r) + "\n" for r in rows).encode(), "application/x-ndjson",
                           "providerbench_degeneration_labels.jsonl")
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        if not self._local_only():
            return
        p = urlsplit(self.path).path.rstrip("/")
        try:
            body = self._body()
            if p == "/api/keys":
                provs = config.load_providers()
                vals = {provs[k].key_env: v.strip() for k, v in body.items()
                        if k in provs and provs[k].key_env and isinstance(v, str) and v.strip()}
                config.save_keys(vals)
                self._json({"saved": [k for k in body if k in provs and provs[k].key_env in vals]})
            elif p == "/api/keys/delete":
                prov = config.load_providers().get(body.get("provider"))
                if not prov or not prov.key_env:
                    raise ValueError("unknown provider")
                self._json({"removed": config.delete_key(prov.key_env)})
            elif p == "/api/estimate":
                spec = clean_spec(body)
                if not spec["targets"]:
                    raise ValueError("pick at least one target")
                self._json(runner.estimate(spec))
            elif p == "/api/preview":
                self._json(runner.preview(clean_spec(body)))
            elif p == "/api/datasets/delete":
                self._json({"removed": datasets.delete(body.get("id"))})
            elif p == "/api/datasets":
                self._json(datasets.save(str(body.get("name") or "upload.jsonl")[:120], body.get("content") or ""))
            elif p == "/api/runs":
                if body.get("replay"):
                    spec, tasks = runner.replay_spec(body["replay"])
                    run = runner.Run(spec, tasks).start()
                else:
                    spec = clean_spec(body)
                    if not spec["targets"]:
                        raise ValueError("pick at least one target")
                    run = runner.Run(spec).start()
                self._json({"id": run.id})
            elif p.startswith("/api/runs/") and p.endswith("/stop"):
                self._json({"ok": runner.stop(p.split("/")[3])})
            elif p.startswith("/api/runs/") and p.endswith("/reviews"):
                rv = runner.set_review(p.split("/")[3], body.get("target"), body.get("task_id"),
                                       body.get("label"), body.get("note", ""), body.get("spans"))
                self._json({"ok": True, "review": rv})
            elif p.startswith("/api/runs/") and p.endswith("/control"):
                run = runner.RUNS.get(p.split("/")[3])
                if not run:
                    raise ValueError("run is not active in this server")
                run.control(body.get("target"), body.get("action"))
                self._json({"ok": True})
            elif p == "/api/prices/refresh":
                log = []
                pricing.refresh(log=log.append)
                self._json({"ok": True, "log": log})
            elif p == "/api/prices/manual":
                pricing.set_manual(body["provider"], body["model"], body["input"], body["output"],
                                   body.get("cached_input"), body.get("currency") or "USD")
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, KeyError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)


def serve(port=8765, mock=False):
    config.migrate_legacy()
    config.load_env()
    if mock:
        from . import mock as mockmod
        os.environ["PB_MOCK"] = "1"
        mockmod.start()
        mockmod.seed_prices()
    if not pricing.PRICES.exists():
        threading.Thread(target=lambda: pricing.refresh(log=lambda *_: None), daemon=True).start()
    srv = LocalServer(("127.0.0.1", port), Handler)
    note = "  (mock providers on :18081, :18082)" if mock else ""
    print("providerbench on http://127.0.0.1:%d%s" % (port, note), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
