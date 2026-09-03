"""Deterministic scaled corpus + gold-set generator for the harness.

WHY: the hand-written smoke fixtures (3 docs / 14 questions) catch regressions
but cannot discriminate engines at the CoLearn acceptance gate — recall@5 over 3
documents is trivially high and 14 questions give ~7pp granularity per question.
This script generates the FULL profile: ~50 documents across 3 multi-file drive
corpora with overlapping topics (real distractor density), an mtime spread (for
the later freshness scoring), edge-case files, and ~100 gold questions derived
from facts PLANTED during generation — so expected answers are correct by
construction, without an LLM, fully reproducible from a seed.

Drive model (matches production ACL semantics): one drive = one corpus = the
hard ACL boundary. A gold case's `allowed_file_ids` always contains WHOLE
drives (every file of each allowed drive) because on the box a user sees a
drive or does not; `relevant_file_ids` marks the specific file(s) the answer
lives in; `forbidden_file_ids` lists canary files of the other drives.

Output (committed, but regenerable):
    fixtures_full/<drive>/<doc>.txt      the corpus files (mtimes are set)
    fixtures_full/manifest.json          v2 manifest ({"drive": ...} per doc)
    gold_set/dev_full.jsonl              tuning split      (~40%)
    gold_set/holdout_full.jsonl          reporting split   (~60%)

Usage:
    python gen_corpus.py [--seed 42] [--out fixtures_full]

Only stdlib. Deterministic for a given seed + template version.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE_VERSION = 1  # bump when templates change (invalidates old gold sets)

# ---------------------------------------------------------------------------
# Seeded entity pools (synthetic; no real data)
# ---------------------------------------------------------------------------

PEOPLE = [
    "Anna Kovacs", "Peter Molnar", "Julia Szabo", "Marton Toth", "Eva Farkas",
    "Balazs Nemeth", "Ildiko Varga", "Gergely Kiss", "Nora Balogh", "Tamas Olah",
]
DEPARTMENTS = ["Finance", "Operations", "Engineering", "Sales", "HR", "Legal"]
PROJECTS = ["Northwind", "Bluebird", "Granite", "Meridian", "Lighthouse"]
VENDORS = ["Cloudline Kft", "SecureWare Zrt", "DataForge BV", "NetPulse GmbH", "OfficeHub Kft"]
QUARTERS = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026"]


def _money(rng: random.Random) -> str:
    return f"{rng.randint(12, 96) * 50} thousand EUR"


def _pct(rng: random.Random) -> str:
    return f"{rng.randint(4, 39)} percent"


def _day(rng: random.Random, year: int = 2026) -> str:
    return f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


# ---------------------------------------------------------------------------
# Document templates. Each returns (text, facts) where facts drive the gold set:
#   fact = {kind, question, relevant (paths), tier, answer_hint}
# Question phrasing mixes EN + HU (the box is multilingual by spec).
# ---------------------------------------------------------------------------


def gen_quarterly_report(rng: random.Random, quarter: str) -> tuple[str, list[dict]]:
    revenue = _money(rng)
    margin = _pct(rng)
    top_vendor = rng.choice(VENDORS)
    vendor_spend = _money(rng)
    owner = rng.choice(PEOPLE)
    text = (
        f"{quarter} Financial Report (synthetic)\n\n"
        f"Prepared by {owner}, Finance department.\n\n"
        f"Revenue: total revenue for {quarter} was {revenue}, with a gross margin of "
        f"{margin}. Operating costs stayed within the approved envelope.\n\n"
        f"Vendor spend: the largest vendor in {quarter} was {top_vendor} with a total "
        f"spend of {vendor_spend}. All invoices were settled within payment terms.\n\n"
        f"Outlook: the company expects moderate growth next quarter driven by the "
        f"{rng.choice(PROJECTS)} project pipeline and continued cost discipline. "
        f"Headcount planning is coordinated with HR and reviewed monthly.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2", "xkey": "revenue", "xlabel": quarter,
         "q": f"What was the total revenue in {quarter}?",
         "hint": revenue},
        {"kind": "fact", "tier": "T2",
         "q": f"Mennyi volt a legnagyobb beszállítói költés a(z) {quarter} időszakban?",
         "hint": vendor_spend},
        {"kind": "quote", "tier": "T1",
         "q": f"Quote the exact revenue sentence of the {quarter} financial report.",
         "hint": f"total revenue for {quarter} was {revenue}"},
    ]
    return text, facts


def gen_budget_memo(rng: random.Random, dept: str) -> tuple[str, list[dict]]:
    cap = _money(rng)
    cut = _pct(rng)
    owner = rng.choice(PEOPLE)
    deadline = _day(rng)
    text = (
        f"Budget Memo — {dept} (synthetic)\n\n"
        f"Author: {owner}\n\n"
        f"The {dept} department budget cap for the fiscal year is {cap}. Discretionary "
        f"spending must be reduced by {cut} compared to the previous year. Requests above "
        f"the cap require CFO approval.\n\n"
        f"All {dept} budget submissions are due by {deadline}. Late submissions roll over "
        f"to the next review cycle. Travel and training budgets are tracked separately.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2", "xkey": "budget_cap", "xlabel": dept,
         "q": f"What is the {dept} department budget cap?",
         "hint": cap},
        {"kind": "fact", "tier": "T2",
         "q": f"What is the submission deadline for the {dept} budget?",
         "hint": deadline},
    ]
    return text, facts


def gen_invoice_summary(rng: random.Random, quarter: str) -> tuple[str, list[dict]]:
    count = rng.randint(40, 220)
    largest_vendor = rng.choice(VENDORS)
    largest = _money(rng)
    overdue = rng.randint(0, 9)
    text = (
        f"Invoice Summary — {quarter} (synthetic)\n\n"
        f"A total of {count} supplier invoices were processed in {quarter}. The largest "
        f"single invoice came from {largest_vendor} at {largest}.\n\n"
        f"At quarter close {overdue} invoices were overdue; all were resolved in the "
        f"following month. Payment terms compliance is reported to the CFO monthly.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2",
         "q": f"How many supplier invoices were processed in {quarter}?",
         "hint": str(count)},
        {"kind": "fact", "tier": "T2",
         "q": f"Which vendor issued the largest single invoice in {quarter}?",
         "hint": largest_vendor},
    ]
    return text, facts


def gen_vendor_contract(rng: random.Random, vendor: str) -> tuple[str, list[dict]]:
    value = _money(rng)
    renewal = _day(rng)
    notice = rng.choice([30, 60, 90])
    sla = f"99.{rng.randint(0, 9)} percent"
    text = (
        f"Vendor Agreement Summary — {vendor} (synthetic)\n\n"
        f"Annual contract value: {value}. The agreement renews on {renewal} unless "
        f"terminated with {notice} days written notice.\n\n"
        f"Service level: {vendor} commits to {sla} monthly availability. Penalties apply "
        f"below the committed level as defined in Annex B. Data processing terms follow "
        f"the standard DPA. Invoicing is quarterly in arrears.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2",
         "q": f"What is the annual contract value of the {vendor} agreement?",
         "hint": value},
        {"kind": "fact", "tier": "T2",
         "q": f"How many days of notice are required to terminate the {vendor} agreement?",
         "hint": f"{notice} days"},
        {"kind": "quote", "tier": "T1",
         "q": f"Quote the availability commitment of {vendor}.",
         "hint": sla},
    ]
    return text, facts


def gen_handbook_chapter(rng: random.Random, topic: str) -> tuple[str, list[dict]]:
    if topic == "working-hours":
        core = f"{rng.choice(['08:30', '09:00', '09:30'])} and {rng.choice(['15:00', '15:30', '16:00'])}"
        text = (
            "Employee Handbook — Working Hours (synthetic)\n\n"
            f"Standard working time is 40 hours per week. Core hours are between {core}, "
            "during which employees are expected to be reachable. Flexible scheduling "
            "outside core hours is agreed with the team lead.\n\n"
            "Overtime must be pre-approved and is compensated according to local law.\n"
        )
        facts = [
            {"kind": "quote", "tier": "T1",
             "q": "Quote the core hours policy from the handbook.",
             "hint": f"Core hours are between {core}"},
        ]
    elif topic == "leave":
        days = rng.randint(25, 32)
        text = (
            "Employee Handbook — Leave Policy (synthetic)\n\n"
            f"Employees are entitled to {days} days of paid annual leave. Leave requests "
            "are submitted in the HR portal at least two weeks in advance. Unused days "
            "may be carried over until the end of March.\n\n"
            "Sick leave follows statutory rules and requires a medical certificate from "
            "the third day.\n"
        )
        facts = [
            {"kind": "fact", "tier": "T2",
             "q": "How many days of paid annual leave do employees get?",
             "hint": f"{days} days"},
            {"kind": "fact", "tier": "T2",
             "q": "Hány nap fizetett szabadság jár évente a munkavállalóknak?",
             "hint": f"{days}"},
        ]
    elif topic == "remote":
        cap = rng.choice([2, 3])
        text = (
            "Employee Handbook — Remote Work (synthetic)\n\n"
            f"Employees may work remotely up to {cap} days per week after probation. "
            "Remote days are coordinated within the team so that every project keeps an "
            "on-site presence. Equipment for remote work is provided by the company.\n\n"
            "Security rules: company data may only be accessed through the managed VPN, "
            "and confidential documents must not be stored on personal devices.\n"
        )
        facts = [
            {"kind": "fact", "tier": "T2",
             "q": "How many remote days per week are allowed after probation?",
             "hint": f"{cap} days"},
            {"kind": "synthesis", "tier": "T3",
             "q": "Summarize the remote work security rules.",
             "hint": "VPN"},
        ]
    else:  # security-training
        freq = rng.choice(["quarterly", "twice a year"])
        text = (
            "Employee Handbook — Security Training (synthetic)\n\n"
            f"Security awareness training is mandatory {freq} for all staff. Completion "
            "is tracked by HR and reported to the security officer. New joiners complete "
            "the baseline module during onboarding.\n\n"
            "Phishing simulations are run without prior notice; repeated failures "
            "trigger a refresher course.\n"
        )
        facts = [
            {"kind": "fact", "tier": "T2",
             "q": "How often is security awareness training mandatory?",
             "hint": freq},
        ]
    return text, facts


def gen_onboarding(rng: random.Random, dept: str) -> tuple[str, list[dict]]:
    buddy = rng.choice(PEOPLE)
    weeks = rng.choice([4, 6, 8])
    text = (
        f"Onboarding Guide — {dept} (synthetic)\n\n"
        f"The onboarding program for {dept} takes {weeks} weeks. Each new joiner gets a "
        f"buddy; the current buddy coordinator is {buddy}.\n\n"
        "Week one covers accounts, tooling and mandatory trainings. The remaining weeks "
        "are role-specific shadowing with weekly check-ins. Feedback is collected at the "
        "end of the program.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2",
         "q": f"How long is the onboarding program in {dept}?",
         "hint": f"{weeks} weeks"},
        {"kind": "fact", "tier": "T2",
         "q": f"Who is the buddy coordinator for {dept} onboarding?",
         "hint": buddy},
    ]
    return text, facts


def gen_board_minutes(rng: random.Random, month: str) -> tuple[str, list[dict]]:
    project = rng.choice(PROJECTS)
    budget = _money(rng)
    owner = rng.choice(PEOPLE)
    due = _day(rng)
    decision = rng.choice(["approved", "deferred to the next meeting", "rejected"])
    text = (
        f"Board Meeting Minutes — {month} (synthetic, restricted)\n\n"
        f"Attendees: Chair, CEO, CFO, and two non-executive directors.\n\n"
        f"1. Project {project}: the investment request of {budget} was {decision}. "
        f"Action item: {owner} to present the updated business case by {due}.\n\n"
        f"2. Risk review: the board reviewed the top operational risks. Vendor "
        f"concentration remains the highest rated risk; mitigation owned by Operations.\n\n"
        f"3. Governance: the annual policy review cycle starts next month.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2",
         "q": f"What was the board decision on the Project {project} investment in {month}?",
         "hint": decision},
        {"kind": "fact", "tier": "T2",
         "q": f"Who presents the updated {project} business case, and by when ({month} minutes)?",
         "hint": owner, "extra_hints": [due]},
        {"kind": "trap", "tier": "T4",
         "q": f"What investment amount did the board discuss for Project {project} in {month}?",
         "hint": budget},
    ]
    return text, facts


def gen_strategy_memo(rng: random.Random, project: str) -> tuple[str, list[dict]]:
    target = _pct(rng)
    horizon = rng.choice(["12 months", "18 months", "24 months"])
    text = (
        f"Strategy Memo — Project {project} (synthetic, restricted)\n\n"
        f"Objective: grow the addressable market share by {target} within {horizon}. "
        f"The plan assumes phased rollout, partner enablement and pricing review.\n\n"
        f"Key dependencies: engineering capacity, regulatory review and channel "
        f"readiness. The steering committee meets monthly.\n"
    )
    facts = [
        {"kind": "fact", "tier": "T2",
         "q": f"What is the market share growth target of Project {project}?",
         "hint": target},
        {"kind": "synthesis", "tier": "T3",
         "q": f"Summarize the key dependencies of Project {project}.",
         "hint": "engineering capacity"},
    ]
    return text, facts


# ---------------------------------------------------------------------------
# Corpus assembly
# ---------------------------------------------------------------------------


def build_corpus(rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Returns (documents, fact_records).

    document = {drive, path, file_id, title, text, mtime_days_ago, sensitivity, doc_type}
    fact_record = fact + {file_id, drive}
    """
    docs: list[dict] = []
    facts: list[dict] = []

    def add(drive: str, name: str, title: str, text: str, doc_facts: list[dict],
            sensitivity: str, doc_type: str) -> None:
        path = f"{drive}/{name}.txt"
        file_id = f"vivesec://files/{drive}/{name}"
        docs.append({
            "drive": drive, "path": path, "file_id": file_id, "title": title,
            "text": text, "mtime_days_ago": rng.randint(5, 400),
            "sensitivity": sensitivity, "doc_type": doc_type,
        })
        for f in doc_facts:
            facts.append({**f, "file_id": file_id, "drive": drive})

    # --- finance drive (quarterlies + invoices + budgets + vendor contracts) -
    for q in QUARTERS:
        text, f = gen_quarterly_report(rng, q)
        add("finance", f"report_{q.lower().replace(' ', '_')}", f"{q} Financial Report",
            text, f, "confidential", "financial_report")
    for q in QUARTERS:
        text, f = gen_invoice_summary(rng, q)
        add("finance", f"invoices_{q.lower().replace(' ', '_')}", f"Invoice Summary {q}",
            text, f, "confidential", "invoice_summary")
    for dept in DEPARTMENTS:
        text, f = gen_budget_memo(rng, dept)
        add("finance", f"budget_{dept.lower()}", f"Budget Memo {dept}",
            text, f, "confidential", "budget_memo")
    for v in VENDORS:
        text, f = gen_vendor_contract(rng, v)
        slug = v.lower().replace(" ", "_")
        add("finance", f"vendor_{slug}", f"Vendor Agreement {v}",
            text, f, "confidential", "vendor_contract")

    # --- hr drive (handbook chapters + onboarding) --------------------------
    for topic in ["working-hours", "leave", "remote", "security-training"]:
        text, f = gen_handbook_chapter(rng, topic)
        add("hr", f"handbook_{topic.replace('-', '_')}", f"Handbook {topic}",
            text, f, "internal", "employee_handbook")
    for dept in DEPARTMENTS:
        text, f = gen_onboarding(rng, dept)
        add("hr", f"onboarding_{dept.lower()}", f"Onboarding {dept}",
            text, f, "internal", "onboarding_guide")
    # edge cases: empty + unicode-heavy note
    add("hr", "empty_placeholder", "Empty placeholder", "", [],
        "internal", "note")
    add("hr", "unicode_note",
        "Ékezetes jegyzet",
        "Belső jegyzet (szintetikus): az őszi árvíztűrő tükörfúrógép átadása "
        "sikeresen megtörtént. Felelős: üzemeltetés. További teendő nincs.\n",
        [{"kind": "fact", "tier": "T2",
          "q": "Ki a felelős az árvíztűrő tükörfúrógép átadásáért?",
          "hint": "üzemeltetés"}],
        "internal", "note")

    # --- board drive (minutes + strategy, restricted) ------------------------
    months = ["January 2026", "February 2026", "March 2026", "April 2026",
              "May 2026", "June 2026"]
    for m in months:
        text, f = gen_board_minutes(rng, m)
        add("board", f"minutes_{m.lower().replace(' ', '_')}", f"Board Minutes {m}",
            text, f, "restricted", "board_minutes")
    for p in PROJECTS:
        text, f = gen_strategy_memo(rng, p)
        add("board", f"strategy_{p.lower()}", f"Strategy {p}",
            text, f, "restricted", "strategy_memo")

    return docs, facts


