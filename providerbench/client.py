"""One streaming chat-completions request, timed.

- TTFT: request written -> first streamed token of any kind (reasoning, answer or tool call).
  TCP/TLS connect is timed separately and excluded.
- Output TPS: (completion_tokens - 1) / (last token - first token), only when >= 32 tokens.
- The API key is sent only to the provider's own host: the request URL's host must equal the host
  of the provider's base_url, and redirects are never followed.
"""
import http.client
import json
import socket
import time
from urllib.parse import urlsplit

MIN_TOKENS_FOR_TPS = 32
KEEP_CHARS = 200_000        # per field, in saved results


def _code_for_status(status):
    if status == 429:
        return "http_429"
    return "http_5xx" if status >= 500 else "http_4xx"


def chat(provider, model_id, body, session="", timeout=900, live=None, stop=None):
    """POST body (stream forced on) and return a reply dict; never raises for network errors.

    live: optional dict updated in place with "thinking"/"answer" tails for the UI.
    stop: optional callable; when it returns True the stream is abandoned.
    """
    url = provider.url(model_id)
    u = urlsplit(url)
    if u.hostname != provider.host():
        raise RuntimeError("refusing to send %s key to %s" % (provider.name, u.hostname))
    body = dict(body, model=model_id, stream=True)
    if provider.quirks.get("stream_usage", True):
        body.setdefault("stream_options", {"include_usage": True})
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
               "User-Agent": "providerbench/0.1"}
    for k, v in provider.headers.items():
        headers[k] = str(v).replace("{session}", session)
    if provider.key:
        headers["Authorization"] = "Bearer " + provider.key

    r = {"error": None, "code": None, "status": None, "message": {"content": "", "reasoning": "", "tool_calls": []},
         "finish_reason": None, "usage": None}
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=timeout)
    content, reasoning, calls = [], [], {}
    t_first = t_first_answer = t_first_reason = t_last = None
    try:
        tc = time.perf_counter()
        conn.connect()
        r["connect_ms"] = (time.perf_counter() - tc) * 1000
        t0 = time.perf_counter()
        conn.request("POST", u.path + ("?" + u.query if u.query else ""), body=json.dumps(body).encode(),
                     headers=headers)
        resp = conn.getresponse()
        r["status"] = resp.status
        if resp.status != 200:
            r["code"] = _code_for_status(resp.status)
            r["error"] = "HTTP %d: %s" % (resp.status, resp.read(1500).decode("utf-8", "replace"))
            r["e2e_ms"] = (time.perf_counter() - t0) * 1000
            return r
        ctype = resp.getheader("Content-Type", "")
        if "text/event-stream" not in ctype:      # some servers ignore stream=true
            raw = resp.read()
            t_first = t_last = time.perf_counter()
            obj = json.loads(raw or b"{}")
            ch = (obj.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            content.append(msg.get("content") or "")
            reasoning.append(msg.get("reasoning_content") or msg.get("reasoning") or "")
            for i, c in enumerate(msg.get("tool_calls") or []):
                fn = c.get("function") or {}
                calls[i] = {"id": c.get("id") or "", "name": fn.get("name") or "",
                            "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str)
                            else json.dumps(fn.get("arguments") or {})}
            r["finish_reason"] = ch.get("finish_reason")
            r["usage"] = obj.get("usage")
            if not (content[0] or reasoning[0] or calls):
                t_first = None
        else:
            for raw in resp:
                if stop and stop():
                    r["error"], r["code"] = "stopped", "stopped"
                    break
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                now = time.perf_counter()
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("error"):
                    r["error"], r["code"] = "stream error: %s" % json.dumps(obj["error"])[:600], "stream_error"
                    break
                if obj.get("usage"):
                    r["usage"] = obj["usage"]
                for ch in obj.get("choices") or []:
                    d = ch.get("delta") or {}
                    rs = d.get("reasoning_content") or d.get("reasoning") or ""
                    if not isinstance(rs, str):
                        rs = json.dumps(rs)
                    c = d.get("content") or ""
                    tc_text = ""
                    for call in d.get("tool_calls") or []:
                        fn = call.get("function") or {}
                        slot = calls.setdefault(call.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        slot["id"] = call.get("id") or slot["id"]
                        slot["name"] += fn.get("name") or ""
                        a = fn.get("arguments") or ""
                        slot["arguments"] += a if isinstance(a, str) else json.dumps(a)
                        tc_text += (fn.get("name") or "") + (a if isinstance(a, str) else "")
                    if rs:
                        reasoning.append(rs)
                        t_first_reason = t_first_reason or now
                    if c:
                        content.append(c)
                    if c or tc_text:
                        t_first_answer = t_first_answer or now
                    if rs or c or tc_text:
                        t_first = t_first or now
                        t_last = now
                        if live is not None:
                            if rs:
                                live["thinking"] = (live.get("thinking", "") + rs)[-600:]
                            if c or tc_text:
                                live["answer"] = (live.get("answer", "") + c + tc_text)[-600:]
                    if ch.get("finish_reason"):
                        r["finish_reason"] = ch["finish_reason"]
        t_end = time.perf_counter()
    except socket.timeout as e:
        r["error"], r["code"] = "timeout: %s" % e, "timeout"
        return r
    except (OSError, http.client.HTTPException, ValueError) as e:
        r["error"], r["code"] = "%s: %s" % (type(e).__name__, e), "connection"
        return r
    finally:
        conn.close()

    r["e2e_ms"] = (t_end - t0) * 1000
    msg = r["message"]
    msg["content"] = "".join(content)[:KEEP_CHARS]
    msg["reasoning"] = "".join(reasoning)[:KEEP_CHARS]
    msg["tool_calls"] = [{"id": c["id"] or "call_%d" % i, "type": "function",
                          "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                         for i, c in sorted(calls.items())]
    if t_first is None:
        if not r["error"]:
            r["error"], r["code"] = "HTTP 200 but no tokens (finish_reason=%s)" % r["finish_reason"], "empty_200"
        return r

    usage = r["usage"] or {}
    pd = usage.get("prompt_tokens_details") or {}
    cd = usage.get("completion_tokens_details") or {}
    out_chars = len(msg["content"]) + len(msg["reasoning"]) + sum(len(c["arguments"]) for c in calls.values())
    completion = usage.get("completion_tokens")
    r["usage_reported"] = completion is not None
    if completion is None:
        completion = int(out_chars / 3.6) + 1
    cached = pd.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens", usage.get("cached_tokens"))
    r["cache_reported"] = cached is not None
    reason_tok = cd.get("reasoning_tokens")
    r["reasoning_estimated"] = reason_tok is None and bool(msg["reasoning"])
    if reason_tok is None:
        reason_tok = int(round(completion * len(msg["reasoning"]) / out_chars)) if out_chars else 0
    gen_s = (t_last - t_first) if t_last else 0
    r.update(ttft_ms=(t_first - t0) * 1000,
             ttfa_ms=(t_first_answer - t0) * 1000 if t_first_answer else None,
             thinking_ms=((t_first_answer or t_last) - t_first_reason) * 1000 if t_first_reason else None,
             prompt_tokens=usage.get("prompt_tokens"), cached_tokens=cached or 0,
             completion_tokens=completion, reasoning_tokens=reason_tok,
             tps=(completion - 1) / gen_s if completion >= MIN_TOKENS_FOR_TPS and gen_s > 0 else None)
    return r


def get_json(provider, path, timeout=30):
    """GET <base><path> with the provider's key (e.g. /models). Returns (status, json|None, text)."""
    url = provider.url("", path) if "{model}" not in provider.base_url else None
    if not url:
        return None, None, "base_url is per-model"
    u = urlsplit(url)
    if u.hostname != provider.host():
        raise RuntimeError("refusing to send key to %s" % u.hostname)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=timeout)
    headers = {"Accept": "application/json", "User-Agent": "providerbench/0.1"}
    if provider.key:
        headers["Authorization"] = "Bearer " + provider.key
    try:
        conn.request("GET", u.path + ("?" + u.query if u.query else ""), headers=headers)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(text), text
        except ValueError:
            return resp.status, None, text
    except (OSError, http.client.HTTPException) as e:
        return None, None, "%s: %s" % (type(e).__name__, e)
    finally:
        conn.close()
