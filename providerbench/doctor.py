"""`pb doctor`: make sure nothing private is inside the repository before it is pushed or deployed.

Checks every file git would publish (tracked + untracked-but-not-ignored):
  - no file that holds private data by its path (.env*, run history, uploads, licensed fonts, ...)
  - no saved API key value from ~/.providerbench/.env appears in any file, verbatim
  - no string that looks like a provider secret (sk-..., Bearer ..., key = "...")
and that the private data directory is outside the repo with owner-only permissions.

`pb doctor --install-hook` adds a git pre-commit hook that runs it and blocks the commit on a finding.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

from . import config

ROOT = config.ROOT
BAD_PATHS = [
    (re.compile(r"(^|/)\.env($|\.)(?!example$)"), "an env file (keys)"),
    (re.compile(r"(^|/)(results|runs|uploads|\.cache|cache)/"), "run history, uploads or caches"),
    (re.compile(r"(^|/)(prices|calibration|tasks|run)\.json$"), "local run data"),
    (re.compile(r"\.jsonl$"), "request records"),
    (re.compile(r"\.(woff2?|ttf|otf)$"), "a font file (Matter / Season Mix are licensed)"),
    (re.compile(r"\.(zip|tar|gz)$"), "an archive (exports contain prompts and replies)"),
]
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token)\b\s*[=:]\s*['\"][A-Za-z0-9._~+/=-]{20,}['\"]"),
    re.compile(r"\b(fw|tgp|gsk|xai|hf)_[A-Za-z0-9]{24,}\b"),
]
ALLOW_IN = {"providerbench/doctor.py"}          # this file names the patterns


def repo_files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                             capture_output=True, check=True).stdout.decode()
        return [f for f in out.split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        return [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git/" not in str(p)]


def saved_key_values():
    vals = []
    if config.ENV_FILE.exists():
        for line in config.ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if len(v) >= 12:
                    vals.append(v)
    return vals


def check(log=print):
    problems = []
    home = config.HOME.resolve()
    if str(home).startswith(str(ROOT.resolve()) + os.sep):
        problems.append("PB_HOME (%s) is inside the repository" % home)
    if home.exists() and stat.S_IMODE(home.stat().st_mode) & 0o077:
        problems.append("%s is readable by other users (run: chmod 700 %s)" % (home, home))
    if config.ENV_FILE.exists() and stat.S_IMODE(config.ENV_FILE.stat().st_mode) & 0o077:
        problems.append("%s is readable by other users (run: chmod 600 %s)" % (config.ENV_FILE, config.ENV_FILE))
    keys = saved_key_values()
    files = repo_files()
    for f in files:
        for rx, why in BAD_PATHS:
            if rx.search(f) and not f.startswith("examples/"):
                problems.append("%s: %s would be published" % (f, why))
                break
        if f in ALLOW_IN:
            continue
        path = ROOT / f
        try:
            if path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for k in keys:
            if k in text:
                problems.append("%s: contains one of your saved API keys" % f)
        for rx in SECRET_PATTERNS:
            m = rx.search(text)
            if m:
                problems.append("%s: looks like a secret (%s…)" % (f, m.group(0)[:12]))
                break
    log("private data: %s (outside the repo%s)" % (home, ", %d saved key(s)" % len(keys) if keys else ""))
    log("checked %d files that git would publish" % len(files))
    for p in problems:
        log("  ✗ " + p)
    if not problems:
        log("  ✓ nothing private in the repository")
    return not problems


HOOK = """#!/bin/sh
# providerbench: block commits that would publish keys or local run data
exec ./pb doctor
"""


def install_hook(log=print):
    hooks = ROOT / ".git" / "hooks"
    if not hooks.exists():
        raise SystemExit("not a git checkout")
    path = hooks / "pre-commit"
    if path.exists() and "providerbench" not in path.read_text():
        raise SystemExit("%s already exists; add `./pb doctor` to it yourself" % path)
    path.write_text(HOOK)
    path.chmod(0o755)
    log("installed %s" % path)
