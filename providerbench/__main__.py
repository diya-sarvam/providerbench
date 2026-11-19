"""pb: A/B test inference providers on the same tasks.

    pb serve [--port 8765] [--mock]
    pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "agentic:n=5,turns=3" [--seed N] [--budget 2]
    pb run A B --suites "synthetic:n=50,isl=8000,osl=512,cache_hit=0.6,turns=4"
    pb run A B --data my-traces.jsonl --suites "agentic:n=20,turns=2"      (your own sessions/requests)
    pb run A B --suites tools=30,math=20                                    (quality benchmarks)
    pb run ... --preview                                                    (show the sample, send nothing)
    pb run --replay <run-id>
    pb providers
    pb keys [list | set <provider> | rm <provider>]   (stored in ~/.providerbench/.env, never in the repo)
    pb data check <file.jsonl> | pb data brief     (validate your own traces; print the brief for your agent)
    pb doctor [--install-hook]                        (check nothing private is in the repo before a push)
    pb models                                       (what each provider serves vs what is mapped)
    pb provider add <id> <base_url> [--key-env ENV] [--name NAME]
    pb provider check <id>
    pb prices [refresh | set <provider> <model> <input> <output> [--cached X] [--currency USD]]
    pb suites
    pb show <run-id> [--json]
    pb export <run-id> [--format zip|md|html] [--out f] (every request and reply: raw, Markdown, or a side-by-side HTML report)
"""
import argparse
import json
import re
import sys
import time

from . import config, doctor, failures, pricing, probe, report, runner, server
from .suites import SUITES


def _val(v):
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return v


def parse_suites(s, n):
    """'tools=30,math' | 'agentic:n=5,turns=3;synthetic:isl=2000,osl=256'"""
    out = {}
    parts = s.split(";") if ":" in (s or "") else (s or "").split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, _, rest = part.partition(":")
            params = {k.strip(): _val(v.strip()) for k, _, v in (kv.partition("=") for kv in rest.split(",") if kv.strip())}
        else:
            name, _, cnt = part.partition("=")
            params = {"n": int(cnt)} if cnt else {}
        name = name.strip()
        if name not in SUITES:
            raise SystemExit("unknown suite %r (have: %s)" % (name, ", ".join(SUITES)))
        params.setdefault("n", n or SUITES[name].default_n)
        out[name] = params
    return out


def fmt_ms(x):
    return "—" if x is None else "%.2f s" % (x / 1000)


def print_run(snap):
    print("\nrun %s  seed %s  %s  status %s" % (snap["id"], snap["seed"], snap.get("task_hash") or "", snap["status"]))
    rows = [("accuracy", lambda s: "—" if s["accuracy"] is None else "%.1f%% (%d/%d)" % (100 * s["accuracy"], s["passed"], s["passed"] + s["failed"])),
            ("cost / correct", lambda s: report.money(s["cost_per_correct_usd"])),
            ("cost total", lambda s: report.money(s["cost_usd"])),
            ("ttft p50 cold", lambda s: fmt_ms(s["ttft_cold_ms"]["p50"])),
            ("ttft p50 warm", lambda s: fmt_ms(s["ttft_warm_ms"]["p50"])),
            ("output tok/s", lambda s: "—" if s["tps"]["p50"] is None else "%.0f" % s["tps"]["p50"]),
            ("ISL / OSL", lambda s: "—" if s.get("isl_mean") is None else "%.0f / %.0f" % (s["isl_mean"], s["osl_mean"] or 0)),
            ("throughput", lambda s: "—" if not s.get("throughput_tps") else "%.0f tok/s" % s["throughput_tps"]),
            ("e2e p50", lambda s: fmt_ms(s["e2e_ms"]["p50"])),
            ("cache (warm)", lambda s: "not reported" if not s["cache_reported"] else ("—" if s["cache_pct"] is None else "%.0f%%" % (100 * s["cache_pct"]))),
            ("errors", lambda s: str(s["errored"]))]
    ts = [t for t in snap["targets"] if t["summary"]]
    w = max([28] + [len(t["target"]) + 2 for t in ts])
    print("\n%-16s" % "" + "".join(("%-" + str(w) + "s") % t["target"] for t in ts))
    for label, f in rows:
        print("%-16s" % label + "".join(("%-" + str(w) + "s") % f(t["summary"]) for t in ts))
    for t in ts:
        suites = ", ".join("%s %s" % (k, "—" if v["accuracy"] is None else "%d/%d" % (v["passed"], v["passed"] + v["failed"]))
                           for k, v in t["summary"]["by_suite"].items())
        for k, v in t["summary"]["by_suite"].items():
            if v.get("match"):
                suites += "\n  %s agreement: %s" % (k, ", ".join("%s %d" % (m, c) for m, c in v["match"].items() if c))
        fails = ", ".join("%s×%d" % (c, n) for c, n in t["summary"]["failures"].items())
        print("\n%s\n  by suite: %s\n  failures: %s" % (t["target"], suites, fails or "none"))
    if snap.get("verdict"):
        print("\n" + "\n".join("· " + v for v in snap["verdict"]))


