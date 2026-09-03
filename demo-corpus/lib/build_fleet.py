"""Large operational data set for the engineering drive: installed-base history.

Everything here is derived from a single deterministic event model built from
`installed_base.sites` in the bible with a fixed seed. Because the model is the
only source, every aggregate question ("how many corrective interventions at
DK-014 in 2025", "which site had the most incidents", "what was fleet
availability in March 2026") has a COMPUTABLE correct answer, which is emitted
as `fleet_answer_key.json` next to the corpus.

That answer key is the point: it turns the corpus into a scoring harness for
answer generation, not just more documents to retrieve.
"""
from __future__ import annotations

import datetime as dt
import random
from collections import Counter, defaultdict

from .common import Bible, Doc, bullets, h1, h2, kv, md, para, table

DRIVE = "drive_engineering"

WINDOW_START = dt.date(2024, 8, 1)
WINDOW_END = dt.date(2026, 7, 31)

# Rates per site-month. Tuned so the engineering drive lands near a thousand
# documents in total.
CORRECTIVE_RATE = 0.33
REMOTE_RATE = 0.70
PREVENTIVE_INTERVAL_DAYS = 182

FAULTS = [
    ("coolant pump", "Coolant pump bearing noise and reduced flow", "VS2-CLP-001", False, 6, 14),
    ("cooling fan", "Condenser fan module stopped, thermal derating active", "VS2-FAN-004", False, 4, 9),
    ("gas detection", "Gas detection sensor drift, false alarm", "VS2-GSN-002", False, 3, 7),
    ("contactor", "DC contactor failed to close on start command", "VS2-CNT-011", False, 5, 12),
    ("module management", "Module management board reports implausible cell voltage",
     "VS2-BMS-003", False, 4, 10),
    ("communication", "Station bus link intermittent, GOOSE messages lost", "GC3-SFP-001", True, 1, 4),
    ("door seal", "Enclosure door seal degraded, ingress warning", "VS2-SEAL-01", False, 2, 5),
    ("controller", "Controller watchdog reset after SNTP loss", "-", True, 1, 3),
    ("customer supply", "Auxiliary supply interruption on the customer side", "-", True, 1, 6),
    ("firmware", "Firmware configuration mismatch after site change", "-", True, 1, 4),
    ("filter", "Coolant filter clogged, differential pressure high", "VS2-FLT-007", False, 2, 5),
    ("power supply", "Redundant 24 V supply unit degraded", "GC3-PSU-001", False, 3, 8),
]

REMOTE_TOPICS = [
    "Alarm acknowledged after transient grid frequency excursion",
    "Set-point mismatch corrected in the controller configuration",
    "State-of-charge estimator recalibrated remotely",
    "Communication link restored after customer firewall change",
    "Scheduled availability report regenerated at customer request",
    "Thermal derating investigated; ambient temperature above design point",
    "Event log exported for the customer's incident review",
    "Reactive power set-point adjusted after grid operator instruction",
    "Time synchronisation source changed to the customer NTP server",
    "Spurious door contact alarm cleared after inspection by site staff",
]

TECHNICIANS = {
    "DK": ["Lars Nygaard", "Mette Sørensen", "Jonas Bak"],
    "SE": ["Lars Nygaard", "Mette Sørensen", "Erik Holm"],
    "NO": ["Lars Nygaard", "Erik Holm"],
    "DE": ["Stefan Ruhland", "Martin Keller", "Andrea Vogt"],
    "AT": ["Martin Keller", "Andrea Vogt"],
    "HU": ["Zsolt Bakos", "Tamás Illés"],
}

_CACHE: dict[int, dict] = {}


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------

def _months(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y + 1, 1, 1) - dt.timedelta(days=1) if m == 12 \
        else dt.date(y, m + 1, 1) - dt.timedelta(days=1)


