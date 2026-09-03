#!/usr/bin/env python3
"""ViVeSec AIBox terhelés-mérés — hány egyidejű kérést / usert bír a box?

A kérdés: alkalmas-e a box egyszerre több kérés kiszolgálására, és ha igen,
hol a határ userszámban? A választ NEM lehet kitalálni, mérni kell, mert a
szűk keresztmetszet egyetlen dolog: a generálás egyetlen GPU-n, egyetlen
betöltött modellen (qwen3.6:35b) fut. Az adapter HTTP-szerver (ThreadingHTTPServer)
párhuzamosan FOGADJA a kéréseket, a retrieval (bge-m3 embed + vektorkeresés)
is párhuzamosítható, de a tokengenerálás az Ollamán sorba/kötegbe áll a GPU-n.

Ez a szkript ezt méri feketedobozként, pontosan úgy, ahogy egy user érzékeli:
egyszerre C kérést indít az adapter /api/v1/ui/query végpontjára, és rögzíti,
meddig tart, amíg MINDENKI választ kap, hány hiba/timeout van, és mennyivel
lassul egy-egy kérés a párhuzamosság növelésével.

    python3 98_concurrency_bench.py --adapter http://127.0.0.1:8088 \
        --drive /storage/drives/engineering/ --levels 1,2,3,4,6,8 --out ~/conc.json

    # demó box: az adapter a :80-on figyel
    python3 98_concurrency_bench.py --adapter http://127.0.0.1:80 --levels 1,2,4 ...

A KULCS-DIAGNÓZIS a "serialization" oszlop: ha a p50(C) ~ C * p50(1), akkor a
generálás teljesen sorosít (a GPU egyszerre egy kérésen dolgozik, a többi vár);
ha lassabban nő, mint C, akkor van valódi párhuzamos kötegelés (OLLAMA_NUM_PARALLEL).

Stdlib only, Python 3.6+, semmit nem telepít a boxra.
"""
import argparse
import base64
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

SCHEMA = 1

# Rövid, tényszerű, korpuszfüggetlen kérdések: eltérő retrieval, de mindegyik
# rövid választ vár, hogy egy kérés összemérhető GPU-munkaegység legyen. A
# forgatás a "két azonos prompt egymás után gyorsabb" cache-hatást is tompítja.
DEFAULT_QUESTIONS = [
    "Summarize the key point in one sentence.",
    "What is the most important date mentioned?",
    "Name the main parties or people involved.",
    "What is the headline figure or status?",
    "List the top three items in one line each.",
    "What decision or next step is described?",
]


def ask(adapter, drive, user, question, timeout):
    """Egy kérés -> (ok, latency_s, http_status, backend, err)."""
    payload = json.dumps({"query": question}).encode()
    req = urllib.request.Request(
        adapter + "/api/v1/ui/query", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode(),
                 "VVS-User": user})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
        return (True, time.time() - t0, 200, body.get("backend"), None)
    except urllib.error.HTTPError as e:
        return (False, time.time() - t0, e.code, None, "http_%d" % e.code)
    except Exception as e:  # timeout, reset, stb.
        kind = "timeout" if isinstance(e, (TimeoutError,)) or "timed out" in str(e).lower() else "error"
        return (False, time.time() - t0, 0, None, "%s:%s" % (kind, e))


