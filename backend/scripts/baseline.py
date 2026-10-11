"""Phase 0 baseline (ADR 0001): plan a fixed set of route types and report what each one costs.

    python scripts/baseline.py --mode offline            # simulated providers: structure, stages, DB; free
    python scripts/baseline.py --mode live --passes 2    # real APIs, cold then warm cache; costs money and quota

Runs in-process against its own database (never the app's), so "cold" really is an empty cache. Writes
docs/perf/baseline-<mode>-<date>.md and .json. Only the fixed test routes below are ever planned.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parent / "docs" / "perf"


def next_saturday(weeks_ahead: int = 1) -> date:
    d = date.today() + timedelta(days=3)
    return d + timedelta(days=(5 - d.weekday()) % 7 + 7 * (weeks_ahead - 1))


def routes(live: bool) -> list[dict]:
    d = next_saturday(2)

    def trip(origin, dest, days, budget, modes, stay="budget", start="06:00", end="22:00", travellers=2):
        return {"origin": {"name": origin}, "destination": {"name": dest}, "start_date": d.isoformat(),
                "end_date": (d + timedelta(days=days - 1)).isoformat(), "start_time": start, "end_time": end,
                "budget": budget, "travellers": travellers, "traveller_type": "friends", "transport_modes": modes,
                "food": "both", "trip_styles": ["balanced"], "stay": stay if days > 1 else "no_stay"}
    out = [
        {"key": "local", "label": "Local (same city)", "trip": trip("Hawa Mahal, Jaipur" if live else "Jaipur", "Jaipur", 1, 3000, ["cab", "metro"], start="09:00", end="20:00")},
        {"key": "rail_short", "label": "Short rail (Jaipur → New Delhi)", "trip": trip("Jaipur", "New Delhi", 1, 8000, ["train", "bus", "metro"], start="05:00", end="23:30")},
        {"key": "rail_change", "label": "Rail, likely a change (Udaipur → Rishikesh)", "trip": trip("Udaipur", "Rishikesh", 3, 25000, ["train", "bus"])},
        {"key": "railhead", "label": "Hill station via railhead (New Delhi → Mussoorie)", "trip": trip("New Delhi", "Mussoorie", 2, 15000, ["train", "bus", "cab"])},
        {"key": "flight_dom", "label": "Domestic flight (Mumbai → Goa)", "trip": trip("Mumbai", "Goa", 3, 40000, ["flight", "train"], stay="tier2")},
        {"key": "international", "label": "International (New Delhi → Istanbul)" if live else "International (Mumbai → London)",
         "trip": trip("New Delhi", "Istanbul", 4, 60000, ["flight"], stay="tier2") if live else trip("Mumbai", "London", 4, 3000, ["flight"], stay="tier2")},
    ]
    return out


def setup_env(mode: str) -> Path:
    db = Path(tempfile.mkdtemp(prefix=f"yatro-baseline-{mode}-")) / "baseline.db"
    os.environ.update({"DATABASE_URL": f"sqlite:///{db}", "RATE_LIMIT_ENABLED": "false", "FREE_TRIP_PLANS": "500",
                       "TELEMETRY_ENABLED": "true", "SMTP_HOST": "", "SMS_PROVIDER": "console", "OTP_RESEND_SECONDS": "0",
                       "ENVIRONMENT": "development", "LOG_LEVEL": "WARNING"})
    if mode == "offline":
        os.environ.update({"DEMO_MODE": "true", "LLM_PROVIDER": "template"})
    return db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--passes", type=int, default=1, help="2 = cold then warm cache")
    args = ap.parse_args()
    live = args.mode == "live"
    setup_env(args.mode)
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    from fastapi.testclient import TestClient

    import app.models  # noqa: F401
    from app.core import telemetry as T
    from app.db.base import Base
    from app.db.session import engine
    from app.main import app
    Base.metadata.create_all(engine)
    if not live:
        from app.api.deps import providers_dep
        from tests.sim_providers import sim_providers
        p = sim_providers("normal")
        app.dependency_overrides[providers_dep] = lambda: p

    results: list[dict] = []
    with TestClient(app) as c:
        email, phone = "baseline@example.com", "+919800099999"
        codes = {ch: c.post("/api/auth/send-code", json={"channel": ch, "target": t}).json()["dev_code"]
                 for ch, t in (("email", email), ("phone", phone))}
        tok = c.post("/api/auth/register", json={"name": "Baseline", "email": email, "phone": phone, "password": "baseline123",
                                                 "password_confirm": "baseline123", "email_code": codes["email"],
                                                 "phone_code": codes["phone"]}).json()["access_token"]
        auth = {"Authorization": f"Bearer {tok}"}
        for n in range(1, args.passes + 1):
            pass_name = "cold" if n == 1 else "warm"
            replan_trip = None
            for r in routes(live):
                trip = c.post("/api/trips", json=r["trip"], headers=auth)
                if trip.status_code != 201:
                    results.append({"pass": pass_name, "route": r["key"], "label": r["label"], "error": trip.text[:200]})
                    continue
                tid = trip.json()["id"]
                t0 = time.perf_counter()
                resp = c.post(f"/api/trips/{tid}/plan", headers=auth)
                wall = round((time.perf_counter() - t0) * 1000)
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                status = body.get("status") or (body.get("feasibility") or {}).get("kind") or resp.status_code
                results.append({"pass": pass_name, "route": r["key"], "label": r["label"], "http_status": resp.status_code,
                                "outcome": _outcome(body, resp.status_code),
                                "wall_ms": wall, "trace": T.RECENT[-1]})
                print(f"[{pass_name}] {r['key']:<14} {resp.status_code} {wall/1000:6.1f}s  http={T.RECENT[-1]['http_calls']}"
                      f"  pairs={T.RECENT[-1]['rail_pairs']['outcomes']}" + ("" if resp.status_code == 200 else "  " + resp.text[:160]), flush=True)
                if r["key"] == "rail_short":
                    replan_trip = tid
            if replan_trip:
                t0 = time.perf_counter()
                resp = c.post(f"/api/trips/{replan_trip}/replan", headers=auth, json={"message": "start at 9am"})
                wall = round((time.perf_counter() - t0) * 1000)
                results.append({"pass": pass_name, "route": "replan", "label": "Replan of short rail (\"start at 9am\")",
                                "http_status": resp.status_code, "outcome": "ready" if resp.status_code == 200 else str(resp.status_code),
                                "wall_ms": wall, "trace": T.RECENT[-1]})
                print(f"[{pass_name}] replan         {resp.status_code} {wall/1000:6.1f}s", flush=True)
    write_report(args.mode, results)


def _outcome(body: dict, code: int) -> str:
    """ready / infeasible / error. A ready trip also carries a feasibility record, with feasible true."""
    if code != 200:
        return f"http {code}"
    fe = body.get("feasibility") or {}
    if fe.get("feasible") is False:
        return "infeasible (" + str(fe.get("kind") or "?") + ")"
    return str(body.get("status") or "?")


def write_report(mode: str, results: list[dict]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    (DOCS / f"baseline-{mode}-{stamp}.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    ok = [r for r in results if "trace" in r]
    lines = [f"# Baseline ({mode}) — {stamp}", "",
             "Generated by `scripts/baseline.py` (ADR 0001). Costs are list-price estimates per call "
             "(core/telemetry.py `DEFAULT_PRICES`); rail APIs are counted in quota units.", "",
             "## Per plan", "",
             "| Pass | Route | Outcome | Wall s | 1st event ms | HTTP calls (err) | Google+search $ | Rail units | Pairs: trains / empty / failed | Cache hit/miss | LLM tokens ($) | DB queries |",
             "|---|---|---|---:|---:|---:|---:|---:|---|---|---|---:|"]
    for r in results:
        if "trace" not in r:
            lines.append(f"| {r['pass']} | {r['label']} | error: {r['error']} | | | | | | | | | |")
            continue
        t = r["trace"]
        po = t["rail_pairs"]["outcomes"]
        failed = sum(v for k, v in po.items() if k not in ("trains", "empty"))
        lines.append(f"| {r['pass']} | {r['label']} | {r['outcome']} | {r['wall_ms']/1000:.1f} | {t['first_event_ms'] or 0:.0f} | "
                     f"{t['http_calls']} ({t['http_errors']}) | {t['usd_google_and_search']:.3f} | {t['rail_quota_units']} | "
                     f"{po.get('trains', 0)} / {po.get('empty', 0)} / {failed} | {t['cache']['hits']}/{t['cache']['misses']} | "
                     f"{t['llm']['tokens']} (${t['llm']['usd']:.4f}) | {t['db']['queries']} |")
    # endpoints across all plans
    agg: dict[str, dict] = {}
    for r in ok:
        for k, v in r["trace"]["endpoints"].items():
            a = agg.setdefault(k, {"calls": 0, "errors": 0, "usd": 0.0, "max_ms": 0.0, "p50": []})
            a["calls"] += v["calls"]; a["errors"] += v["errors"]; a["usd"] += v["usd"]
            a["max_ms"] = max(a["max_ms"], v["max_ms"]); a["p50"].append(v["p50_ms"])
    lines += ["", "## External endpoints (all plans)", "", "| Endpoint | Calls | Errors | p50 ms (median of plans) | Max ms | Est. $ |", "|---|---:|---:|---:|---:|---:|"]
    for k, a in sorted(agg.items(), key=lambda kv: -kv[1]["calls"]):
        lines.append(f"| `{k}` | {a['calls']} | {a['errors']} | {statistics.median(a['p50']):.0f} | {a['max_ms']:.0f} | {a['usd']:.3f} |")
    # stages
    st: dict[str, list[float]] = {}
    for r in ok:
        for s in r["trace"]["stages"]:
            st.setdefault(s["name"], []).append(s["end_ms"] - s["start_ms"])
    lines += ["", "## Stage time (ms, across plans)", "", "| Stage | Median | Max |", "|---|---:|---:|"]
    for k, v in st.items():
        lines.append(f"| {k} | {statistics.median(v):.0f} | {max(v):.0f} |")
    # wasted rail calls
    tried = sum(r["trace"]["rail_pairs"]["tried"] for r in ok)
    empty = sum(r["trace"]["rail_pairs"]["outcomes"].get("empty", 0) for r in ok)
    lines += ["", "## Rail station pairs", "", f"- Pairs tried: **{tried}**; returned no trains: **{empty}** "
              f"({(100 * empty / tried) if tried else 0:.0f}%). These are the calls Phase 2 Tier 1 (known-empty pairs) would save.", ""]
    walls = [r["wall_ms"] for r in ok]
    if walls:
        lines += ["## Summary", "", f"- Plans: {len(ok)}; wall time median {statistics.median(walls)/1000:.1f} s, max {max(walls)/1000:.1f} s",
                  f"- Time to first progress event: max {max((r['trace']['first_event_ms'] or 0) for r in ok):.0f} ms", ""]
    out = DOCS / f"baseline-{mode}-{stamp}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