def fleet(b: Bible) -> dict:
    """Build (once) the deterministic event history for the installed base."""
    if id(b) in _CACHE:
        return _CACHE[id(b)]

    ib = b.d["installed_base"]
    sites = []
    for raw in ib["sites"]:
        site = dict(raw)
        site["commissioned_date"] = dt.date.fromisoformat(raw["commissioned"])
        site["units"] = int(round(raw["capacity_mwh"] / 2.5))
        site["events"] = []
        sites.append(site)

    events: list[dict] = []
    counters: Counter = Counter()

    for site in sites:
        rng = random.Random("voltara-fleet-" + site["id"])
        start = max(site["commissioned_date"], WINDOW_START)
        if start > WINDOW_END:
            continue
        # a per-site quality factor makes some sites genuinely worse than others
        quality = rng.choice([0.55, 0.75, 1.0, 1.0, 1.3, 1.7])
        site["quality_factor"] = quality

        # preventive maintenance on a fixed interval from commissioning
        day = site["commissioned_date"] + dt.timedelta(days=PREVENTIVE_INTERVAL_DAYS)
        while day <= WINDOW_END:
            if day >= WINDOW_START:
                counters["preventive"] += 1
                events.append({
                    "kind": "preventive", "date": day, "site": site["id"],
                    "category": "preventive maintenance",
                    "summary": "Scheduled six-monthly preventive maintenance",
                    "technician": rng.choice(TECHNICIANS[site["country"]]),
                    "duration_h": round(rng.uniform(3.0, 6.5), 1),
                    "response_h": 0.0, "downtime_h": 0.0, "part": "-",
                    "remote": False, "planned": True,
                })
            day += dt.timedelta(days=PREVENTIVE_INTERVAL_DAYS)

        # unplanned events, month by month
        cur = start
        while cur <= WINDOW_END:
            m_end = min(_month_end(cur.year, cur.month), WINDOW_END)
            span = (m_end - cur).days + 1
            share = span / 30.0
            for kind, rate in (("corrective", CORRECTIVE_RATE), ("remote", REMOTE_RATE)):
                lam = rate * share * (quality if kind == "corrective" else 1.0)
                n = 0
                # small-count Poisson by inversion
                p, acc, k = pow(2.718281828459045, -lam), 0.0, 0
                u = rng.random()
                while k < 8:
                    acc += p
                    if u <= acc:
                        n = k
                        break
                    k += 1
                    p *= lam / k
                    n = k
                for _ in range(n):
                    when = cur + dt.timedelta(days=rng.randrange(span))
                    if kind == "remote":
                        counters["remote"] += 1
                        events.append({
                            "kind": "remote", "date": when, "site": site["id"],
                            "category": "remote support",
                            "summary": rng.choice(REMOTE_TOPICS),
                            "technician": rng.choice(TECHNICIANS[site["country"]]),
                            "duration_h": round(rng.uniform(0.3, 2.0), 1),
                            "response_h": round(rng.uniform(0.2, 3.0), 1),
                            "downtime_h": 0.0, "part": "-",
                            "remote": True, "planned": False,
                        })
                    else:
                        cat, desc, part, remote_fix, dmin, dmax = rng.choice(FAULTS)
                        counters["corrective"] += 1
                        down = 0.0 if remote_fix else round(rng.uniform(dmin, dmax), 1)
                        events.append({
                            "kind": "corrective", "date": when, "site": site["id"],
                            "category": cat, "summary": desc,
                            "technician": rng.choice(TECHNICIANS[site["country"]]),
                            "duration_h": round(rng.uniform(1.5, 9.0), 1),
                            "response_h": round(rng.uniform(0.8, 11.0), 1),
                            "downtime_h": down, "part": part,
                            "remote": remote_fix, "planned": False,
                        })
            cur = m_end + dt.timedelta(days=1)

    events.sort(key=lambda e: (e["date"], e["site"], e["kind"]))
    per_year_seq: Counter = Counter()
    by_site: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        year = e["date"].year
        per_year_seq[year] += 1
        e["id"] = "TCK-%d-%04d" % (year, per_year_seq[year])
        by_site[e["site"]].append(e)
    for site in sites:
        site["events"] = by_site.get(site["id"], [])

    # monthly fleet aggregates
    monthly: dict[str, dict] = {}
    for y, m in _months(WINDOW_START, WINDOW_END):
        key = "%04d-%02d" % (y, m)
        m_start, m_end = dt.date(y, m, 1), _month_end(y, m)
        live = [s for s in sites if s["commissioned_date"] <= m_end]
        hours = 0.0
        for s in live:
            first = max(s["commissioned_date"], m_start)
            hours += ((m_end - first).days + 1) * 24.0
        ev = [e for e in events if m_start <= e["date"] <= m_end]
        corr = [e for e in ev if e["kind"] == "corrective"]
        prev = [e for e in ev if e["kind"] == "preventive"]
        rem = [e for e in ev if e["kind"] == "remote"]
        downtime = sum(e["downtime_h"] for e in corr)
        monthly[key] = {
            "period": key,
            "sites_live": len(live),
            "capacity_mwh": round(sum(s["capacity_mwh"] for s in live), 1),
            "preventive": len(prev),
            "corrective": len(corr),
            "remote": len(rem),
            "downtime_hours": round(downtime, 1),
            "availability_pct": round(100.0 * (1.0 - downtime / hours), 2) if hours else 100.0,
            "mttr_hours": round(sum(e["duration_h"] for e in corr) / len(corr), 2) if corr else 0.0,
            "response_hours": round(sum(e["response_h"] for e in corr) / len(corr), 2) if corr else 0.0,
            "parts_used": Counter(e["part"] for e in corr if e["part"] != "-"),
            "events": ev,
        }

    data = {"sites": sites, "events": events, "monthly": monthly, "counters": counters,
            "by_site": by_site, "service_levels": ib["service_levels"]}
    _CACHE[id(b)] = data
    return data


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------

def _slug(text: str) -> str:
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() else "_")
    return "".join(out).strip("_")


