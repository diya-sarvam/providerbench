"""Fetching public benchmark data, with a local cache in .cache/ (git-ignored).

Datasets are fetched by URL from their official homes (Hugging Face datasets-server, GitHub raw);
nothing is vendored into the repo.
"""
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from .config import HOME

CACHE = HOME / "cache"
HF_ROWS = "https://datasets-server.huggingface.co/rows"
UA = "providerbench/0.1 (+https://github.com/)"


def http_get(url, timeout=90, retries=4, binary=False, cache=True):
    """GET with retries; cached forever under .cache/ keyed by URL (datasets are versioned)."""
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = CACHE / "http" / key
    if cache and path.exists():
        raw = path.read_bytes()
        return raw if binary else raw.decode("utf-8", "replace")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            return raw if binary else raw.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - network is flaky, retry everything
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET %s failed: %s" % (url, last))


def hf_count(dataset, split, config="default"):
    q = urllib.parse.urlencode({"dataset": dataset, "config": config, "split": split,
                                "offset": 0, "length": 1})
    return json.loads(http_get(HF_ROWS + "?" + q))["num_rows_total"]


def hf_rows(dataset, split, offset, length, config="default"):
    """Rows [offset, offset+length) of a Hugging Face dataset split, as plain dicts."""
    out = []
    while length > 0:
        n = min(length, 100)
        q = urllib.parse.urlencode({"dataset": dataset, "config": config, "split": split,
                                    "offset": offset, "length": n})
        page = json.loads(http_get(HF_ROWS + "?" + q))
        rows = [r["row"] for r in page.get("rows", [])]
        out.extend(rows)
        if len(rows) < n:
            break
        offset, length = offset + n, length - n
    return out


def hf_all(dataset, split, config="default"):
    return hf_rows(dataset, split, 0, hf_count(dataset, split, config), config)


def jsonl(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]
