"""Two fake OpenAI-compatible providers for developing the UI and runner without keys or spend.

    mock-swift   127.0.0.1:18081   fast, mostly right, reports cached tokens
    mock-steady  127.0.0.1:18082   slower, wrong more often, ~8% HTTP 429, some broken tool JSON

They do not know the answers: for math/GPQA they answer from a hash of the prompt, and "know" the
right answer only when it is visible in the prompt, which it never is. So accuracy is noise; the
point is exercising the plumbing (streaming, usage, tools, errors).
"""
import hashlib
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler

PROFILES = {
    18081: {"name": "swift", "ttft": 0.15, "tps": 220, "err429": 0.0, "badjson": 0.0, "cache": True},
    18082: {"name": "steady", "ttft": 0.45, "tps": 90, "err429": 0.08, "badjson": 0.15, "cache": False},
}


def _fill(schema, rng):
    t = (schema or {}).get("type")
    if "enum" in (schema or {}):
        return schema["enum"][0]
    if t == "integer":
        return rng.randint(1, 10)
    if t == "number":
        return round(rng.uniform(1, 10), 2)
    if t == "boolean":
        return True
    if t == "array":
        return [_fill(schema.get("items") or {"type": "string"}, rng)]
    if t == "object":
        props = schema.get("properties") or {}
        return {k: _fill(props[k], rng) for k in schema.get("required", list(props)[:1]) if k in props}
    return "value"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = {}            # port -> set of prefix hashes, for the fake prefix cache

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": m} for m in ("glm-5.3-flash", "deepseek-v4.1-flash", "gemma-4-31b")]})
            self._send(200, body.encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        port = self.server.server_address[1]
        prof = PROFILES[port]
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs, tools = req.get("messages", []), req.get("tools")
        blob = json.dumps(msgs)
        rng = random.Random(hashlib.sha256((blob + prof["name"]).encode()).hexdigest())
        if rng.random() < prof["err429"]:
            return self._send(429, b'{"error":{"message":"rate limited"}}', "application/json")
        prompt_tokens = len(blob) // 4
        cached = 0
        seen = Handler.seen.setdefault(port, set())
        for i in range(len(msgs), 0, -1):
            h = hashlib.sha256(json.dumps(msgs[:i]).encode()).hexdigest()
            if h in seen:
                cached = len(json.dumps(msgs[:i])) // 4
                break
        for i in range(1, len(msgs) + 1):
            seen.add(hashlib.sha256(json.dumps(msgs[:i]).encode()).hexdigest())

        last = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
        last = last if isinstance(last, str) else json.dumps(last)
        thinking = "Let me think about this step by step. " * rng.randint(3, 12)
        calls, answer = [], ""
        if tools and "irrelevan" not in last.lower() and rng.random() > 0.05:
            n = 3 if "cities" in last or "each" in last else 1
            for i in range(n):
                fn = tools[min(i, len(tools) - 1)]["function"]
                args = json.dumps(_fill(fn.get("parameters") or {}, rng))
                if rng.random() < prof["badjson"]:
                    args = args[:-1]
                calls.append({"index": i, "id": "call_%d" % i, "type": "function",
                              "function": {"name": fn["name"], "arguments": args}})
        elif req.get("response_format"):
            answer = json.dumps({"name": "Ada", "age": 36, "tags": ["x"]})
        elif "boxed" in last:
            answer = "The answer is \\boxed{%d}." % rng.choice([42, 70, 3, 12])
        elif "A, B, C or D" in last or "Answer:" in last:
            answer = "Answer: %s" % rng.choice("ABCD")
        elif "exactly" in last.lower() and ":" in last:
            answer = last.split(":", 1)[1].strip()
        else:
            answer = "Hello! How can I help you today?"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        ttft = prof["ttft"] * (1 + (prompt_tokens - cached) / 20000)
        time.sleep(ttft)
        step = 1.0 / prof["tps"]

        def emit(obj):
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()

        completion = 0
        for word in thinking.split(" "):
            emit({"choices": [{"index": 0, "delta": {"reasoning_content": word + " "}}]})
            completion += 1
            time.sleep(step)
        for i in range(0, len(answer), 4):
            emit({"choices": [{"index": 0, "delta": {"content": answer[i:i + 4]}}]})
            completion += 1
            time.sleep(step)
        for c in calls:
            emit({"choices": [{"index": 0, "delta": {"tool_calls": [c]}}]})
            completion += len(c["function"]["arguments"]) // 4
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
                 "completion_tokens_details": {"reasoning_tokens": len(thinking.split(" "))}}
        if prof["cache"]:
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
        emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
        emit({"choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def start():
    from .server import LocalServer
    servers = []
    for port in PROFILES:
        srv = LocalServer(("127.0.0.1", port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
    return servers


def seed_prices():
    from . import pricing
    pricing.set_manual("mock-swift", "glm-5.3-flash", 0.15, 0.5, 0.03)
    pricing.set_manual("mock-steady", "glm-5.3-flash", 0.08, 0.30)
    for m in ("deepseek-v4.1-flash", "gemma-4-31b"):
        pricing.set_manual("mock-swift", m, 0.3, 1.2, 0.06)
        pricing.set_manual("mock-steady", m, 0.2, 0.8)