def build(b: Bible) -> list[Doc]:
    f = fleet(b)
    sites, monthly = f["sites"], f["monthly"]
    docs: list[Doc] = []

    # ---------------------------------------------------------- site records
    for s in sites:
        ev = s["events"]
        corr = [e for e in ev if e["kind"] == "corrective"]
        prev = [e for e in ev if e["kind"] == "preventive"]
        rem = [e for e in ev if e["kind"] == "remote"]
        last_prev = max((e["date"] for e in prev), default=None)
        docs.append(Doc(
            DRIVE, f"fleet/sites/{s['id']}_{_slug(s['name'])}.md",
            f"Site record {s['id']} — {s['name']}", "md", tags=["/data", "/search"],
            body=md(
                h1(f"Site Record — {s['id']} {s['name']}"),
                kv([("Site ID", s["id"]), ("Site name", s["name"]),
                    ("Customer", s["customer"]), ("Country", s["country"]),
                    ("Commissioned", s["commissioned"]),
                    ("Installed capacity", f"{s['capacity_mwh']} MWh"),
                    ("VoltStack 2 units", s["units"]),
                    ("Service level", s["service_level"]),
                    ("Response target",
                     f"{f['service_levels'][s['service_level']]['response_target_hours']} hours"),
                    ("Coverage", f["service_levels"][s["service_level"]]["coverage"])]),
                h2("Configuration"),
                table(["Item", "Value"],
                      [["Containers", s["units"]],
                       ["Controller", "GC-3000"],
                       ["Controller firmware", "2.6.4"],
                       ["Nominal power", f"{s['units'] * 1.25} MW"],
                       ["Grid interface", "IEC 61850 station bus, Modbus TCP to customer SCADA"],
                       ["Remote monitoring", "Voltara Service Portal over customer VPN"]]),
                h2("Service history summary"),
                table(["Metric", "Value"],
                      [["Preventive maintenance visits", len(prev)],
                       ["Corrective interventions", len(corr)],
                       ["Remote resolutions", len(rem)],
                       ["Total service events", len(ev)],
                       ["Unplanned downtime (hours)",
                        round(sum(e["downtime_h"] for e in corr), 1)],
                       ["Last preventive maintenance",
                        last_prev.isoformat() if last_prev else "not yet due"]]),
                h2("Most frequent fault categories"),
                (table(["Category", "Occurrences"],
                       [[c, n] for c, n in Counter(e["category"] for e in corr).most_common(5)])
                 if corr else para("No corrective intervention has been recorded at this site.")),
                h2("Recent events"),
                table(["Ticket", "Date", "Type", "Category", "Summary"],
                      [[e["id"], e["date"].isoformat(), e["kind"], e["category"],
                        e["summary"]] for e in ev[-8:]]),
            )))

    # --------------------------------------------------------------- tickets
    site_by_id = {s["id"]: s for s in sites}
    for e in f["events"]:
        s = site_by_id[e["site"]]
        year = e["date"].year
        closed = e["date"] + dt.timedelta(days=0 if e["remote"] else (1 if e["duration_h"] > 6 else 0))
        docs.append(Doc(
            DRIVE, f"fleet/tickets/{year}/{e['id']}.md",
            f"{e['id']} — {s['id']} {e['category']}", "md", tags=["/search"],
            body=md(
                h1(f"Service Ticket {e['id']}"),
                kv([("Site", f"{s['id']} {s['name']}"), ("Customer", s["customer"]),
                    ("Country", s["country"]), ("Service level", s["service_level"]),
                    ("Opened", e["date"].isoformat()), ("Closed", closed.isoformat()),
                    ("Type", "planned maintenance" if e["planned"] else
                     ("remote resolution" if e["remote"] else "corrective intervention")),
                    ("Category", e["category"]), ("Technician", e["technician"])]),
                h2("Description"),
                para(e["summary"] + "."),
                h2("Resolution"),
                para(
                    "Preventive maintenance carried out according to checklist CHK-COM-03: "
                    "coolant level and quality, torque check on the DC busbars, gas and fire "
                    "detection function test, emergency stop test, and firmware version check."
                    if e["planned"] else
                    ("Resolved remotely through the Voltara Service Portal; no site visit was "
                     "required and there was no interruption to availability."
                     if e["remote"] else
                     f"Site visit performed. The affected part ({e['part']}) was replaced from "
                     f"the regional spare stock and the system was returned to service after a "
                     f"functional test.")),
                h2("Effort and impact"),
                table(["Metric", "Value"],
                      [["Response time (hours)", e["response_h"]],
                       ["Work duration (hours)", e["duration_h"]],
                       ["Unplanned downtime (hours)", e["downtime_h"]],
                       ["Part used", e["part"]],
                       ["Warranty", "yes" if e["date"] < s["commissioned_date"]
                        + dt.timedelta(days=730) else "no"]]),
            )))

    # ------------------------------------------------------- monthly reports
    keys = sorted(monthly)
    for i, key in enumerate(keys):
        mm = monthly[key]
        prev_mm = monthly[keys[i - 1]] if i else None
        worst = Counter(e["site"] for e in mm["events"] if e["kind"] == "corrective")
        docs.append(Doc(
            DRIVE, f"fleet/monthly/Fleet_Service_Report_{key}.md",
            f"Fleet service report {key}", "md", tags=["/report", "/data"],
            body=md(
                h1(f"Fleet Service Report — {key}"),
                kv([("Reporting period", key), ("Owner", b.who("p_nygaard")),
                    ("Sites in service", mm["sites_live"]),
                    ("Installed capacity", f"{mm['capacity_mwh']} MWh")]),
                h2("Service volume"),
                table(["Metric", key, "previous month"],
                      [["Preventive maintenance visits", mm["preventive"],
                        prev_mm["preventive"] if prev_mm else "-"],
                       ["Corrective interventions", mm["corrective"],
                        prev_mm["corrective"] if prev_mm else "-"],
                       ["Remote resolutions", mm["remote"],
                        prev_mm["remote"] if prev_mm else "-"],
                       ["Unplanned downtime (hours)", mm["downtime_hours"],
                        prev_mm["downtime_hours"] if prev_mm else "-"],
                       ["Fleet availability", f"{mm['availability_pct']}%",
                        f"{prev_mm['availability_pct']}%" if prev_mm else "-"],
                       ["Mean time to repair (hours)", mm["mttr_hours"],
                        prev_mm["mttr_hours"] if prev_mm else "-"],
                       ["Mean response time (hours)", mm["response_hours"],
                        prev_mm["response_hours"] if prev_mm else "-"]]),
                h2("Sites with the most corrective interventions"),
                (table(["Site", "Corrective interventions"],
                       [[sid, n] for sid, n in worst.most_common(5)])
                 if worst else para("No corrective intervention was recorded in this period.")),
                h2("Parts consumed"),
                (table(["Part", "Quantity"],
                       [[p, n] for p, n in mm["parts_used"].most_common()])
                 if mm["parts_used"] else para("No spare part was consumed in this period.")),
                h2("Commentary"),
                para(
                    f"Fleet availability in {key} was {mm['availability_pct']} percent across "
                    f"{mm['sites_live']} sites, with {mm['downtime_hours']} hours of unplanned "
                    f"downtime. {mm['corrective']} corrective interventions required a site visit "
                    f"and {mm['remote']} issues were resolved remotely."),
            )))

    # ----------------------------------------------------- quarterly reports
    quarters: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        y, m = int(key[:4]), int(key[5:])
        quarters["%d-Q%d" % (y, (m - 1) // 3 + 1)].append(key)
    for q, mkeys in sorted(quarters.items()):
        if len(mkeys) < 3:
            continue
        agg = {k: sum(monthly[mk][k] for mk in mkeys)
               for k in ("preventive", "corrective", "remote")}
        downtime = round(sum(monthly[mk]["downtime_hours"] for mk in mkeys), 1)
        avail = round(sum(monthly[mk]["availability_pct"] for mk in mkeys) / len(mkeys), 2)
        worst = Counter()
        for mk in mkeys:
            worst.update(e["site"] for e in monthly[mk]["events"] if e["kind"] == "corrective")
        docs.append(Doc(
            DRIVE, f"fleet/quarterly/Fleet_Availability_Report_{q}.md",
            f"Fleet availability report {q}", "md", tags=["/report", "/data", "/exec"],
            body=md(
                h1(f"Fleet Availability Report — {q}"),
                kv([("Quarter", q), ("Months", ", ".join(mkeys)),
                    ("Owner", b.who("p_nygaard")),
                    ("Sites at quarter end", monthly[mkeys[-1]]["sites_live"])]),
                h2("Summary"),
                table(["Metric", "Value"],
                      [["Average fleet availability", f"{avail}%"],
                       ["Unplanned downtime (hours)", downtime],
                       ["Corrective interventions", agg["corrective"]],
                       ["Preventive maintenance visits", agg["preventive"]],
                       ["Remote resolutions", agg["remote"]],
                       ["Contractual availability target", "99.0%"],
                       ["Target met", "yes" if avail >= 99.0 else "no"]]),
                h2("Monthly breakdown"),
                table(["Month", "Availability", "Corrective", "Downtime (h)"],
                      [[mk, f"{monthly[mk]['availability_pct']}%", monthly[mk]["corrective"],
                        monthly[mk]["downtime_hours"]] for mk in mkeys]),
                h2("Sites requiring attention"),
                table(["Site", "Corrective interventions in the quarter"],
                      [[sid, n] for sid, n in worst.most_common(5)]),
            )))

    # --------------------------------------------------- half-year reviews
    # Monthly and quarterly reports each rank sites within their own period, so
    # a question about a longer span can only be answered by adding them up —
    # which is exactly what a top-k retriever cannot do. These roll-ups carry
    # the whole-period ranking in one document.
    halves: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        y, m = int(key[:4]), int(key[5:])
        halves["%d-H%d" % (y, 1 if m <= 6 else 2)].append(key)
    for half, mkeys in sorted(halves.items()):
        ev = [e for mk in mkeys for e in monthly[mk]["events"]]
        corr = [e for e in ev if e["kind"] == "corrective"]
        rank = Counter(e["site"] for e in corr)
        cats = Counter(e["category"] for e in corr)
        cust = Counter(site_by_id[e["site"]]["customer"] for e in corr)
        downtime = round(sum(e["downtime_h"] for e in corr), 1)
        avail = round(sum(monthly[mk]["availability_pct"] for mk in mkeys) / len(mkeys), 2)
        partial = "" if len(mkeys) == 6 else \
            f" (partial period: only {', '.join(mkeys)} are covered)"
        docs.append(Doc(
            DRIVE, f"fleet/halfyear/Fleet_Service_Review_{half}.md",
            f"Fleet service review {half}", "md", tags=["/report", "/exec", "/data"],
            body=md(
                h1(f"Fleet Service Review — {half}{partial}"),
                kv([("Period", half), ("Months covered", ", ".join(mkeys)),
                    ("Owner", b.who("p_nygaard")),
                    ("Sites at period end", monthly[mkeys[-1]]["sites_live"]),
                    ("Installed capacity at period end",
                     f"{monthly[mkeys[-1]]['capacity_mwh']} MWh")]),
                h2("Totals for the whole period"),
                table(["Metric", half],
                      [["Corrective interventions", len(corr)],
                       ["Preventive maintenance visits",
                        len([e for e in ev if e["kind"] == "preventive"])],
                       ["Remote resolutions", len([e for e in ev if e["kind"] == "remote"])],
                       ["Unplanned downtime (hours)", downtime],
                       ["Average fleet availability", f"{avail}%"]]),
                h2(f"Sites ranked by corrective interventions in {half}"),
                para(f"This ranking covers the whole of {half}, not a single month."),
                table(["Rank", "Site", "Name", "Country", "Corrective interventions"],
                      [[i + 1, sid, site_by_id[sid]["name"], site_by_id[sid]["country"], n]
                       for i, (sid, n) in enumerate(rank.most_common(10))]),
                h2(f"Fault categories in {half}"),
                table(["Category", "Occurrences"], [[c, n] for c, n in cats.most_common(8)]),
                h2(f"Customers by corrective interventions in {half}"),
                table(["Customer", "Corrective interventions"],
                      [[c, n] for c, n in cust.most_common(6)]),
                h2("Monthly breakdown"),
                table(["Month", "Corrective", "Preventive", "Remote", "Availability"],
                      [[mk, monthly[mk]["corrective"], monthly[mk]["preventive"],
                        monthly[mk]["remote"], f"{monthly[mk]['availability_pct']}%"]
                       for mk in mkeys]),
            )))

    # --------------------------------------------------- annual inspections
    for s in sites:
        if s["commissioned_date"] > dt.date(2025, 6, 30):
            continue
        ev25 = [e for e in s["events"] if e["date"].year == 2025]
        corr25 = [e for e in ev25 if e["kind"] == "corrective"]
        docs.append(Doc(
            DRIVE, f"fleet/inspections/Annual_Inspection_2025_{s['id']}.md",
            f"Annual inspection 2025 — {s['id']} {s['name']}", "md", tags=["/compliance"],
            body=md(
                h1(f"Annual Inspection Report 2025 — {s['id']} {s['name']}"),
                kv([("Site", f"{s['id']} {s['name']}"), ("Customer", s["customer"]),
                    ("Inspection date", f"2025-{(hash(s['id']) % 9) + 3:02d}-"
                                        f"{(hash(s['name']) % 26) + 1:02d}"),
                    ("Inspector", "Julia Brandt, Head of Compliance & Quality"),
                    ("Result", "passed" if len(corr25) < 4 else "passed with observations")]),
                h2("Scope"),
                para("""
                Annual safety and condition inspection: electrical safety, earthing, thermal
                imaging of the DC connections, fire and gas detection systems, emergency stop
                chain, enclosure integrity and documentation completeness.
                """),
                h2("Findings"),
                bullets([
                    "Electrical safety and earthing: compliant, earthing resistance below 1 ohm.",
                    "Thermal imaging: no hot spot above the 15 K threshold on DC connections.",
                    "Fire and gas detection: function test passed on all detectors.",
                    "Emergency stop: verified from the container door and the control room.",
                ] + ([f"Observation: {len(corr25)} corrective interventions were recorded during "
                      f"the year, above the fleet average; the fault pattern is dominated by "
                      f"{Counter(e['category'] for e in corr25).most_common(1)[0][0]}."]
                     if len(corr25) >= 4 else [])),
                h2("Service activity in 2025"),
                table(["Metric", "Value"],
                      [["Preventive maintenance visits",
                        len([e for e in ev25 if e["kind"] == "preventive"])],
                       ["Corrective interventions", len(corr25)],
                       ["Remote resolutions", len([e for e in ev25 if e["kind"] == "remote"])],
                       ["Unplanned downtime (hours)",
                        round(sum(e["downtime_h"] for e in corr25), 1)]]),
            )))

    # ------------------------------------------------ commissioning protocols
    for s in sites:
        docs.append(Doc(
            DRIVE, f"fleet/commissioning/Commissioning_Protocol_{s['id']}.md",
            f"Commissioning protocol — {s['id']} {s['name']}", "md", tags=["/compliance"],
            body=md(
                h1(f"Commissioning Protocol — {s['id']} {s['name']}"),
                kv([("Site", f"{s['id']} {s['name']}"), ("Customer", s["customer"]),
                    ("Country", s["country"]),
                    ("Provisional acceptance", s["commissioned"]),
                    ("Installed capacity", f"{s['capacity_mwh']} MWh"),
                    ("Containers", s["units"]),
                    ("Procedure", "SAT-ESB-01 revision B"),
                    ("Warranty start", s["commissioned"]),
                    ("Warranty end (24 months)",
                     (s["commissioned_date"] + dt.timedelta(days=730)).isoformat())]),
                h2("Test results"),
                table(["Test", "Requirement", "Result"],
                      [["Insulation and continuity", "per procedure", "pass"],
                       ["Auxiliary systems", "cooling, fire, gas, emergency stop", "pass"],
                       ["Communication", "IEC 61850 point-to-point with customer SCADA", "pass"],
                       ["Static functional test", "charge, discharge, idle in 10% steps", "pass"],
                       ["Dynamic test", "frequency response and ride-through", "pass"],
                       ["Round-trip efficiency", "at least 88.0%",
                        "%.1f%%" % (88.4 + (hash(s["id"]) % 18) / 10.0)],
                       ["72-hour availability run", "at least 99.0%",
                        "%.1f%%" % (99.1 + (hash(s["name"]) % 8) / 10.0)],
                       ["Open defects at handover", "no category A or B defect", "none"]]),
                h2("Handover"),
                para("""
                The as-built documentation pack and the operator training record were handed over
                and countersigned by the customer representative. The site was registered in the
                Voltara Service Portal and remote monitoring was enabled.
                """),
            )))

    # --------------------------------------------------------- fleet exports
    lines = ["site_id,name,customer,country,commissioned,capacity_mwh,units,service_level,"
             "preventive,corrective,remote,downtime_hours"]
    for s in sites:
        ev = s["events"]
        corr = [e for e in ev if e["kind"] == "corrective"]
        lines.append("%s,%s,%s,%s,%s,%.1f,%d,%s,%d,%d,%d,%.1f" % (
            s["id"], s["name"], s["customer"], s["country"], s["commissioned"],
            s["capacity_mwh"], s["units"], s["service_level"],
            len([e for e in ev if e["kind"] == "preventive"]), len(corr),
            len([e for e in ev if e["kind"] == "remote"]),
            sum(e["downtime_h"] for e in corr)))
    docs.append(Doc(
        DRIVE, "fleet/Installed_Base_Register.csv", "Installed base register", "csv",
        tags=["/data"], body="\n".join(lines)))

    tick = ["ticket_id,date,site_id,country,type,category,technician,response_h,duration_h,"
            "downtime_h,part"]
    for e in f["events"]:
        tick.append("%s,%s,%s,%s,%s,%s,%s,%.1f,%.1f,%.1f,%s" % (
            e["id"], e["date"].isoformat(), e["site"], site_by_id[e["site"]]["country"],
            e["kind"], e["category"], e["technician"], e["response_h"], e["duration_h"],
            e["downtime_h"], e["part"]))
    docs.append(Doc(
        DRIVE, "fleet/Service_Ticket_Export.csv", "Service ticket export", "csv",
        tags=["/data", "difficulty:numeric-dense"], body="\n".join(tick)))

    docs.append(Doc(
        DRIVE, "fleet/Fleet_Availability_by_Month.xlsx", "Fleet availability by month", "xlsx",
        tags=["/data", "difficulty:xlsx"],
        sheets={
            "Monthly": [["Month", "Sites", "Capacity (MWh)", "Availability %", "Downtime (h)",
                         "Corrective", "Preventive", "Remote", "MTTR (h)", "Response (h)"]] +
                       [[k, monthly[k]["sites_live"], monthly[k]["capacity_mwh"],
                         monthly[k]["availability_pct"], monthly[k]["downtime_hours"],
                         monthly[k]["corrective"], monthly[k]["preventive"],
                         monthly[k]["remote"], monthly[k]["mttr_hours"],
                         monthly[k]["response_hours"]] for k in keys],
            "By site": [["Site", "Name", "Country", "Capacity (MWh)", "Service level",
                         "Corrective", "Downtime (h)"]] +
                       [[s["id"], s["name"], s["country"], s["capacity_mwh"], s["service_level"],
                         len([e for e in s["events"] if e["kind"] == "corrective"]),
                         round(sum(e["downtime_h"] for e in s["events"]
                                   if e["kind"] == "corrective"), 1)] for s in sites],
        }))

    # ------------------------------------------------------- O&M manual (PDF)
    manual: list[tuple[str, str]] = []
    for chapter, title, paras in _MANUAL:
        manual.append(("h1", f"{chapter}. {title}"))
        for pt, ptext in paras:
            manual.append((pt, ptext))
        manual.append(("pagebreak", ""))
    docs.append(Doc(
        DRIVE, "fleet/VoltStack2_Operation_and_Maintenance_Manual.pdf",
        "VoltStack 2 operation and maintenance manual", "pdf",
        tags=["/search", "difficulty:long-document"], blocks=manual))

    return docs


# --------------------------------------------------------------------------
# answer key
# --------------------------------------------------------------------------

def answer_key(b: Bible) -> dict:
    """Questions whose correct answer is computed from the fleet model."""
    f = fleet(b)
    sites, monthly, events = f["sites"], f["monthly"], f["events"]
    by_id = {s["id"]: s for s in sites}
    corr = [e for e in events if e["kind"] == "corrective"]

    def site_corr(sid, year=None):
        return [e for e in by_id[sid]["events"]
                if e["kind"] == "corrective" and (year is None or e["date"].year == year)]

    worst_2026h1 = Counter(
        e["site"] for e in corr if dt.date(2026, 1, 1) <= e["date"] <= dt.date(2026, 6, 30))
    worst_all = Counter(e["site"] for e in corr)
    cust_sites = Counter(s["customer"] for s in sites)
    dk14 = site_corr("DK-014", 2025)
    pumps = [e for e in corr if e["category"] == "coolant pump"]

    def leaders(counter):
        """All keys sharing the top count — a superlative question can have a tie."""
        top = counter.most_common(1)[0][1]
        return sorted(k for k, v in counter.items() if v == top)

    h1_leaders = leaders(worst_2026h1)
    all_leaders = leaders(worst_all)
    cust_leaders = leaders(cust_sites)

    cases = [
        {"question": "How many corrective interventions were recorded at site DK-014 in 2025?",
         "answer": str(len(dk14)), "kind": "count", "drive": "engineering"},
        {"question": "How many corrective interventions were recorded at site DE-007 in 2025?",
         "answer": str(len(site_corr("DE-007", 2025))), "kind": "count", "drive": "engineering"},
        {"question": "Which site had the most corrective interventions in the first half of 2026?",
         "answer": h1_leaders[0], "accept": h1_leaders, "kind": "lookup",
         "drive": "engineering", "aggregation": "whole-period"},
        {"question": "Which site has the most corrective interventions in total?",
         "answer": all_leaders[0], "accept": all_leaders, "kind": "lookup",
         "drive": "engineering", "aggregation": "whole-corpus"},
        {"question": "What was the fleet availability in March 2026?",
         "answer": "%s%%" % monthly["2026-03"]["availability_pct"], "kind": "figure",
         "drive": "engineering"},
        {"question": "What was the fleet availability in June 2026?",
         "answer": "%s%%" % monthly["2026-06"]["availability_pct"], "kind": "figure",
         "drive": "engineering"},
        {"question": "How many corrective interventions were there across the fleet in June 2026?",
         "answer": str(monthly["2026-06"]["corrective"]), "kind": "count", "drive": "engineering"},
        {"question": "How many sites are there in Sweden?",
         "answer": str(sum(1 for s in sites if s["country"] == "SE")), "kind": "count",
         "drive": "engineering"},
        {"question": "What is the total installed capacity of the fleet?",
         "answer": "%.1f MWh" % sum(s["capacity_mwh"] for s in sites), "kind": "figure",
         "drive": "engineering"},
        {"question": "Which customer has the most sites?",
         "answer": cust_leaders[0], "accept": cust_leaders, "kind": "lookup",
         "drive": "engineering", "aggregation": "whole-corpus"},
        {"question": "When was the site in Kalundborg commissioned?",
         "answer": by_id["DK-013"]["commissioned"], "kind": "date", "drive": "engineering"},
        {"question": "What is the service level of the Aalborg Havn site?",
         "answer": by_id["DK-008"]["service_level"], "kind": "lookup", "drive": "engineering"},
        {"question": "What is the installed capacity of the Gävle site?",
         "answer": "%.1f MWh" % by_id["SE-006"]["capacity_mwh"], "kind": "figure",
         "drive": "engineering"},
        {"question": "How many coolant pump failures has the fleet had?",
         "answer": str(len(pumps)), "kind": "count", "drive": "engineering",
         "aggregation": "whole-corpus"},
        {"question": "How many preventive maintenance visits were carried out in 2025?",
         "answer": str(len([e for e in events
                            if e["kind"] == "preventive" and e["date"].year == 2025])),
         "kind": "count", "drive": "engineering", "aggregation": "whole-period"},
        {"question": "How many sites does Voltara service in Norway?",
         "answer": str(sum(1 for s in sites if s["country"] == "NO")), "kind": "count",
         "drive": "engineering"},
        {"question": "What was the mean time to repair in May 2026?",
         "answer": "%s hours" % monthly["2026-05"]["mttr_hours"], "kind": "figure",
         "drive": "engineering"},
        {"question": "How much unplanned downtime did the fleet have in April 2026?",
         "answer": "%s hours" % monthly["2026-04"]["downtime_hours"], "kind": "figure",
         "drive": "engineering"},
    ]
    return {
        "generated_from": "lib/build_fleet.py deterministic model",
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "totals": {
            "sites": len(sites),
            "events": len(events),
            "corrective": f["counters"]["corrective"],
            "preventive": f["counters"]["preventive"],
            "remote": f["counters"]["remote"],
            "capacity_mwh": round(sum(s["capacity_mwh"] for s in sites), 1),
        },
        "cases": cases,
    }


# --------------------------------------------------------------------------
# the long manual: enough real structure that the answer is buried deep
# --------------------------------------------------------------------------

_MANUAL: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("1", "Introduction and safety", [
        ("", "This manual covers the operation and maintenance of the VoltStack 2 containerised "
             "battery energy storage system with the GC-3000 grid controller. It applies to all "
             "units delivered from serial number VS2-0100 onwards."),
        ("h2", "1.1 Qualified personnel"),
        ("", "Work inside the container may only be carried out by personnel who have completed "
             "the Voltara site safety induction and the battery-specific hazard training, and who "
             "are qualified for work on low-voltage DC installations above 120 V."),
        ("h2", "1.2 Residual hazards"),
        ("", "The DC bus remains energised after the AC supply is isolated. Wait at least fifteen "
             "minutes after opening the main DC contactor and verify the absence of voltage before "
             "touching any conductor."),
        ("", "Lithium iron phosphate cells can vent flammable gas under abuse conditions. Do not "
             "enter the container while a gas alarm is active. Ventilate for at least thirty "
             "minutes after the alarm clears before entry."),
        ("h2", "1.3 Emergency stop"),
        ("", "The emergency stop is hardwired to the container door and to the customer control "
             "room. Operating it opens the main contactor, stops the cooling pumps and places the "
             "controller in a safe state. Resetting requires a deliberate action at the panel."),
    ]),
    ("2", "System description", [
        ("", "Each container holds one 2.5 MWh battery block, the DC distribution, the thermal "
             "management system and the local control cabinet. Up to sixteen containers form one "
             "block behind a single GC-3000."),
        ("h2", "2.1 Electrical data"),
        ("", "Nominal DC voltage 750 V, range 672 to 840 V. Continuous power 1.25 MW per "
             "container, peak power 1.75 MW for thirty seconds. Round-trip efficiency 89.4 percent "
             "at 0.5 C and 25 degrees Celsius."),
        ("h2", "2.2 Thermal management"),
        ("", "Closed-loop liquid cooling with a glycol mixture. The design operating range is "
             "minus 20 to plus 50 degrees Celsius ambient. Above 45 degrees the system derates "
             "power linearly; below minus 10 degrees charge current is limited until the cells "
             "reach the minimum charge acceptance temperature."),
        ("h2", "2.3 Protection"),
        ("", "Overcurrent, overvoltage, undervoltage, overtemperature, insulation monitoring, gas "
             "detection and aerosol fire suppression. The protection path in firmware 3.0 has a "
             "verified worst-case execution time and is independent of the communication stacks."),
    ]),
    ("3", "Routine operation", [
        ("h2", "3.1 Normal operating states"),
        ("", "Idle, charging, discharging and standby. Transitions between states complete within "
             "one hundred milliseconds at block level."),
        ("h2", "3.2 State of charge management"),
        ("", "For frequency services the recommended operating window is 20 to 80 percent state of "
             "charge. Operating continuously above 90 percent or below 10 percent accelerates "
             "capacity fade and is outside the assumptions of the capacity guarantee."),
        ("h2", "3.3 Remote monitoring"),
        ("", "The site gateway establishes an outbound connection to the Voltara Service Portal. "
             "The portal never initiates a connection into the site network. Every remote command "
             "is logged with the operator identity and is visible to the customer."),
    ]),
    ("4", "Preventive maintenance", [
        ("", "Preventive maintenance is performed every six months. The interval is counted from "
             "provisional acceptance and is tracked per site in the Voltara Service Portal."),
        ("h2", "4.1 Six-monthly checklist"),
        ("", "Coolant level and quality; coolant filter differential pressure; torque check on all "
             "DC busbar connections against the marked positions; gas and fire detection function "
             "test; emergency stop test from both locations; door seal condition; firmware version "
             "check and event log export."),
        ("h2", "4.2 Annual activities"),
        ("", "In addition to the six-monthly checklist: thermal imaging of the DC connections "
             "under load, earthing resistance measurement, capacity verification cycle, and "
             "recalibration of the gas detection sensors."),
        ("h2", "4.3 Coolant"),
        ("", "The coolant is replaced every four years or when the quality test fails. The filter "
             "cartridge is replaced at every six-monthly visit or earlier if the differential "
             "pressure exceeds 0.8 bar."),
        ("h2", "4.4 Consumables and wear parts"),
        ("", "Coolant pump assembly: expected service life 25,000 operating hours. Condenser fan "
             "module: 30,000 hours. Gas detection sensor: replace every five years regardless of "
             "condition. Door seal kit: inspect at each visit, replace on any visible degradation."),
    ]),
    ("5", "Fault finding", [
        ("h2", "5.1 Coolant pump alarm"),
        ("", "A coolant pump alarm is raised when the flow falls below 60 percent of nominal for "
             "more than sixty seconds. Check the filter differential pressure first; a clogged "
             "filter produces the same symptom as a failing pump. If the filter is clean, listen "
             "for bearing noise and replace the pump assembly, part VS2-CLP-001."),
        ("h2", "5.2 Gas detection false alarm"),
        ("", "Sensor drift is the most common cause of a false gas alarm, particularly in the "
             "second half of the sensor life. Verify with a second instrument before declaring a "
             "false alarm. Sensors from a common production batch drift together, so a false alarm "
             "at one site is a reason to check the batch across the fleet."),
        ("h2", "5.3 Contactor does not close"),
        ("", "Check the interlock chain first: door contact, emergency stop, insulation monitor "
             "and the controller enable signal. A contactor that does not close is more often an "
             "interlock condition than a failed contactor."),
        ("h2", "5.4 Communication link intermittent"),
        ("", "Intermittent GOOSE loss is usually a fibre or transceiver problem rather than a "
             "controller fault. Replace the transceiver, part GC3-SFP-001, before replacing the "
             "controller board."),
        ("h2", "5.5 Watchdog reset after time synchronisation loss"),
        ("", "Firmware versions before 2.6.4 could reset the watchdog if the time source was "
             "unreachable for more than forty-eight hours. Upgrade to 2.6.4 or later; the "
             "behaviour is corrected."),
    ]),
    ("6", "Spare parts and logistics", [
        ("", "Regional spare stock is held at the München hub and the København depot. Minimum "
             "stock levels are set from the fleet failure rate and are reviewed quarterly."),
        ("h2", "6.1 Critical spares"),
        ("", "Coolant pump assembly VS2-CLP-001, minimum stock six, lead time eight weeks. "
             "Controller main board GC3-CPU-002, minimum stock three, lead time twelve weeks. "
             "Module management board VS2-BMS-003, minimum stock eight, lead time ten weeks."),
        ("h2", "6.2 Warranty handling"),
        ("", "The standard warranty is twenty-four months from provisional acceptance. Parts "
             "replaced under warranty must be returned to the München hub with the ticket number "
             "quoted on the return note."),
    ]),
    ("7", "Decommissioning", [
        ("", "At end of life the battery block is discharged to the transport state of charge of "
             "30 percent, the DC bus is isolated and the container is prepared for transport under "
             "UN 38.3. Cells are returned to the supplier under the take-back agreement."),
        ("h2", "7.1 Second life"),
        ("", "Modules that retain more than 70 percent of their nominal capacity may be assessed "
             "for second-life applications. This assessment is not part of the standard service "
             "scope and is quoted separately."),
    ]),
]
