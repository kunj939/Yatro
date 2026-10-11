"""Phase 2 rail baseline (ADR 0003): what Tier 1 and Tier 2 save on real RailRadar traffic, within a hard call cap.

    python scripts/rail_baseline.py            # 3 fixed routes, cold then warm; real API calls (max 30)

Only the rail search runs (the same provider stack as a plan: RailRadar, with IRCTC1's station search and IRCTC1 as
the next source). Google is never called: Phase 2 doesn't change Google usage, and a full cold plan alone makes 26-49
calls (Phase 0), which wouldn't fit the cap. Every external call is counted before it is sent; the 31st is refused.

Cold: a fresh database, so both tiers and the 12-hour response cache are empty. Warm: the same database with the
response cache cleared, so the saving shown is the tiers' alone (a repeat search more than 12 hours later).
Writes docs/perf/rail-baseline-<date>.md and .json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parent / "docs" / "perf"
CAP = 30
FREE_TIER = 1000  # RailRadar requests/month (docs/COST.md)


def next_saturday(weeks_ahead: int = 2) -> date:
    d = date.today() + timedelta(days=3)
    return d + timedelta(days=(5 - d.weekday()) % 7 + 7 * (weeks_ahead - 1))


class CapReached(Exception):
    pass


class NoPlaces:
    """No map lookups: station positions come from the built-in station table, else the city centre."""
    def resolve(self, *a, **k):
        raise RuntimeError("map lookups are off in the rail baseline")


def main() -> None:
    db = Path(tempfile.mkdtemp(prefix="yatro-rail-baseline-")) / "rail.db"
    os.environ.update({"DATABASE_URL": f"sqlite:///{db}", "TELEMETRY_ENABLED": "true", "LOG_LEVEL": "WARNING",
                       "RAIL_REFRESH_DAILY_CAP": "0"})
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    import app.models  # noqa: F401
    from app.core import telemetry as T
    from app.core.config import get_settings
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.models.catalog import ApiCache
    from app.providers.rail_chain import RailChain
    from app.providers.types import PlaceData
    Base.metadata.create_all(engine)

    s = get_settings()
    if not s.railradar_api_key:
        sys.exit("RAILRADAR_API_KEY is not set")

    sent = {"n": 0}
    real = T.TracingTransport.handle_request

    def guarded(self, request):
        if sent["n"] >= CAP:
            raise CapReached(f"call cap of {CAP} reached; refused {request.url.host}{request.url.path}")
        sent["n"] += 1
        return real(self, request)
    T.TracingTransport.handle_request = guarded

    places = NoPlaces()
    irctc = None
    if s.irctc_rapidapi_key:
        from app.providers.indian_rail import IndianRailProvider
        irctc = IndianRailProvider(places, s.irctc_rapidapi_key, s.irctc_rapidapi_host)
    from app.providers.railradar import RailRadarProvider
    chain = RailChain([RailRadarProvider(places, s.railradar_api_key, station_search=irctc._station_rows if irctc else None),
                       *([irctc] if irctc else [])])

    def city(name, lat, lng):
        return PlaceData("baseline", name.lower(), name, "city", lat, lng)
    gurugram, agra = city("Gurugram", 28.4595, 77.0266), city("Agra", 27.1767, 78.0081)
    mussoorie, delhi = city("Mussoorie", 30.4598, 78.0644), city("Delhi", 28.6139, 77.2090)
    jaipur = city("Jaipur", 26.9124, 75.7873)
    day = next_saturday()
    routes = [("Gurugram → Agra (day trip)", gurugram, agra, day, day),
              ("Gurugram → Mussoorie (2 days)", gurugram, mussoorie, day, day + timedelta(days=1)),
              ("Delhi → Jaipur (day trip)", delhi, jaipur, day, day)]

    results: list[dict] = []
    stopped = None
    for pass_name in ("cold", "warm"):
        if pass_name == "warm":
            with SessionLocal() as d:  # the 12-hour response cache would hide the tiers; clear it
                d.query(ApiCache).delete()
                d.commit()
        for label, o, dst, dep, ret in routes:
            if stopped:
                break
            with T.plan_trace(f"rail-baseline {pass_name} {label}") as tr:
                try:
                    opts = chain.intercity(o, dst, dep, ret)
                    error = None
                except CapReached as exc:
                    opts, error = [], str(exc)
            summ = T.RECENT[-1]
            tiers: dict[str, int] = {}
            for c in tr.cache:
                if str(c.get("provider", "")).startswith("rail-tiers:"):
                    for k, v in c.items():
                        if k.startswith("tier"):
                            tiers[k] = tiers.get(k, 0) + int(v)
            trains = sorted({str(t.meta.get("number")) for t in opts if t.meta.get("number")})
            row = {"pass": pass_name, "route": label, "calls": summ["http_calls"], "errors": summ["http_errors"],
                   "endpoints": {k: v["calls"] for k, v in summ["endpoints"].items()},
                   "rail_units": summ["rail_quota_units"], "pairs": summ["rail_pairs"]["outcomes"],
                   "pair_detail": [f"{p['pair']} {p['outcome']}" for p in summ["rail_pairs"]["detail"]],
                   "tiers": tiers, "options": len(opts), "trains": trains,
                   "sources": sorted({str(t.meta.get("source_kind")) for t in opts}), "error": error}
            results.append(row)
            print(f"[{pass_name}] {label:<32} calls={row['calls']:<3} units={row['rail_units']:<3} "
                  f"pairs={row['pairs']} tiers={tiers} trains={len(trains)} total_sent={sent['n']}"
                  + (f"  STOPPED: {error}" if error else ""), flush=True)
            if error:
                stopped = error
    write_report(results, sent["n"], stopped, day)


def write_report(results: list[dict], sent: int, stopped: str | None, day: date) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    (DOCS / f"rail-baseline-{stamp}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    cold = {r["route"]: r for r in results if r["pass"] == "cold"}
    warm = {r["route"]: r for r in results if r["pass"] == "warm"}
    lines = [f"# Rail baseline (Phase 2) — {stamp}", "",
             f"Generated by `scripts/rail_baseline.py`. Travel date {day} (a Saturday). Rail search only, real APIs, "
             f"hard cap {CAP} external calls (sent: **{sent}**{'; stopped early: ' + stopped if stopped else ''}). "
             "Warm = same database with the 12-hour response cache cleared, so the saving is the tiers' alone.", "",
             "| Route | Cold calls | Warm calls | Saved | by Tier 2 (saved timetable hits) | by Tier 1 (empty pairs skipped) | by Tier 1 (working pair first) | Same trains warm? |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    tot = {"c": 0, "w": 0, "t2": 0, "skip": 0, "order": 0}
    for route, c in cold.items():
        w = warm.get(route)
        if not w:
            lines.append(f"| {route} | {c['calls']} | not run | | | | | |")
            continue
        saved = c["calls"] - w["calls"]
        t2 = w["tiers"].get("tier2_hit", 0)
        skip = w["tiers"].get("tier1_skipped_empty", 0)
        order = max(0, saved - t2 - skip)  # pairs never asked about because the known working pair went first
        same = "yes" if c["trains"] == w["trains"] else f"no ({len(c['trains'])} vs {len(w['trains'])})"
        lines.append(f"| {route} | {c['calls']} | {w['calls']} | {saved} | {t2} | {skip} | {order} | {same} |")
        for k, v in (("c", c["calls"]), ("w", w["calls"]), ("t2", t2), ("skip", skip), ("order", order)):
            tot[k] += v
    if tot["c"]:
        lines.append(f"| **Total** | **{tot['c']}** | **{tot['w']}** | **{tot['c'] - tot['w']}** | {tot['t2']} | "
                     f"{tot['skip']} | {tot['order']} | |")
    lines += ["", "## Detail", "", "| Pass | Route | Calls by endpoint | Station pairs | Tier counters | Trains | Sources |",
              "|---|---|---|---|---|---:|---|"]
    for r in results:
        eps = "<br>".join(f"{k}: {v}" for k, v in r["endpoints"].items()) or "none"
        lines.append(f"| {r['pass']} | {r['route']} | {eps} | {'<br>'.join(r['pair_detail']) or 'none'} | "
                     f"{', '.join(f'{k}={v}' for k, v in sorted(r['tiers'].items())) or 'none'} | {len(r['trains'])} | "
                     f"{', '.join(r['sources'])} |")
    out = DOCS / f"rail-baseline-{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