def run_level(adapter, drive, level, rounds, timeout, questions, qidx):
    """C egyidejű kérés `rounds`-szor; barrier-rel egyszerre indítjuk őket."""
    results = []          # (ok, lat, status, backend, err)
    round_walls = []      # amíg MINDENKI kész lett az adott körben
    for _ in range(rounds):
        barrier = threading.Barrier(level)
        bag = [None] * level
        starts = [0.0] * level
        ends = [0.0] * level

        def worker(i):
            q = questions[qidx[0] % len(questions)]
            qidx[0] += 1
            barrier.wait()               # mind a C szál egyszerre lő
            starts[i] = time.time()
            bag[i] = ask(adapter, drive, "conc.u%02d" % i, q, timeout)
            ends[i] = time.time()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(level)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        results.extend(bag)
        round_walls.append(max(ends) - min(starts))
    return results, round_walls


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--drive", default="/storage/drives/engineering/")
    ap.add_argument("--levels", default="1,2,3,4,6,8",
                    help="egyidejű kérések szintjei, vesszővel")
    ap.add_argument("--rounds", type=int, default=2,
                    help="hányszor ismételjük szintenként (átlagolás)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--sla", type=float, default=60.0,
                    help="elfogadható max válaszidő (s); eddig 'kiszolgálható' a szint")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    print("== ViVeSec AIBox párhuzamossági mérés ==")
    print("adapter=%s drive=%s levels=%s rounds=%d sla=%.0fs" % (
        args.adapter, args.drive, levels, args.rounds, args.sla))

    # Bemelegítés: a modell legyen betöltve, mielőtt időt mérünk.
    print("bemelegítés (modell betöltése)...", end=" ", flush=True)
    ok, lat, status, backend, err = ask(
        args.adapter, args.drive, "conc.warmup", DEFAULT_QUESTIONS[0], args.timeout)
    print("ok=%s %.1fs backend=%s%s" % (ok, lat, backend, "" if ok else " ERR=%s" % err))
    if not ok:
        print("A bemelegítő kérés elbukott — nem folytatom. Ellenőrizd az adapter/drive elérést.")
        sys.exit(2)

    single_p50 = None
    rows = []
    for c in levels:
        # Minden szint UGYANAZT a kérdés-sorozatot kapja -> a szintek fair módon
        # összevethetők (különben eltérő generálási hosszt mérnénk, nem terhelést).
        qidx = [0]
        print("\n-- szint C=%d (%d kör, összesen %d kérés) --" % (c, args.rounds, c * args.rounds))
        t_level = time.time()
        results, round_walls = run_level(
            args.adapter, args.drive, c, args.rounds, args.timeout, DEFAULT_QUESTIONS, qidx)
        wall = time.time() - t_level

        lats = [r[1] for r in results if r[0]]
        oks = sum(1 for r in results if r[0])
        timeouts = sum(1 for r in results if not r[0] and r[4] and r[4].startswith("timeout"))
        errors = sum(1 for r in results if not r[0]) - timeouts
        p50 = pct(lats, 50)
        p95 = pct(lats, 95)
        mx = max(lats) if lats else 0.0
        if single_p50 is None and c == 1 and p50 > 0:
            single_p50 = p50
        ser = (p50 / single_p50) if single_p50 else float("nan")
        # Aggregált áteresztés: hány kérés / perc, körönkénti faliidőből.
        mean_round_wall = statistics.mean(round_walls) if round_walls else 0.0
        thr = (c / mean_round_wall * 60.0) if mean_round_wall else 0.0
        row = {"level": c, "ok": oks, "errors": errors, "timeouts": timeouts,
               "p50_s": round(p50, 1), "p95_s": round(p95, 1), "max_s": round(mx, 1),
               "round_wall_s": round(mean_round_wall, 1),
               "throughput_per_min": round(thr, 1),
               "serialization_x": round(ser, 2) if ser == ser else None}
        rows.append(row)
        print("  ok=%d hiba=%d timeout=%d | p50=%.1fs p95=%.1fs max=%.1fs" % (
            oks, errors, timeouts, p50, p95, mx))
        print("  kör-faliidő=%.1fs  áteresztés=%.1f kérés/perc  lassulás=%sx a C=1-hez" % (
            mean_round_wall, thr, row["serialization_x"]))

    # Értelmezés: a legnagyobb szint, ahol nincs hiba/timeout ÉS a max válaszidő
    # az SLA alatt marad — ez a gyakorlati "egyidejű user" határ.
    usable = [r for r in rows if r["errors"] == 0 and r["timeouts"] == 0 and r["max_s"] <= args.sla]
    limit = max((r["level"] for r in usable), default=0)

    print("\n================ ÖSSZEGZÉS ================")
    print("%-4s %-5s %-6s %-8s %-8s %-8s %-10s %-8s" % (
        "C", "ok", "hiba", "p50s", "p95s", "maxs", "kér/perc", "lassulás"))
    for r in rows:
        print("%-4d %-5d %-6d %-8.1f %-8.1f %-8.1f %-10.1f %-8s" % (
            r["level"], r["ok"], r["errors"] + r["timeouts"],
            r["p50_s"], r["p95_s"], r["max_s"], r["throughput_per_min"],
            "%.2fx" % r["serialization_x"] if r["serialization_x"] else "-"))
    print("-------------------------------------------")
    print("SLA=%.0fs mellett gyakorlati egyidejű határ: %s user" % (
        args.sla, limit if limit else "<1 (már 1 kérés is túllépi az SLA-t)"))
    print("Megjegyzés: ha a 'lassulás' ~ C-vel arányos, a GPU sorosít (1 egyidejű")
    print("generálás); ha lassabban nő, van párhuzamos kötegelés (OLLAMA_NUM_PARALLEL).")

    if args.out:
        report = {"schema": SCHEMA, "adapter": args.adapter, "drive": args.drive,
                  "sla_s": args.sla, "rounds": args.rounds, "levels": rows,
                  "practical_concurrent_limit": limit,
                  "single_p50_s": round(single_p50, 1) if single_p50 else None,
                  "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print("\nJSON riport: %s" % args.out)


if __name__ == "__main__":
    main()