# ---------------------------------------------------------------------------
# Gold-set assembly
# ---------------------------------------------------------------------------


def build_gold(rng: random.Random, docs: list[dict], facts: list[dict]) -> list[dict]:
    """Turn planted facts into gold cases.

    ACL model: allowed = EVERY file of the allowed drive(s) (drive = ACL unit),
    relevant = the file the fact lives in, forbidden = canary files from the
    NON-allowed drives (their presence in results = leak).
    """
    by_drive: dict[str, list[str]] = {}
    for d in docs:
        by_drive.setdefault(d["drive"], []).append(d["file_id"])

    def canaries(excluded: list[str]) -> list[str]:
        out: list[str] = []
        for drive, ids in by_drive.items():
            if drive not in excluded:
                out.extend(rng.sample(ids, min(2, len(ids))))
        return out

    cases: list[dict] = []
    n = 0

    def case_id() -> str:
        nonlocal n
        n += 1
        return f"q-gen-{n:03d}"

    # 1) Normal per-fact cases (fact / quote / synthesis) — user sees ONLY the
    #    drive the fact lives in.
    for f in facts:
        if f["kind"] == "trap":
            continue
        drive = f["drive"]
        lang = "hu" if any(ch in f["q"] for ch in "áéíóöőúüű") else "en"
        cases.append({
            "id": case_id(),
            "query": f["q"],
            "lang": lang,
            "allowed_file_ids": sorted(by_drive[drive]),
            "relevant_file_ids": [f["file_id"]],
            "forbidden_file_ids": sorted(canaries([drive])),
            "expected_tier": f["tier"],
            "answer_hints": [f["hint"]] + list(f.get("extra_hints") or []),
            "notes": f"generated:{f['kind']}",
        })

    # 2) Cross-document synthesis (T3): compare two planted values of the same
    #    xkey family — relevant = BOTH files, so recall@k is stressed for real.
    xgroups: dict[str, list[dict]] = {}
    for f in facts:
        if f.get("xkey"):
            xgroups.setdefault(f["xkey"], []).append(f)
    XQ = {
        "revenue": "Compare the total revenue of {a} and {b}. Which was higher?",
        "budget_cap": "Compare the budget caps of the {a} and {b} departments.",
    }
    for xkey, group in sorted(xgroups.items()):
        rng.shuffle(group)
        for i in range(0, len(group) - 1, 2):
            fa, fb = group[i], group[i + 1]
            drive = fa["drive"]
            cases.append({
                "id": case_id(),
                "query": XQ[xkey].format(a=fa["xlabel"], b=fb["xlabel"]),
                "lang": "en",
                "allowed_file_ids": sorted(by_drive[drive]),
                "relevant_file_ids": sorted({fa["file_id"], fb["file_id"]}),
                "forbidden_file_ids": sorted(canaries([drive])),
                "expected_tier": "T3",
                "answer_hints": [fa["hint"], fb["hint"]],
                "notes": f"generated:cross-doc {xkey}",
            })

    # 3) ACL-trap cases: the fact lives on the BOARD drive, but the user only
    #    sees finance+hr. Correct behaviour: no leak, nothing relevant found
    #    (level-2 expects a refusal — expected_tier T4).
    for f in facts:
        if f["kind"] != "trap":
            continue
        cases.append({
            "id": case_id(),
            "query": f["q"],
            "lang": "en",
            "allowed_file_ids": sorted(by_drive["finance"] + by_drive["hr"]),
            "relevant_file_ids": [],
            "forbidden_file_ids": sorted(by_drive["board"]),
            "expected_tier": "T4",
            "leak_hints": [f["hint"]],
            "notes": f"acl-trap: answer only on board drive ({f['file_id']})",
        })

    # 4) Out-of-corpus (unanswerable anywhere) — refusal material for level 2.
    OUT_OF_CORPUS = [
        "What is the wifi password of the Vienna office?",
        "Mikor lesz a következő céges síelés?",
        "What does the source code of the payment service look like?",
        "Which cloud region hosts the production database?",
        "What is the CEO's home address?",
        "Mennyi a kávégép karbantartási díja?",
    ]
    for q in OUT_OF_CORPUS:
        drive = rng.choice(list(by_drive.keys()))
        cases.append({
            "id": case_id(),
            "query": q,
            "lang": "hu" if any(ch in q for ch in "áéíóöőúüű") else "en",
            "allowed_file_ids": sorted(by_drive[drive]),
            "relevant_file_ids": [],
            "forbidden_file_ids": sorted(canaries([drive])),
            "expected_tier": "T4",
            "notes": "out-of-corpus: must refuse, no grounded answer exists",
        })

    rng.shuffle(cases)
    return cases


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_corpus(docs: list[dict], out_dir: str) -> None:
    now = time.time()
    for d in docs:
        path = os.path.join(out_dir, d["path"].replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(d["text"])
        mtime = now - d["mtime_days_ago"] * 86400
        os.utime(path, (mtime, mtime))
    manifest = {
        "template_version": TEMPLATE_VERSION,
        "documents": [
            {
                "file_id": d["file_id"],
                "path": d["path"],
                "drive": d["drive"],
                "acl_scope": [d["drive"]],
                "sensitivity": d["sensitivity"],
                "doc_type": d["doc_type"],
                "language_hint": "hu" if d["path"].endswith("unicode_note.txt") else "en",
                "title": d["title"],
                "mtime_days_ago": d["mtime_days_ago"],
            }
            for d in docs
        ],
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_gold(cases: list[dict], gold_dir: str, dev_ratio: float = 0.4) -> tuple[int, int]:
    cut = int(len(cases) * dev_ratio)
    dev, holdout = cases[:cut], cases[cut:]
    for name, split in (("dev_full", dev), ("holdout_full", holdout)):
        with open(os.path.join(gold_dir, f"{name}.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            for c in split:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return len(dev), len(holdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="generate the FULL harness corpus + gold set")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="fixtures_full",
                    help="output fixtures dir (relative to harness/)")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    docs, facts = build_corpus(rng)
    cases = build_gold(rng, docs, facts)

    out_dir = os.path.join(HERE, args.out)
    gold_dir = os.path.join(HERE, "gold_set")
    write_corpus(docs, out_dir)
    n_dev, n_holdout = write_gold(cases, gold_dir)

    drives = {}
    for d in docs:
        drives[d["drive"]] = drives.get(d["drive"], 0) + 1
    tiers = {}
    for c in cases:
        tiers[c["expected_tier"]] = tiers.get(c["expected_tier"], 0) + 1
    print(f"corpus : {len(docs)} docs  {drives}")
    print(f"gold   : {len(cases)} cases  dev={n_dev} holdout={n_holdout}  tiers={tiers}")
    print(f"out    : {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
