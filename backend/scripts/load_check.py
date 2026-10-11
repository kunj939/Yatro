"""Phase 5 measurement (ADR 0008): how do N plans running at the same moment behave in ONE process?

    python scripts/load_check.py --levels 1,5,10,20 --provider-latency-ms 0

Simulated providers (free), SQLite, one uvicorn worker in this process, real HTTP streaming. `--provider-latency-ms` adds a
sleep to every simulated provider call, standing in for network wait (which costs no CPU). With 0 you see the CPU-bound
floor: the planner's own work and the database. Writes docs/perf/load-<date>.md and .json. Never calls a real provider.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parent / "docs" / "perf"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,5,10,20")
    ap.add_argument("--provider-latency-ms", type=int, default=0)
    ap.add_argument("--latency-mix", action="store_true",
                    help="realistic mix: 94%% 300-800 ms, 5%% 3-5 s, 1%% a 6 s timeout that fails the call")
    args = ap.parse_args()
    db = Path(tempfile.mkdtemp(prefix="yatro-load-")) / "load.db"
    os.environ.update({"DATABASE_URL": os.environ.get("LOAD_DATABASE_URL") or f"sqlite:///{db}", "RATE_LIMIT_ENABLED": "false", "FREE_TRIP_PLANS": "5000",
                       "TELEMETRY_ENABLED": "false", "SMTP_HOST": "", "SMS_PROVIDER": "console", "OTP_RESEND_SECONDS": "0",
                       "ENVIRONMENT": "development", "LOG_LEVEL": "ERROR", "DEMO_MODE": "true", "LLM_PROVIDER": "template",
                       "BUDGET_MODE": "off"})
    os.chdir(ROOT)

    import httpx
    import uvicorn

    import app.models  # noqa: F401
    from app.api.deps import providers_dep
    from app.db.base import Base
    from app.db.session import engine
    from app.main import app
    from baseline import next_saturday
    from tests.sim_providers import sim_providers
    Base.metadata.create_all(engine)
    providers = sim_providers("normal")
    if args.latency_mix:
        _slow_down(providers, _mix)
    elif args.provider_latency_ms:
        _slow_down(providers, lambda: args.provider_latency_ms / 1000)
    app.dependency_overrides[providers_dep] = lambda: providers

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    base = "http://127.0.0.1:8765/api"
    for _ in range(100):
        try:
            httpx.get(base + "/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    c = httpx.Client(base_url=base, timeout=300)
    email, phone = "load@example.com", "+919800088888"
    codes = {ch: c.post("/auth/send-code", json={"channel": ch, "target": t}).json()["dev_code"]
             for ch, t in (("email", email), ("phone", phone))}
    tok = c.post("/auth/register", json={"name": "Load", "email": email, "phone": phone, "password": "loadtest123",
                                         "password_confirm": "loadtest123", "email_code": codes["email"],
                                         "phone_code": codes["phone"]}).json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    d = next_saturday(2)
    body = {"origin": {"name": "Jaipur"}, "destination": {"name": "New Delhi"}, "start_date": d.isoformat(),
            "end_date": d.isoformat(), "start_time": "05:00", "end_time": "23:30", "budget": 8000, "travellers": 2,
            "traveller_type": "friends", "transport_modes": ["train", "bus", "metro"], "food": "both",
            "trip_styles": ["balanced"], "stay": "no_stay"}
    proc = _proc()
    rows = []

    def one(_i: int) -> dict:
        tid = c.post("/trips", json=body, headers=auth).json()["id"]
        t0 = time.perf_counter()
        first = stage_first = None
        queued = False
        status = 0
        unavailable = False
        with httpx.Client(base_url=base, timeout=300) as cc, cc.stream("POST", f"/trips/{tid}/plan", headers=auth,
                                                                       json={"stream": True}) as r:
            status = r.status_code
            for line in r.iter_lines():
                if not line:
                    continue
                if first is None:
                    first = time.perf_counter() - t0  # the first line of any kind (a place in line counts: the user sees it)
                ev = json.loads(line) if line[:1] == "{" else {}
                if ev.get("type") == "queue":
                    queued = True
                if ev.get("type") == "stage" and stage_first is None:
                    stage_first = time.perf_counter() - t0  # the first real planning stage
                if ev.get("type") == "result" and ev.get("state") == "unavailable":
                    unavailable = True
        return {"status": status, "wall": time.perf_counter() - t0, "first": first or 0.0, "stage": stage_first or 0.0,
                "queued": queued, "unavailable": unavailable}

    one(0)  # warm the process (imports, caches) so level 1 measures steady state
    for n in [int(x) for x in args.levels.split(",")]:
        peak = {"threads": 0, "rss": 0}
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                peak["threads"] = max(peak["threads"], threading.active_count())
                if proc:
                    peak["rss"] = max(peak["rss"], proc.memory_info().rss)
                time.sleep(0.05)
        threading.Thread(target=sample, daemon=True).start()
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as pool:
            res = list(pool.map(one, range(n)))
        total = time.perf_counter() - t0
        stop.set()
        walls = sorted(r["wall"] for r in res)
        firsts = sorted(r["first"] for r in res)
        stages = sorted(r["stage"] for r in res)
        running_now = [r for r in res if not r["queued"]]  # plans that did not have to wait in line
        rows.append({"concurrent": n, "ok": sum(1 for r in res if r["status"] == 200 and not r["unavailable"]),
                     "unavailable": sum(1 for r in res if r["unavailable"]), "failed": sum(1 for r in res if r["status"] != 200),
                     "p50_s": round(statistics.median(walls), 2), "p95_s": round(_pct(walls, 95), 2), "max_s": round(walls[-1], 2),
                     "first_event_p95_s": round(_pct(firsts, 95), 2), "first_stage_p95_s": round(_pct(stages, 95), 2),
                     "first_stage_p95_not_queued_s": round(_pct(sorted(r["stage"] for r in running_now), 95), 2) if running_now else None,
                     "waited_in_line": sum(1 for r in res if r["queued"]), "batch_s": round(total, 2),
                     "plans_per_min": round(n / total * 60, 1), "peak_threads": peak["threads"],
                     "peak_rss_mb": round(peak["rss"] / 1e6) if peak["rss"] else None})
        print(rows[-1], flush=True)
    server.should_exit = True
    _write(rows, args.provider_latency_ms, args.latency_mix)


def _pct(xs: list[float], p: int) -> float:
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def _proc():
    try:
        import psutil
        return psutil.Process()
    except Exception:
        return None


_RNG = random.Random(20261007)
_RNG_LOCK = threading.Lock()


def _mix() -> float:
    """Seconds for one provider call. 6.0 means "hit the timeout" (the caller raises ProviderUnavailable after the wait)."""
    with _RNG_LOCK:
        r = _RNG.random()
        if r < 0.01:
            return 6.0
        if r < 0.06:
            return _RNG.uniform(3.0, 5.0)
        return _RNG.uniform(0.3, 0.8)


def _slow_down(providers, seconds) -> None:
    """Sleep before every public method of each simulated provider (network wait: no CPU, no GIL). `seconds` is a function."""
    import functools
    for name in ("maps", "places", "transport", "search", "weather", "llm"):
        obj = getattr(providers, name, None)
        if obj is None:
            continue
        for attr in dir(obj):
            fn = getattr(obj, attr, None)
            if attr.startswith("_") or not callable(fn):
                continue

            def wrap(f):
                @functools.wraps(f)
                def inner(*a, **k):
                    s = seconds()
                    time.sleep(s)
                    if s >= 6.0:
                        from app.core.errors import ProviderUnavailable
                        raise ProviderUnavailable("simulated timeout")
                    return f(*a, **k)
                return inner
            try:
                setattr(obj, attr, wrap(fn))
            except Exception:
                pass


def _write(rows: list[dict], latency: int, mix: bool = False) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    tag = f"load-{stamp}" + ("-mix" if mix else (f"-{latency}ms" if latency else "")) + (f"-{os.environ['LOAD_TAG']}" if os.environ.get("LOAD_TAG") else "")  # LOAD_TAG keeps one run from overwriting another's report
    (DOCS / f"{tag}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if mix:
        how = " with a realistic latency mix on every provider call (94% 300-800 ms, 5% 3-5 s, 1% a 6 s timeout that fails the call)"
    elif latency:
        how = f" with {latency} ms added to every provider call"
    else:
        how = " with no added latency (CPU and database floor)"
    lines = [f"# Load check — {stamp}", "",
             f"Generated by `scripts/load_check.py` (ADR 0008). One uvicorn worker, SQLite, simulated providers{how}. "
             "Each plan is a Jaipur → New Delhi day trip, streamed. The process RSS includes this test client.", "",
             "First event = the first line of any kind (with the runner, a waiting plan's first line is its place in line). "
             "First stage = the first real planning stage. 'Not queued' = plans that did not have to wait.", "",
             "| Plans at once | OK | Unavailable | Failed | p50 s | p95 s | Max s | First event p95 s | First stage p95 s (all) | "
             "First stage p95 s (not queued) | Waited in line | Plans/min | Peak threads | Peak RSS MB |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['concurrent']} | {r['ok']} | {r['unavailable']} | {r['failed']} | {r['p50_s']} | {r['p95_s']} | {r['max_s']} | "
                     f"{r['first_event_p95_s']} | {r['first_stage_p95_s']} | {r['first_stage_p95_not_queued_s']} | {r['waited_in_line']} | "
                     f"{r['plans_per_min']} | {r['peak_threads']} | {r['peak_rss_mb'] or 'n/a'} |")
    (DOCS / f"{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", DOCS / f"{tag}.md")


if __name__ == "__main__":
    main()