def cmd_run(a):
    config.load_env()
    if a.replay:
        spec, tasks = runner.replay_spec(a.replay)
    else:
        if len(a.targets) < 1:
            raise SystemExit("give at least one target, e.g. fireworks:glm-5.3-flash")
        suites = parse_suites(a.suites, a.n)
        if a.data:
            from . import datasets
            from pathlib import Path
            meta = datasets.save(Path(a.data).name, Path(a.data).read_text())
            print("data: %s -> %d sessions, %d requests, %d steps%s" % (
                a.data, meta["sessions"], meta["requests"], meta["steps"],
                (" (%d bad lines, first: %s)" % (len(meta["errors"]), meta["errors"][0])) if meta["errors"] else ""))
            suites.setdefault("custom", {"n": a.n or 20})["source"] = "upload:" + meta["id"]
            for k in [k for k in suites if k != "custom" and a.suites == "tools,math"]:
                suites.pop(k)
        spec = {"targets": a.targets, "suites": runner.suite_params(suites), "seed": a.seed,
                "concurrency": a.concurrency, "budget_usd": a.budget, "thinking": a.thinking}
        if a.temperature is not None:
            spec["temperature"] = a.temperature
        tasks = None
        if a.preview:
            pv = runner.preview(spec)
            print("seed %d · %d requests per target · %s" % (pv["seed"], pv["requests"], pv["task_hash"]))
            for name, s in pv["suites"].items():
                print("\n%s (%d groups)" % (name, s["total_groups"]))
                for g in s["groups"]:
                    toks = g["prompt_tokens"]
                    print("  %-48s %d turn(s)  ~%s tok  %s" % (g["label"][:48], g["turns"],
                          "%dk" % (toks[0] // 1000) if len(toks) == 1 else "%dk→%dk" % (toks[0] // 1000, toks[-1] // 1000),
                          "; ".join(g["expected"][:2])))
            print("\nrun it with --seed %d" % pv["seed"])
            return
    est = runner.estimate(spec, tasks)
    for t in est["targets"]:
        print("estimate %-40s %s" % (t["target"], report.money(t["usd"]) if t["priced"] else "no price (run `pb prices refresh` or set one)"))
    run = runner.Run(spec, tasks).start()
    last = ""
    while run.status in ("starting", "sampling", "running"):
        time.sleep(1)
        line = "  ".join("%s %d/%d" % (k, s["done"], s["total"]) for k, s in run.state.items())
        if line != last and not a.json:
            print("\r" + line + "  $%.4f" % run.spend, end="", flush=True)
            last = line
    snap = run.snapshot()
    if a.json:
        print(json.dumps(snap, indent=1))
    else:
        print()
        if run.error:
            print("error:", run.error)
        print_run(snap)
        print("\nresults: %s/" % (runner.RESULTS / run.id))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pb", description="A/B test inference providers on the same tasks.")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="local web UI")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--mock", action="store_true", help="add two local fake providers (no keys, no spend)")

    r = sub.add_parser("run", help="run suites against targets")
    r.add_argument("targets", nargs="*", help="provider:model, e.g. fireworks:glm-5.3-flash")
    r.add_argument("--suites", default="tools,math",
                   help="'tools=30,math=20' or 'agentic:n=5,turns=3;synthetic:isl=2000,osl=256' (see pb suites)")
    r.add_argument("--data", help="your own JSONL (sessions or requests), replayed by the agentic suite")
    r.add_argument("--preview", action="store_true", help="print the sample and exit; nothing is sent")
    r.add_argument("--temperature", type=float)
    r.add_argument("-n", type=int, help="count for suites given without =count")
    r.add_argument("--seed", type=int, help="replay a sample; default is a fresh sample")
    r.add_argument("--replay", help="send exactly the tasks of a past run")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--budget", type=float, default=2.0, help="USD hard stop for the run")
    r.add_argument("--thinking", choices=("default", "off"), default="default")
    r.add_argument("--mock", action="store_true")
    r.add_argument("--json", action="store_true")

    sub.add_parser("providers", help="list providers and key status")
    ky = sub.add_parser("keys", help="list, set or remove saved API keys (~/.providerbench/.env)")
    ky.add_argument("action", nargs="?", default="list", choices=("list", "set", "rm"))
    ky.add_argument("provider", nargs="?")
    da = sub.add_parser("data", help="check a JSONL of your own sessions/requests, or print the agent brief")
    da.add_argument("action", choices=("check", "brief"))
    da.add_argument("file", nargs="?")
    dr = sub.add_parser("doctor", help="check that nothing private is inside the repository")
    dr.add_argument("--install-hook", action="store_true", help="also add a git pre-commit hook that runs it")
    sub.add_parser("models", help="compare each provider's /models with models.toml and the provider files")
    pv = sub.add_parser("provider", help="add or check a provider")
    pvs = pv.add_subparsers(dest="pcmd")
    pa = pvs.add_parser("add")
    pa.add_argument("id")
    pa.add_argument("base_url")
    pa.add_argument("--key-env")
    pa.add_argument("--name")
    pc = pvs.add_parser("check")
    pc.add_argument("id")

    pr = sub.add_parser("prices", help="show, refresh or set prices")
    pr.add_argument("action", nargs="?", default="show", choices=("show", "refresh", "set"))
    pr.add_argument("args", nargs="*")
    pr.add_argument("--cached", type=float)
    pr.add_argument("--currency", default="USD")

    sub.add_parser("suites", help="list suites")
    ex = sub.add_parser("export", help="write a run's traces (run.json, tasks.json, *.jsonl) to a zip")
    ex.add_argument("run_id")
    ex.add_argument("--out")
    ex.add_argument("--format", choices=("zip", "md", "html"), default="zip",
                    help="zip: raw records; md: readable Markdown; html: self-contained side-by-side report")
    ex.add_argument("--limit", type=int, default=6000, help="md: clip long texts at this many characters (0 = no limit)")
    sh = sub.add_parser("show", help="print a past run")
    sh.add_argument("run_id")
    sh.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    config.migrate_legacy()
    if a.cmd == "serve":
        server.serve(a.port, a.mock)
    elif a.cmd == "run":
        if a.mock:
            from . import mock
            import os
            os.environ["PB_MOCK"] = "1"
            mock.start()
            mock.seed_prices()
        cmd_run(a)
    elif a.cmd == "providers":
        config.load_env()
        for p in config.load_providers().values():
            key = "key saved" if p.key else ("no key needed" if not p.key_env else "set " + p.key_env)
            print("%-12s %-12s %-14s %s" % (p.id, p.name, key, ", ".join(p.models)))
    elif a.cmd == "keys":
        config.load_env()
        provs = config.load_providers()
        if a.action == "list":
            for p in provs.values():
                if p.key_env:
                    print("%-12s %-22s %s" % (p.id, p.key_env, "saved" if p.key else "—"))
            print("stored in %s" % config.ENV_FILE)
        else:
            if not a.provider:
                raise SystemExit("give a provider id: " + ", ".join(k for k, v in provs.items() if v.key_env))
            p = provs.get(a.provider)
            # a provider not added yet (pb provider add comes next) uses <ID>_API_KEY
            env = p.key_env if p and p.key_env else re.sub(r"[^A-Z0-9]", "_", a.provider.upper()) + "_API_KEY"
            name = p.name if p else a.provider
            if a.action == "rm":
                print("removed %s" % env if config.delete_key(env) else "no saved key for %s" % name)
            else:
                import getpass
                val = getpass.getpass("%s key, saved as %s (input hidden): " % (name, env)).strip()
                if val:
                    config.save_keys({env: val})
                    print("saved to %s" % config.ENV_FILE)
    elif a.cmd == "data":
        from . import datasets
        if a.action == "brief":
            print(datasets.brief()["brief"])
        else:
            if not a.file:
                raise SystemExit("pb data check <file.jsonl>")
            from pathlib import Path
            items, errors = datasets.parse(Path(a.file).read_text())
            st = datasets.stats(items)
            print("%s: %d sessions, %d requests, %d replayable steps, ~%s tokens per line, tools: %s" % (
                a.file, st["sessions"], st["requests"], st["steps"], format(st["tokens_mean"], ","), ", ".join(st["tools"]) or "none"))
            for e in errors[:20]:
                print("  error " + e)
            if len(errors) > 20:
                print("  ... %d more errors" % (len(errors) - 20))
            if not items:
                print("  no usable lines")
            sys.exit(1 if errors or not items else 0)
    elif a.cmd == "doctor":
        ok = doctor.check()
        if a.install_hook:
            doctor.install_hook()
        sys.exit(0 if ok else 1)
    elif a.cmd == "models":
        probe.audit()
    elif a.cmd == "provider":
        if a.pcmd == "add":
            probe.add(a.id, a.base_url, a.key_env, a.name)
        elif a.pcmd == "check":
            sys.exit(0 if probe.check(a.id) else 1)
        else:
            pv.print_help()
    elif a.cmd == "prices":
        config.load_env()
        if a.action == "refresh":
            pricing.refresh()
        elif a.action == "set":
            if len(a.args) != 4:
                raise SystemExit("pb prices set <provider> <model> <input per 1M> <output per 1M>")
            pricing.set_manual(a.args[0], a.args[1], a.args[2], a.args[3], a.cached, a.currency)
        for row in pricing.rows():
            print("%-11s %-22s in %-9g cached %-9g out %-9g %s  %s%s" % (
                row["provider"], row["model"], row["input"], row["cached_input"], row["output"], row["currency"],
                row["source"], " · " + row["quant"] if row.get("quant") else ""))
    elif a.cmd == "suites":
        for s in SUITES.values():
            print("%-11s [%s] %s" % (s.name, s.scenario, s.blurb))
            print("            params: " + ", ".join("%s=%s" % (p["key"], p["default"]) for p in s.params()))
    elif a.cmd == "export":
        if a.format == "md":
            doc = runner.export_markdown(a.run_id, a.limit)
            blob = doc.encode() if doc is not None else None
        elif a.format == "html":
            doc = runner.export_html(a.run_id)
            blob = doc.encode() if doc is not None else None
        else:
            blob = runner.export_zip(a.run_id)
        if blob is None:
            raise SystemExit("no run %s" % a.run_id)
        out = a.out or "providerbench_%s.%s" % (a.run_id, a.format)
        open(out, "wb").write(blob)
        print("wrote %s (%d KB)" % (out, len(blob) // 1024))
    elif a.cmd == "show":
        snap = runner.get_run(a.run_id)
        if not snap:
            raise SystemExit("no run %s" % a.run_id)
        print(json.dumps(snap, indent=1)) if a.json else print_run(snap)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
