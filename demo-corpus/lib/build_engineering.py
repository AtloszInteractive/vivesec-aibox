"""Engineering drive: project plans, weekly status, specs, test logs, service."""
from __future__ import annotations

import random

from .common import Bible, Doc, bullets, h1, h2, h3, kv, md, numbered, para, table

DRIVE = "drive_engineering"


def build(b: Bible) -> list[Doc]:
    d = b.d
    helios = b.projects["prj_helios"]
    meridian = b.projects["prj_meridian"]
    anchor = b.projects["prj_anchor"]
    tundra = b.projects["prj_tundra"]
    docs: list[Doc] = []

    # ------------------------------------------------ Helios plan: v1/v2/final
    def helios_plan(version: str, pac_date: str, batch2: str, note: str,
                    issued: str) -> list[tuple[str, str]]:
        return [
            ("h2", "1. Purpose and scope"),
            ("", f"This plan governs the supply, installation and commissioning of a 40 MWh battery energy "
                 f"storage system for {helios['customer']} at the Esbjerg site under the agreement signed on "
                 f"18 September 2025. Contract value is EUR {helios['value_meur']} million."),
            ("h2", "2. Document control"),
            ("", f"Version: {version}. Issued: {issued}. Project manager: {b.who('p_nygaard')}. "
                 f"Sponsor: {b.who('p_holm')}. Approver: {b.who('p_barat')}."),
            ("", note),
            ("h2", "3. Schedule"),
            ("", "Detailed design freeze: 30 January 2026."),
            ("", "Cell delivery batch 1: 15 April 2026."),
            ("", f"Cell delivery batch 2: {batch2}."),
            ("", "Site energisation: 15 September 2026."),
            ("", f"Provisional acceptance (PAC): {pac_date}."),
            ("", "Final acceptance (FAC): twelve months after provisional acceptance."),
            ("h2", "4. Work breakdown"),
            ("", "WBS 1000 Engineering and design — owner: Péter Halász."),
            ("", "WBS 2000 Procurement and cell supply — owner: Zoltán Varga."),
            ("", "WBS 3000 Manufacturing and factory acceptance test — owner: Stefan Ruhland."),
            ("", "WBS 4000 Site works, installation and cabling — owner: Lars Nygaard."),
            ("", "WBS 5000 Commissioning, SAT and grid compliance — owner: Lars Nygaard."),
            ("", "WBS 6000 Handover, documentation and training — owner: Katrine Holm."),
            ("h2", "5. Contractual milestones and payment"),
            ("", "Payment terms are 30% on order, 50% on delivery and 20% on provisional acceptance, "
                 "net 30 days from invoice."),
            ("", "Delay damages under clause 8.2 accrue at 0.5% of the contract price per commenced week "
                 "and are capped at 10% of the contract price."),
            ("", "The warranty period under clause 11.1 is 24 months from provisional acceptance, "
                 "extendable to 60 months against an annual service fee."),
            ("h2", "6. Governance"),
            ("", "The steering committee meets monthly. Escalation path: project manager, sponsor, "
                 "Executive Committee, Board. Change requests above EUR 50,000 require sponsor approval."),
            ("h2", "7. Quality and HSE"),
            ("", "All site work follows the Voltara HSE handbook and the customer's site rules. "
                 "The site acceptance test procedure SAT-ESB-01 applies. Grid compliance is demonstrated "
                 "against EN 50549-2 and the Danish grid code."),
        ]

    docs.append(Doc(
        DRIVE, "projects/Helios_Project_Plan_v1.docx", "Project Helios — Project Plan v1", "docx",
        tags=["/tracking", "difficulty:versions", "superseded"],
        blocks=helios_plan(
            "v1", "30 November 2026", "30 June 2026",
            "SUPERSEDED. This version was issued before the design freeze and does not reflect the "
            "approved cable routing or the revised commissioning sequence.",
            "2025-10-14")))
    docs.append(Doc(
        DRIVE, "projects/Helios_Project_Plan_v2.docx", "Project Helios — Project Plan v2", "docx",
        tags=["/tracking", "difficulty:versions", "superseded"],
        blocks=helios_plan(
            "v2", "30 November 2026", "30 June 2026",
            "SUPERSEDED by the final version issued on 2026-07-15. Retained for audit purposes only.",
            "2026-02-20")))
    docs.append(Doc(
        DRIVE, "projects/Helios_Project_Plan_final.docx", "Project Helios — Project Plan (final)", "docx",
        tags=["/tracking", "difficulty:versions", "authoritative"],
        blocks=helios_plan(
            "final", "22 January 2027 (revised forecast; contractual date remains 30 November 2026)",
            "21 August 2026 (revised forecast; contractual date remains 30 June 2026)",
            "THIS IS THE CURRENT AUTHORITATIVE VERSION. It supersedes v1 (2025-10-14) and v2 (2026-02-20). "
            "The revised dates reflect the Nordcell cell delivery delay recorded as risk R-HEL-04.",
            "2026-07-15")))

    # ----------------------------------------------------- Helios risk register
    risk_rows = [["Risk ID", "Description", "Severity", "Owner", "Impact", "Mitigation", "Status"]]
    for r in helios["open_risks"]:
        risk_rows.append([r["id"], r["description"], r["severity"], b.name(r["owner"]),
                          r.get("impact", ""), r.get("mitigation", ""), "open"])
    risk_rows += [
        ["R-HEL-01", "Cable trench works dependent on customer civil contractor", "low",
         b.name("p_nygaard"), "Up to 2 weeks schedule", "Weekly coordination call with Vestkraft", "closed"],
        ["R-HEL-02", "Factory acceptance test capacity clash with Regensburg order", "medium",
         b.name("p_ruhland"), "FAT slot conflict in October", "Reserve line time from 5 October", "open"],
        ["R-HEL-03", "Danish grid code revision expected Q4 2026", "low",
         b.name("p_kiss"), "Re-certification of controller settings", "Track TSO consultation", "open"],
        ["R-HEL-05", "Winter weather window for outdoor commissioning", "medium",
         b.name("p_nygaard"), "Commissioning limited from mid-December", "Front-load outdoor works", "open"],
        ["R-HEL-06", "Currency exposure on DKK milestone payments", "low",
         b.name("p_sorensen"), "FX movement on 20% final payment", "Forward cover taken 2026-03-05", "closed"],
    ]
    docs.append(Doc(
        DRIVE, "projects/Helios_Risk_Register.xlsx", "Project Helios — risk register", "xlsx",
        tags=["/tracking", "difficulty:xlsx"],
        sheets={
            "Risks": risk_rows,
            "Milestones": [["Milestone", "Contractual date", "Forecast", "Status"]] +
                          [[m["name"], m["due"], m.get("actual") or m.get("forecast") or "", m["status"]]
                           for m in helios["milestones"]],
        }))

    # ---------------------------------------------------------- Anchor tasks
    docs.append(Doc(
        DRIVE, "projects/Anchor_Task_List_2026-07.md", "Project Anchor — task list July 2026", "md",
        tags=["/tracking"],
        body=md(
            h1("Project Anchor — Task List, July 2026"),
            kv([("Project", anchor["name"]), ("Manager", b.who("p_novak")),
                ("Sponsor", b.who("p_sorensen")),
                ("Budget", f"EUR {anchor['budget_meur']} million"),
                ("Spend to date", f"EUR {anchor['spend_to_date_meur']} million"),
                ("Percent complete", f"{anchor['percent_complete']}%"),
                ("Status", anchor["status"].upper())]),
            h2("Open tasks"),
            table(["ID", "Task", "Owner", "Due", "Status"],
                  [["ANC-114", "Reconcile DK open receivables that failed automatic migration",
                    "Tamás Novák", "2026-08-21", "in progress"],
                   ["ANC-115", "Freeze the chart of accounts for the HU and DE entities",
                    "Tamás Novák", "2026-08-07", "in progress"],
                   ["ANC-118", "User acceptance testing, purchase-to-pay cycle",
                    "Zoltán Varga", "2026-08-28", "not started"],
                   ["ANC-119", "User acceptance testing, order-to-cash cycle",
                    "Sofie Lund", "2026-08-28", "not started"],
                   ["ANC-121", "Cut-over rehearsal 3 including bank interfaces",
                    "Tamás Novák", "2026-09-11", "not started"],
                   ["ANC-122", "Decommissioning plan for the legacy system",
                    "Bence Tóth", "2026-10-30", "not started"],
                   ["ANC-125", "Dual-ledger reporting procedure for the phased go-live",
                    "Tamás Novák", "2026-09-04", "not started"]]),
            h2("Completed in July"),
            bullets([
                "ANC-109 Data migration dry-run 2 completed on 14 July 2026, one day ahead of plan.",
                "ANC-111 Interface specification for the Northbridge to Voltara Service Portal link signed off.",
                "ANC-112 Segregation-of-duties matrix reviewed with the compliance function.",
            ]),
            h2("Decision pending"),
            para("""
            The go-live approach (single cut-over on 30 September 2026 versus a phased approach with the Danish
            entity in a second wave on 30 November 2026) is with the Executive Committee. See the decision memo
            in the finance drive. The recommendation is the phased approach at an additional cost of
            EUR 74,000.
            """),
        )))

    # --------------------------------------------------------- steering minutes
    steering = [
        ("2026-05-14", "42%", [
            "The detailed design freeze was confirmed as complete, six days late, with no cost impact.",
            "Cell delivery batch 1 arrived on 11 April, four days ahead of the contractual date.",
            "Nordcell confirmed a two-week delay to batch 2; the committee asked procurement to obtain a "
            "written recovery plan by 29 May.",
            "The customer requested a variation to the auxiliary transformer rating; the change request "
            "CR-HEL-07 was priced at EUR 46,800 and approved by the sponsor.",
        ]),
        ("2026-06-11", "51%", [
            "Nordcell revised the batch 2 forecast from mid-July to 21 August, a cumulative slip of seven weeks.",
            "The committee instructed procurement to start qualification of an alternative cell supplier.",
            "The forecast provisional acceptance date moved from 30 November 2026 to 22 January 2027.",
            "Finance was asked to quantify the delay damages exposure under clause 8.2 for the next meeting.",
        ]),
        ("2026-07-09", "58%", [
            "Delay damages exposure was presented at EUR 0.50 million against a contractual cap of "
            "EUR 1.24 million.",
            "A counter-claim against Nordcell under clause 7.1 of the framework agreement was approved for "
            "service; the letter was issued on 30 July 2026.",
            "LM Cells ApS passed the technical pre-qualification; a commercial decision is due by 29 August 2026.",
            "The committee agreed to notify the customer formally of the revised acceptance date before "
            "31 August 2026.",
            "The project plan was reissued as the final version on 15 July 2026.",
        ]),
        ("2026-08-06", "61%", [
            "The customer was formally notified of the revised provisional acceptance date of "
            "22 January 2027 on 31 July 2026, within the deadline set by the committee.",
            "Nordcell confirmed the batch 2 delivery date of 21 August 2026 in writing and did not "
            "dispute the claim served on 30 July 2026.",
            "LM Cells ApS commercial evaluation is complete; the recommendation is dual sourcing with a "
            "70/30 volume split, for decision by 29 August 2026.",
            "Winter working procedures for the site were tabled by the HSE function; outdoor works are to "
            "be front-loaded before mid-December.",
            "The erection all-risks insurance expires with provisional acceptance; an extension has been "
            "requested from the broker.",
        ]),
    ]
    for date, complete, points in steering:
        docs.append(Doc(
            DRIVE, f"minutes/Helios_Steering_Minutes_{date}.md",
            f"Project Helios steering committee — {date}", "md", tags=["/summary"],
            body=md(
                h1(f"Project Helios — Steering Committee Minutes, {date}"),
                kv([("Chair", b.who("p_holm")),
                    ("Present", ", ".join([b.name(x) for x in
                                           ["p_holm", "p_nygaard", "p_varga", "p_halasz", "p_novak"]])),
                    ("Apologies", b.name("p_ruhland")),
                    ("Percent complete", complete),
                    ("Minutes by", b.name("p_farkas"))]),
                h2("Decisions and discussion"),
                bullets(points),
                h2("Actions"),
                table(["Action", "Owner", "Due"],
                      [["Confirm the recovery plan with the cell supplier", b.name("p_varga"),
                        "2026-08-29"],
                       ["Update the customer on the revised acceptance date", b.name("p_nygaard"),
                        "2026-08-31"],
                       ["Refresh the delay damages exposure for the Board pack", b.name("p_novak"),
                        "2026-09-04"]]),
            )))

    # ------------------------------------------------------- weekly status
    weeks = {
        20: ("2026-05-11", "green", "Design documentation for the auxiliary transformer variation issued.",
             "Meridian beta build 3.0.0-b7 passed the regression suite."),
        21: ("2026-05-18", "green", "Cable pulling started at the Esbjerg site.",
             "Northbridge ERP interface specification circulated for review."),
        22: ("2026-05-25", "amber", "Nordcell confirmed a further two-week slip on cell batch 2.",
             "Meridian IEC 61850 conformance pre-test completed with three minor observations."),
        23: ("2026-06-01", "amber", "Alternative cell supplier long-list reduced to two candidates.",
             "Orion building permit received from the city of München."),
        24: ("2026-06-08", "amber", "Revised acceptance date of 22 January 2027 agreed internally.",
             "AI Box platform pilot opened to the finance and operations teams."),
        25: ("2026-06-15", "amber",
             "Delay damages exposure modelled at EUR 0.50 million on the current forecast.",
             "AI Box platform went live with 38 users and five indexed drives."),
        26: ("2026-06-22", "amber",
             "Delay damages exposure confirmed at EUR 0.50 million after the finance review.",
             "AI Box platform usage reached 38 enabled users across five indexed drives."),
        27: ("2026-06-29", "amber", "LM Cells ApS passed technical pre-qualification.",
             "Meridian type approval dossier assembly started for the TÜV Süd submission."),
        28: ("2026-07-06", "amber", "Steering committee approved serving the Nordcell counter-claim.",
             "Data migration dry-run 2 preparation completed."),
        29: ("2026-07-13", "amber", "Data migration dry-run 2 completed one day ahead of plan.",
             "Project plan reissued as the final version."),
        30: ("2026-07-20", "amber", "Orion assembly equipment order placed a week early.",
             "Q2 management accounts issued on 21 July."),
        31: ("2026-07-27", "amber", "Formal delay claim served on the cell supplier under clause 7.1.",
             "LM Cells commercial evaluation started; decision due 29 August."),
        32: ("2026-08-03", "amber",
             "Customer formally notified of the revised acceptance date of 22 January 2027.",
             "Type approval dossier for firmware v3.0 is 70 percent assembled."),
    }
    for wk, (date, rag, line1, line2) in weeks.items():
        docs.append(Doc(
            DRIVE, f"status/Weekly_Status_2026-W{wk}.md",
            f"Weekly engineering status 2026-W{wk}", "md",
            tags=["/report"] + (["difficulty:near-duplicate"] if wk in (25, 26) else []),
            body=md(
                h1(f"Weekly Engineering Status — 2026-W{wk} (week ending {date})"),
                kv([("Prepared by", b.who("p_halasz")), ("Overall status", rag.upper())]),
                h2("Highlights"),
                bullets([line1, line2]),
                h2("Project status"),
                table(["Project", "Status", "Comment"],
                      [["Helios", "amber", "Cell delivery batch 2 delayed; recovery plan in progress"],
                       ["Meridian", "green", "Firmware v3.0 on track for type approval submission"],
                       ["Anchor", "amber", "Go-live approach under decision"],
                       ["Tundra", "green", "Deliverable D1.1 submitted"],
                       ["Orion", "green", "Building works progressing to plan"]]),
                h2("Resourcing"),
                para(f"""
                Engineering headcount stands at 96 across the three sites. Four positions remain open in
                Budapest and two in München. The average time to hire is
                {d['hr']['average_time_to_hire_days']} days.
                """),
                h2("Next week"),
                bullets([
                    "Continue site cable works and prepare the energisation checklist.",
                    "Close out the remaining IEC 61850 conformance observations.",
                    "Progress the alternative cell supplier commercial evaluation.",
                ]),
            )))

    # ----------------------------------------------------------- Meridian
    docs.append(Doc(
        DRIVE, "meridian/Meridian_Sprint_Log_2026-Q2.md",
        "Project Meridian sprint log Q2 2026", "md", tags=["/report", "/tracking"],
        body=md(
            h1("Project Meridian — Sprint Log Q2 2026"),
            kv([("Project", meridian["name"]), ("Lead", b.who("p_kiss")),
                ("Sponsor", b.who("p_lehmann")),
                ("Budget", f"EUR {meridian['budget_meur']} million"),
                ("Spend to date", f"EUR {meridian['spend_to_date_meur']} million"),
                ("Percent complete", f"{meridian['percent_complete']}%")]),
            h2("Sprint summary"),
            table(["Sprint", "Dates", "Committed", "Delivered", "Notes"],
                  [["2026.07", "01–14 Apr", 34, 34, "IEC 61850 GOOSE publisher completed"],
                   ["2026.08", "15–28 Apr", 38, 31, "Two stories carried over: MMS reporting"],
                   ["2026.09", "29 Apr–12 May", 36, 38, "Carry-over cleared, IEEE 1547 ride-through added"],
                   ["2026.10", "13–26 May", 30, 30, "Beta freeze prepared, regression suite green"],
                   ["2026.11", "27 May–09 Jun", 28, 26, "Conformance test findings triaged"],
                   ["2026.12", "10–23 Jun", 32, 32, "Type approval dossier assembly started"]]),
            h2("Beta freeze"),
            para("""
            Firmware v3.0 beta was frozen on 27 May 2026, two days ahead of the milestone date of 29 May 2026.
            The build identifier is 3.0.0-rc1. Three minor observations from the IEC 61850 conformance
            pre-test were closed in sprint 2026.11.
            """),
            h2("Upcoming milestones"),
            table(["Milestone", "Due", "Status"],
                  [[m["name"], m["due"], m["status"]] for m in meridian["milestones"]]),
        )))

    docs.append(Doc(
        DRIVE, "meridian/GC3000_Firmware_v3_Release_Notes.md",
        "GC-3000 firmware v3.0 release notes (beta)", "md", tags=["/report"],
        body=md(
            h1("GC-3000 Firmware v3.0.0 — Release Notes (beta)"),
            kv([("Build", "3.0.0-rc1"), ("Frozen", "2026-05-27"), ("Status", "beta, not for production"),
                ("Owner", b.who("p_kiss"))]),
            h2("New features"),
            bullets([
                "Full IEC 61850 edition 2.1 server with GOOSE publishing and MMS reporting.",
                "IEEE 1547-2018 category II voltage and frequency ride-through profiles.",
                "EN 50549-2 compliant reactive power control modes: fixed cos phi, Q(U) and Q(P).",
                "Secure boot chain with a hardware root of trust and signed firmware images.",
                "Redundant control pair with sub-100 ms failover.",
            ]),
            h2("Improvements"),
            bullets([
                "Control loop cycle time reduced from 20 ms to 10 ms.",
                "Event log capacity increased from 50,000 to 250,000 entries.",
                "Configuration import and export in SCL format.",
            ]),
            h2("Known limitations"),
            bullets([
                "Type approval by TÜV Süd is not yet granted; submission is due 15 October 2026.",
                "The legacy Modbus RTU gateway is not supported in this build.",
                "Field upgrade from v2.6.x requires a service visit; over-the-air upgrade lands in 3.1.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "meridian/GC3000_Firmware_v2.6.4_Release_Notes.md",
        "GC-3000 firmware v2.6.4 release notes", "md", tags=["/report"],
        body=md(
            h1("GC-3000 Firmware v2.6.4 — Release Notes"),
            kv([("Build", "2.6.4"), ("Released", "2026-04-08"),
                ("Status", "current production release"), ("Owner", b.who("p_kiss"))]),
            h2("Fixes"),
            bullets([
                "Corrected a rounding error in the state-of-charge estimate below 5% capacity.",
                "Fixed a rare watchdog reset when the SNTP server became unreachable for over 48 hours.",
                "Hardened the web configuration interface against a session fixation issue reported in the "
                "2025 penetration test (finding PT-2025-H2).",
            ]),
            h2("Upgrade notes"),
            para("""
            Version 2.6.4 is a drop-in replacement for 2.6.3 and requires no configuration change. All units in
            the field were updated by 30 May 2026. The 41 sites connected to the Voltara Service Portal were
            upgraded remotely.
            """),
        )))

    docs.append(Doc(
        DRIVE, "meridian/Meridian_ADR_012_Timing_Model.md",
        "ADR-012: control loop timing model", "md", tags=["/memo"],
        body=md(
            h1("Architecture Decision Record 012 — Control Loop Timing Model"),
            kv([("Status", "accepted"), ("Date", "2026-03-18"), ("Author", b.who("p_kiss")),
                ("Reviewers", f"{b.name('p_lehmann')}, {b.name('p_halasz')}")]),
            h2("Context"),
            para("""
            The v2 control loop runs at a fixed 20 ms cycle with a cooperative scheduler. IEEE 1547-2018
            category II ride-through requires deterministic response within one grid cycle, which the existing
            model cannot guarantee under worst-case communication load.
            """),
            h2("Decision"),
            para("""
            Move to a pre-emptive scheduler with a 10 ms control cycle and a strict priority band for the
            protection path. Communication stacks run in a lower priority band with bounded buffers. The
            protection path is verified by static worst-case execution time analysis rather than by
            measurement alone.
            """),
            h2("Consequences"),
            bullets([
                "The control processor headroom drops from 61% to 38% at nominal load, which is acceptable.",
                "The Modbus RTU gateway cannot meet the new timing budget and is dropped from v3.0.",
                "Field upgrade from v2.6.x requires a service visit because the bootloader changes.",
            ]),
        )))

    # ------------------------------------------- large test log (>1 MB, dif)
    rng = random.Random(20260615)
    log_lines = [
        "GC-3000 firmware v3.0.0-rc1 — automated regression and endurance test log",
        "Test bench: TB-MUC-02, ambient 23.4 C, DC source Chroma 62180D-1200, grid simulator Regatron TC.ACS",
        "Operator: automated harness v4.2, supervised by Márton Kiss",
        "Start: 2026-06-01T06:00:00Z   End: 2026-06-30T23:59:00Z",
        "",
    ]
    suites = ["IEC61850_GOOSE", "IEC61850_MMS", "IEEE1547_RIDE_THROUGH", "EN50549_Q_U",
              "SOC_ESTIMATOR", "SECURE_BOOT", "FAILOVER_PAIR", "EVENT_LOG", "MODBUS_TCP",
              "THERMAL_DERATE", "SCL_IMPORT", "WATCHDOG"]
    for day in range(1, 31):
        log_lines.append(f"===== 2026-06-{day:02d} nightly run =====")
        for cycle in range(1, 221):
            suite = suites[(day + cycle) % len(suites)]
            dur = rng.uniform(0.8, 9.4)
            cyc_ms = rng.uniform(9.1, 10.4)
            jitter = rng.uniform(0.02, 0.61)
            soc = rng.uniform(11.0, 96.0)
            verdict = "PASS"
            if rng.random() < 0.014:
                verdict = "FAIL"
            log_lines.append(
                f"2026-06-{day:02d}T{(cycle // 3) % 24:02d}:{(cycle * 7) % 60:02d}:{(cycle * 13) % 60:02d}Z "
                f"suite={suite} cycle={cycle:03d} duration_s={dur:.3f} loop_ms={cyc_ms:.3f} "
                f"jitter_ms={jitter:.3f} soc_pct={soc:.2f} temp_c={rng.uniform(21.0, 44.0):.2f} "
                f"dc_v={rng.uniform(690.0, 812.0):.1f} ac_kw={rng.uniform(-1250.0, 1250.0):.1f} "
                f"verdict={verdict}"
            )
            if verdict == "FAIL":
                log_lines.append(
                    f"    detail: assertion 'response_within_one_cycle' violated, measured "
                    f"{rng.uniform(10.5, 13.2):.3f} ms; retried and passed on repeat, "
                    f"tracked as observation OBS-{day:02d}{cycle:03d}"
                )
        log_lines.append(f"----- daily summary 2026-06-{day:02d}: 220 cycles executed -----")
        log_lines.append("")
    log_lines.append("FINAL SUMMARY: 6600 cycles executed, 6508 passed, 92 retried and passed on repeat, "
                     "0 unresolved failures. Firmware v3.0.0-rc1 is released for the type approval dossier.")
    docs.append(Doc(
        DRIVE, "test/GC3000_Firmware_v3_Test_Log_2026-06.txt",
        "GC-3000 firmware v3 endurance test log, June 2026", "txt",
        tags=["difficulty:large-file"], body="\n".join(log_lines)))

    # ----------------------------------------- numeric-dense CSV (difficulty)
    rng2 = random.Random(4242)
    csv_lines = ["cell_id,cycle,capacity_ah,dcir_mohm,temp_c,voltage_v,coulombic_eff,soh_pct"]
    for cell in range(1, 41):
        for cycle in range(0, 8001, 250):
            csv_lines.append(
                f"C{cell:03d},{cycle},{rng2.uniform(94.0, 106.0):.3f},{rng2.uniform(0.42, 1.18):.4f},"
                f"{rng2.uniform(18.0, 41.0):.2f},{rng2.uniform(3.21, 4.19):.4f},"
                f"{rng2.uniform(0.9912, 0.9998):.5f},{max(70.0, 100 - cycle * 0.0031):.3f}"
            )
    docs.append(Doc(
        DRIVE, "test/VoltStack2_Cycle_Test_Matrix.csv",
        "VoltStack 2 cycle test matrix", "csv",
        tags=["difficulty:numeric-dense"], body="\n".join(csv_lines)))

    # ------------------------------------------------------------- specs
    vs2 = next(p for p in d["products"] if p["id"] == "prod_vs2")
    docs.append(Doc(
        DRIVE, "specs/VoltStack2_Technical_Specification.md",
        "VoltStack 2 technical specification", "md", tags=["/search"],
        body=md(
            h1("VoltStack 2 — Technical Specification"),
            kv([("Document", "SPEC-VS2-014"), ("Revision", "D"), ("Date", "2026-02-27"),
                ("Owner", b.who("p_petrova"))]),
            h2("1. System overview"),
            para("""
            VoltStack 2 is a containerised lithium iron phosphate battery energy storage system with an
            installed capacity of 2.5 MWh per unit in a 20-foot ISO container. Units are combined in blocks of
            up to sixteen behind a single GC-3000 controller.
            """),
            h2("2. Electrical data"),
            table(["Parameter", "Value"],
                  [["Nominal energy", "2.5 MWh"],
                   ["Usable energy at beginning of life", "2.375 MWh (95% depth of discharge)"],
                   ["Nominal DC voltage", "750 V"],
                   ["DC voltage range", "672–840 V"],
                   ["Continuous power", "1.25 MW"],
                   ["Peak power, 30 seconds", "1.75 MW"],
                   ["Round-trip efficiency", f"{vs2['round_trip_efficiency_pct']}% at 0.5 C, 25 °C"],
                   ["Cycle life", f"{vs2['cycle_life']} cycles to 80% state of health"],
                   ["Response time", "under 100 ms from idle to full power"]]),
            h2("3. Environmental"),
            table(["Parameter", "Value"],
                  [["Operating temperature", "-20 °C to +50 °C"],
                   ["Storage temperature", "-30 °C to +60 °C"],
                   ["Cooling", "liquid, closed loop, glycol mix"],
                   ["Ingress protection", "IP54 enclosure, IP20 internal"],
                   ["Noise at 1 m", "under 68 dB(A) at full load"],
                   ["Altitude", "up to 2,000 m without derating"]]),
            h2("4. Safety and standards"),
            bullets([
                "IEC 62619 and IEC 63056 for cell and system safety.",
                "UN 38.3 for transport.",
                "Gas detection, aerosol suppression and thermal runaway propagation resistance per "
                "UL 9540A test data.",
                "Emergency stop hardwired to the container door and to the GC-3000 protection path.",
            ]),
            h2("5. Interfaces"),
            bullets([
                "IEC 61850 station bus (fibre, 1 Gbit/s) to the GC-3000.",
                "Modbus TCP for third-party SCADA.",
                "Auxiliary supply 400 V AC three phase, 16 A.",
                "Remote monitoring through the Voltara Service Portal over a customer-provided VPN.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "specs/GC3000_Interface_Specification.md",
        "GC-3000 interface specification", "md", tags=["/search"],
        body=md(
            h1("GC-3000 — Interface Specification"),
            kv([("Document", "SPEC-GC3-007"), ("Revision", "C"), ("Date", "2026-05-06"),
                ("Owner", b.who("p_kiss"))]),
            h2("1. Protocols"),
            table(["Protocol", "Role", "Notes"],
                  [["IEC 61850 ed. 2.1", "server", "GOOSE publishing and MMS reporting from v3.0"],
                   ["IEEE 1547-2018", "profile", "Category II ride-through, from v3.0"],
                   ["EN 50549-2", "profile", "Q(U), Q(P) and fixed cos phi"],
                   ["Modbus TCP", "server", "supported in v2.6.x and v3.0"],
                   ["Modbus RTU", "gateway", "supported in v2.6.x only, dropped in v3.0 (see ADR-012)"],
                   ["SNTP", "client", "time synchronisation, mandatory for event correlation"]]),
            h2("2. Timing"),
            para("""
            The v3.0 control loop runs at a 10 ms cycle with a pre-emptive scheduler. The protection path has a
            guaranteed worst-case execution time verified by static analysis. Failover between a redundant
            control pair completes in under 100 ms.
            """),
            h2("3. Security"),
            bullets([
                "Secure boot with a hardware root of trust; only signed firmware images are accepted.",
                "Role-based access with three roles: observer, operator and engineer.",
                "TLS 1.3 for all management traffic; the web interface enforces session rotation on login.",
                "Event log holds 250,000 entries and is exportable in CSV and SCL formats.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "specs/Site_Acceptance_Test_Procedure.md",
        "Site acceptance test procedure SAT-ESB-01", "md", tags=["/search"],
        body=md(
            h1("Site Acceptance Test Procedure — SAT-ESB-01"),
            kv([("Document", "SAT-ESB-01"), ("Revision", "B"), ("Applies to", "Project Helios, Esbjerg"),
                ("Owner", b.who("p_nygaard"))]),
            h2("1. Prerequisites"),
            bullets([
                "Factory acceptance test report signed for every container in the block.",
                "Grid connection permit in force and the transformer energised.",
                "Protection settings loaded and witnessed by the customer.",
                "Site safety induction completed by all attending personnel.",
            ]),
            h2("2. Test sequence"),
            numbered([
                "Insulation and continuity verification on all DC and AC circuits.",
                "Auxiliary systems: cooling, fire detection, gas detection and emergency stop.",
                "Communication: IEC 61850 GOOSE and MMS point-to-point verification with the customer SCADA.",
                "Static functional tests: charge, discharge and idle transitions at 10% steps.",
                "Dynamic tests: frequency response, voltage ride-through and reactive power set points.",
                "Round-trip efficiency measurement at 0.5 C over three consecutive cycles.",
                "72-hour unattended availability run with automatic reporting.",
            ]),
            h2("3. Acceptance criteria"),
            table(["Criterion", "Requirement"],
                  [["Round-trip efficiency", "at least 88.0% measured at the point of common coupling"],
                   ["Availability during the 72-hour run", "at least 99.0%"],
                   ["Response time to a set-point change", "under 200 ms at the block level"],
                   ["Open defects at handover", "no category A or B defects"]]),
            h2("4. Documentation"),
            para("""
            The completed protocol is signed by the Voltara commissioning engineer and the customer
            representative. A signed scan is filed in the legal drive as the provisional acceptance
            certificate. Provisional acceptance starts the 24-month warranty period under clause 11.1.
            """),
        )))

    # ------------------------------------------------------------- service
    # Figures come from the fleet model so this report cannot contradict the
    # monthly fleet reports built from the same events.
    from . import build_fleet
    fl = build_fleet.fleet(b)
    jun = fl["monthly"]["2026-06"]
    ytd = [fl["monthly"]["2026-%02d" % m] for m in range(1, 7)]
    docs.append(Doc(
        DRIVE, "service/Field_Service_Report_2026-06.md",
        "Field service report June 2026", "md", tags=["/report", "/data"],
        body=md(
            h1("Field Service Report — June 2026"),
            kv([("Owner", b.who("p_nygaard")), ("Connected sites", jun["sites_live"]),
                ("Installed capacity", f"{jun['capacity_mwh']} MWh")]),
            h2("Service volume"),
            table(["Metric", "June 2026", "Year to date"],
                  [["Preventive maintenance visits", jun["preventive"],
                    sum(m["preventive"] for m in ytd)],
                   ["Corrective interventions", jun["corrective"],
                    sum(m["corrective"] for m in ytd)],
                   ["Remote resolutions", jun["remote"], sum(m["remote"] for m in ytd)],
                   ["Mean time to respond (hours)", jun["response_hours"],
                    round(sum(m["response_hours"] for m in ytd) / len(ytd), 2)],
                   ["Mean time to repair (hours)", jun["mttr_hours"],
                    round(sum(m["mttr_hours"] for m in ytd) / len(ytd), 2)],
                   ["Unplanned downtime (hours)", jun["downtime_hours"],
                    round(sum(m["downtime_hours"] for m in ytd), 1)],
                   ["Fleet availability", f"{jun['availability_pct']}%",
                    "%.2f%%" % (sum(m["availability_pct"] for m in ytd) / len(ytd))]]),
            h2("Notable interventions"),
            bullets([
                "Site DK-014: coolant pump replaced under warranty after a bearing failure at 19,400 hours.",
                "Site DE-007: false gas detection alarm traced to a sensor calibration drift; all sensors of "
                "the same batch were recalibrated remotely.",
                "Site HU-002: customer-side auxiliary supply interruption, no Voltara fault.",
            ]),
            h2("Spare parts"),
            para("""
            Spare part availability at the München hub was 94% against a target of 95%. The two stock-outs were
            coolant pumps, now covered by a raised minimum stock level of six units.
            """),
        )))

    docs.append(Doc(
        DRIVE, "service/Commissioning_Checklist.md",
        "Commissioning checklist", "md", tags=["/search"],
        body=md(
            h1("Commissioning Checklist — VoltStack 2 with GC-3000"),
            kv([("Document", "CHK-COM-03"), ("Revision", "E"), ("Owner", b.who("p_nygaard"))]),
            h2("Before energisation"),
            bullets([
                "Confirm the grid connection permit is in force and copy it to the project file.",
                "Verify torque marks on all DC busbar connections.",
                "Confirm the earthing resistance is below 1 ohm.",
                "Load the site-specific protection settings and record the checksum.",
            ]),
            h2("At energisation"),
            bullets([
                "Energise auxiliaries first, then the DC bus, then release the inverter.",
                "Record the ambient temperature, DC voltage and insulation resistance.",
                "Verify the emergency stop from both the container door and the control room.",
            ]),
            h2("After energisation"),
            bullets([
                "Run the 72-hour availability test and archive the report.",
                "Register the site in the Voltara Service Portal and enable remote monitoring.",
                "Hand over the as-built documentation pack and the operator training record.",
            ]),
        )))

    # ------------------------------------------------------------ incidents
    inc = d["compliance"]["incidents"]
    docs.append(Doc(
        DRIVE, "security/Incident_Log_2026.md",
        "Information security incident log 2026", "md", tags=["/compliance"],
        body=md(
            h1("Information Security Incident Log — 2026"),
            kv([("Owner", b.who("p_toth")), ("Reviewed by", b.who("p_brandt")),
                ("Classification", "Internal")]),
            *[md(h2(f"{i['id']} — {i['category']}"),
                 kv([("Date", i["date"]), ("Closed", i.get("closed", "open")),
                     ("Description", i["description"]), ("Impact", i["impact"])]),
                 (para("Personal data breach notification: not made. " + i["rationale"])
                  if "rationale" in i else ""),
                 (bullets(i["actions"]) if i.get("actions") else ""))
              for i in inc],
            h2("Summary"),
            para(f"""
            Two incidents were recorded in the first half of 2026, both closed. No incident met the threshold
            for supervisory authority notification under Article 33 of the GDPR. Multi-factor authentication
            was made mandatory for all users on 15 April 2026 as a direct result of {inc[0]['id']}.
            """),
        )))

    # ------------------------------------------------------------- Tundra
    docs.append(Doc(
        DRIVE, "tundra/TUNDRA-STORE_D1.1_Requirements.pdf",
        "TUNDRA-STORE deliverable D1.1 — requirements report", "pdf", tags=["/strategy"],
        blocks=[
            ("h2", "Document information"),
            ("", f"Grant agreement: {tundra['grant_agreement']}. Programme: {tundra['programme']}."),
            ("", f"Coordinator: {tundra['coordinator']}. Partners: {tundra['consortium_partners']}."),
            ("", f"Deliverable D1.1, due 30 June 2026, submitted 26 June 2026. "
                 f"Voltara lead author: {b.who('p_petrova')}. Voltara coordinator: {b.who('p_weber')}."),
            ("h2", "1. Objective"),
            ("", "TUNDRA-STORE investigates the performance and degradation of lithium iron phosphate "
                 "storage systems at ambient temperatures below minus twenty degrees Celsius, with the aim "
                 "of producing a validated degradation model and a set of design guidelines for "
                 "cold-climate installations."),
            ("h2", "2. Requirements"),
            ("", "R-01 The degradation model shall predict capacity fade within 3% absolute error over "
                 "4,000 equivalent full cycles."),
            ("", "R-02 The test programme shall cover ambient temperatures of minus 30, minus 20 and "
                 "minus 10 degrees Celsius."),
            ("", "R-03 Thermal management strategies shall be evaluated for energy overhead below 4% of "
                 "throughput."),
            ("", "R-04 Design guidelines shall be expressed so that they can be applied to containerised "
                 "systems without structural modification."),
            ("", "R-05 All measurement data shall be published under an open licence within six months of "
                 "the end of the relevant work package."),
            ("h2", "3. Voltara contribution"),
            ("", f"Voltara leads work package 3 with a budget share of "
                 f"EUR {tundra['voltara_share_meur']} million out of a total grant of "
                 f"EUR {tundra['grant_value_meur']} million. The project started on {tundra['start']} and "
                 f"runs for {tundra['duration_months']} months."),
            ("h2", "4. Next milestones"),
            ("", "First periodic report: 28 February 2027. Cold chamber test campaign: from November 2026 "
                 "at the partner facility in Trondheim."),
        ]))

    # ------------------------------------------------------------ platform
    plat = d["platform"]["ai_box"]
    docs.append(Doc(
        DRIVE, "platform/AI_Box_Platform_Status_2026-07.md",
        "AI Box platform status July 2026", "md", tags=["/data"],
        body=md(
            h1("ViVeSec AI Box — Platform Status, July 2026"),
            kv([("Vendor", plat["vendor"]), ("Deployment", plat["deployment"]),
                ("Go-live", plat["go_live"]), ("Platform owner", b.who("p_toth"))]),
            h2("Usage"),
            table(["Metric", "Value"],
                  [["Indexed drives", plat["drives_indexed"]],
                   ["Indexed documents", plat["documents_indexed"]],
                   ["Enabled users", plat["users_enabled"]],
                   ["Queries in July 2026", 1_842],
                   ["Median response time", "1.9 s"],
                   ["95th percentile response time", "3.4 s"],
                   ["Answers with at least one citation", "97.1%"]]),
            h2("Data residency"),
            para("""
            All processing takes place on customer-controlled infrastructure in the Budapest data room. No
            document content leaves the premises, in line with clause 9.1 of the platform agreement. The
            appliance has no outbound internet dependency for inference.
            """),
            h2("Access control"),
            para("""
            Drive-level access control mirrors the file server groups. A user only receives answers from
            drives their group can read, and the citation list is filtered the same way. Access changes take
            effect at the next index synchronisation, which runs every fifteen minutes.
            """),
            h2("Open items"),
            bullets([
                "Scanned documents without a text layer are indexed but not searchable; optical character "
                "recognition is not yet enabled.",
                "The legal drive was added last and is still being backfilled.",
                "Usage reporting per department is planned for September 2026.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "platform/Data_Inventory_2026.md",
        "Data inventory 2026", "md", tags=["/data", "/compliance"],
        body=md(
            h1("Corporate Data Inventory — 2026"),
            kv([("Owner", b.who("p_toth")), ("Reviewed by", b.who("p_brandt")),
                ("Last update", "2026-07-10")]),
            h2("Repositories"),
            table(["Repository", "Content", "Classification", "Retention", "Owner"],
                  [["File server, public drive", "Company material for all staff", "Internal",
                    "7 years", b.name("p_somogyi")],
                   ["File server, engineering drive", "Specifications, tests, project files", "Internal",
                    "10 years", b.name("p_halasz")],
                   ["File server, finance drive", "Accounts, budgets, invoices", "Confidential",
                    "8 years (statutory)", b.name("p_novak")],
                   ["File server, HR drive", "Personnel files, pay data", "Confidential — personal data",
                    "As per retention schedule", b.name("p_kovacs")],
                   ["File server, legal drive", "Contracts, audits, compliance", "Confidential",
                    "10 years after expiry", b.name("p_brandt")],
                   ["Northbridge ERP", "Ledgers, orders, invoices", "Confidential",
                    "8 years (statutory)", b.name("p_novak")],
                   ["Voltara Service Portal", "Site telemetry and service records", "Internal",
                    "5 years", b.name("p_nygaard")],
                   ["ViVeSec AI Box index", "Derived index of the five drives", "Mirrors source",
                    "Rebuilt on demand", b.name("p_toth")]]),
            h2("Personal data"),
            para("""
            Personal data is held in the HR drive, in the ERP payroll interface and in the service portal
            contact records. The record of processing activities was last updated on 30 April 2026 and is
            maintained by the data protection officer.
            """),
            h3("Derived indexes"),
            para("""
            The AI Box index is a derived copy of source documents. It inherits the access control of the
            source drive and is rebuilt from source rather than backed up separately, so no additional
            retention obligation arises.
            """),
        )))

    docs.append(Doc(
        DRIVE, "memos/Decision_Memo_Alternative_Cell_Supplier.md",
        "Decision memo — alternative cell supplier", "md", tags=["/memo"],
        body=md(
            h1("Decision Memo — Qualification of an Alternative Cell Supplier"),
            kv([("To", "Executive Committee"), ("From", b.who("p_varga")),
                ("Contributors", f"{b.name('p_petrova')}, {b.name('p_novak')}"),
                ("Date", "2026-07-24"), ("Decision required by", "2026-08-29")]),
            h2("Background"),
            para("""
            Nordcell Technologies is the single qualified cell supplier under a framework agreement signed on
            5 November 2024 with a value of EUR 18.5 million over three years. The committed lead time under
            clause 7.1 is 16 weeks from call-off. The second Helios batch is now seven weeks late, which moves
            provisional acceptance from 30 November 2026 to 22 January 2027 and creates a delay damages
            exposure of EUR 0.50 million.
            """),
            h2("Options"),
            table(["Option", "Cost", "Lead time", "Assessment"],
                  [["A — stay single-sourced with Nordcell", "EUR 0", "16 weeks nominal",
                    "No qualification cost, but the concentration risk remains and recurs on every project."],
                   ["B — qualify LM Cells ApS as a second source", "EUR 210,000 over 5 months",
                    "18 weeks nominal", "Removes the single point of failure; cells are 4% more expensive."],
                   ["C — dual-source with a volume split of 70/30", "EUR 210,000 plus 1.2% unit cost",
                    "16–18 weeks", "Keeps Nordcell pricing leverage while maintaining a live second source."]]),
            h2("Recommendation"),
            para("""
            Option C. The qualification cost is the same as option B, but holding a live 30% volume share with
            the second supplier keeps the qualification current and preserves negotiating position on the
            Nordcell renewal in 2027. LM Cells passed technical pre-qualification on 29 June 2026.
            """),
            h2("Contractual position"),
            para("""
            The Nordcell framework agreement does not grant exclusivity. Clause 7.1 entitles Voltara to a 2%
            credit per week of delay capped at 8%; a claim of EUR 0.37 million was served on 30 July 2026.
            """),
        )))
    # ---------------------------------------------------- type approval dossier
    docs.append(Doc(
        DRIVE, "meridian/Meridian_Type_Approval_Dossier_Index.md",
        "GC-3000 v3.0 type approval dossier — index", "md", tags=["/tracking", "/compliance"],
        body=md(
            h1("GC-3000 Firmware v3.0 — Type Approval Dossier Index"),
            kv([("Certification body", "TÜV Süd"),
                ("Submission due", "2026-10-15"),
                ("Assembly status", "70% at 2026-08-03"),
                ("Owner", b.who("p_kiss")), ("Approver", b.who("p_lehmann"))]),
            h2("Dossier contents"),
            table(["Section", "Document", "Status", "Owner"],
                  [["1", "Product description and configuration list", "complete", b.name("p_kiss")],
                   ["2", "Grid-code compliance matrix (EN 50549-2, IEEE 1547-2018)", "complete",
                    b.name("p_kiss")],
                   ["3", "IEC 61850 conformance test report", "complete", b.name("p_kiss")],
                   ["4", "Protection function test evidence", "in progress", b.name("p_halasz")],
                   ["5", "Endurance and regression test log (June 2026)", "complete", b.name("p_kiss")],
                   ["6", "Cybersecurity concept per IEC 62443-4-1", "in progress", b.name("p_toth")],
                   ["7", "Software life-cycle and change control evidence", "complete", b.name("p_kiss")],
                   ["8", "Risk assessment and residual risk statement", "not started",
                    b.name("p_brandt")],
                   ["9", "Manufacturer declaration and manuals", "not started", b.name("p_brandt")]]),
            h2("Critical path"),
            para("""
            Section 8 depends on the completion of section 6. The risk assessment cannot be signed before the
            cybersecurity concept is final, and both must precede the manufacturer declaration. The internal
            deadline for sections 6 and 8 is 20 September 2026, which leaves three weeks of float before the
            submission date.
            """),
            h2("Dependencies outside the project"),
            bullets([
                "Laboratory slot at TÜV Süd is reserved for the week of 2 November 2026.",
                "Two engineers are shared with Helios commissioning support; two protected days per week "
                "were agreed for dossier work until submission.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "specs/GC3000_Cybersecurity_Concept.md",
        "GC-3000 cybersecurity concept (IEC 62443)", "md", tags=["/compliance", "/search"],
        body=md(
            h1("GC-3000 — Cybersecurity Concept"),
            kv([("Document", "SEC-GC3-003"), ("Revision", "B (draft)"),
                ("Reference standard", "IEC 62443-4-1 and 62443-4-2"),
                ("Owner", b.who("p_toth")), ("Technical reviewer", b.who("p_kiss"))]),
            h2("1. Security context"),
            para("""
            The controller sits inside the customer's operational technology network behind the site
            firewall. It is not directly reachable from the internet. Remote access is possible only through
            an outbound connection established by the site gateway to the Voltara Service Portal.
            """),
            h2("2. Security level target"),
            table(["Foundational requirement", "Target level", "Implemented in"],
                  [["Identification and authentication control", "SL 2", "v2.6.4 and v3.0"],
                   ["Use control", "SL 2", "v2.6.4 and v3.0"],
                   ["System integrity", "SL 2", "v3.0 (secure boot, signed images)"],
                   ["Data confidentiality", "SL 2", "v3.0 (TLS 1.3 for management traffic)"],
                   ["Restricted data flow", "SL 1", "customer network segmentation"],
                   ["Timely response to events", "SL 2", "v3.0 (250,000-entry event log, export)"],
                   ["Resource availability", "SL 2", "v3.0 (redundant control pair)"]]),
            h2("3. Secure development"),
            bullets([
                "Threat modelling per release, recorded in the architecture decision log.",
                "Static analysis gate in the build; the build fails on a new high-severity finding.",
                "Third-party component inventory with vulnerability monitoring.",
                "Signed firmware images; the bootloader accepts no unsigned image.",
            ]),
            h2("4. Known residual risks"),
            bullets([
                "The Modbus RTU gateway in v2.6.x has no authentication; it is dropped in v3.0.",
                "Field upgrade from v2.6.x to v3.0 requires a service visit because the bootloader changes.",
                "Physical access to the cabinet remains the customer's responsibility.",
            ]),
            h2("5. Vulnerability handling"),
            para("""
            Reports are received through the security contact address and triaged within five business days.
            A public vulnerability disclosure policy is a NIS2 programme item due on 15 December 2026 and is
            not yet published.
            """),
        )))

    docs.append(Doc(
        DRIVE, "projects/Helios_Site_Survey_Esbjerg.md",
        "Helios site survey report — Esbjerg", "md", tags=["/search", "/tracking"],
        body=md(
            h1("Site Survey Report — Esbjerg, Project Helios"),
            kv([("Survey date", "2025-11-18"), ("Issued", "2025-11-27"),
                ("Surveyor", b.who("p_nygaard")), ("Customer contact", "Vestkraft A/S, site management")]),
            h2("1. Site"),
            para("""
            The site is a fenced compound adjacent to the existing 60/10 kV substation, with a usable area of
            approximately 1,850 square metres. Ground conditions are sandy with a stable bearing capacity;
            the geotechnical report confirms that standard strip foundations are sufficient.
            """),
            h2("2. Layout"),
            table(["Item", "Finding"],
                  [["Container positions", "16 positions in two rows of eight, 3 m service aisle"],
                   ["Transformer position", "north-east corner, adjacent to the customer switchgear"],
                   ["Cable route", "82 m from the transformer to the first container row"],
                   ["Access road", "existing, 4 m wide, adequate for a 20-foot container trailer"],
                   ["Crane standing", "confirmed on the south side, load spreading required"]]),
            h2("3. Utilities"),
            bullets([
                "Auxiliary supply available from the customer's low-voltage board; the rating was later "
                "increased under change request CR-HEL-07.",
                "Water for the cooling system top-up is available at the site building.",
                "Fibre for the station bus is to be laid in the same trench as the power cabling.",
            ]),
            h2("4. Constraints"),
            bullets([
                "Noise limit at the site boundary is 45 dB(A) at night; the VoltStack 2 rating of 68 dB(A) "
                "at one metre is compliant at the boundary distance of 34 metres.",
                "Outdoor commissioning is limited from mid-December by weather and daylight.",
                "The grid connection permit was not final at the time of the survey; this became risk "
                "R-HEL-07.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "projects/Regensburg_Project_Plan.md",
        "Stadtwerke Regensburg — project plan", "md", tags=["/tracking"],
        body=md(
            h1("Project Plan — Stadtwerke Regensburg, 8 MWh Storage"),
            kv([("Contract signed", "2026-04-22"), ("Value", "EUR 3.2 million"),
                ("Project manager", b.who("p_erdelyi")), ("Delivery lead", b.who("p_ruhland")),
                ("Provisional acceptance", "2027-02-26"), ("Status", "green")]),
            h2("Scope"),
            para("""
            Supply, installation and commissioning of a 8 MWh battery energy storage system at the
            Regensburg-Haslbach site: four VoltStack 2 units under GC-3000 control, including the grid
            connection documentation to EN 50549-2.
            """),
            h2("Schedule"),
            table(["Milestone", "Date", "Status"],
                  [["Design freeze", "2026-08-28", "on track"],
                   ["Cell call-off", "2026-09-11", "on track"],
                   ["Factory acceptance test", "2026-10-19", "on track (line slot reserved)"],
                   ["Delivery to site", "2026-11-30", "on track"],
                   ["Commissioning start", "2027-01-12", "on track"],
                   ["Provisional acceptance", "2027-02-26", "on track"]]),
            h2("Commercial terms"),
            table(["Term", "Value"],
                  [["Payment", "30% on order, 50% on delivery, 20% on acceptance, 30 days net"],
                   ["Delay damages", "0.5% per commenced week, capped at 5% of the contract price"],
                   ["Warranty", "24 months from provisional acceptance"],
                   ["Governing law", "German law, jurisdiction München"]]),
            h2("Risks"),
            bullets([
                "Factory acceptance test slot overlaps with the Helios recovery schedule; the line time was "
                "reserved from 5 October to protect both.",
                "The delay damages cap here is 5 percent, lower than the 10 percent in the Vestkraft "
                "agreement, so the exposure is smaller but the trigger is the same.",
                "Firmware v3.0 is not required for this project; version 2.6.4 satisfies the grid code.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "tundra/Tundra_WP3_Test_Plan.md",
        "TUNDRA-STORE work package 3 test plan", "md", tags=["/strategy", "/tracking"],
        body=md(
            h1("TUNDRA-STORE — Work Package 3 Test Plan"),
            kv([("Work package", "WP3 — system-level thermal management"),
                ("Lead partner", "Voltara Energy Group"),
                ("Technical lead", b.who("p_petrova")),
                ("Coordinator contact", b.who("p_weber")),
                ("Test facility", "Partner cold chamber, Trondheim"),
                ("Campaign window", "from November 2026")]),
            h2("Objective"),
            para("""
            Quantify the energy overhead of three thermal management strategies at ambient temperatures of
            minus 10, minus 20 and minus 30 degrees Celsius, and produce the data set that feeds the
            degradation model in work package 2.
            """),
            h2("Test matrix"),
            table(["Strategy", "Ambient", "Charge rate", "Cycles", "Measured"],
                  [["Continuous pre-heat", "-10 / -20 / -30 °C", "0.25 C", 60,
                    "energy overhead, capacity, internal resistance"],
                   ["On-demand pre-heat", "-10 / -20 / -30 °C", "0.25 C", 60,
                    "energy overhead, time to ready, capacity"],
                   ["Self-heating by pulsed current", "-20 / -30 °C", "0.25 C", 40,
                    "energy overhead, cell temperature spread, degradation"]]),
            h2("Acceptance criteria"),
            bullets([
                "Energy overhead below 4 percent of throughput, per requirement R-03.",
                "Cell-to-cell temperature spread within 6 K during heating.",
                "No measurable lithium plating signature after the campaign.",
            ]),
            h2("Deliverables"),
            table(["Deliverable", "Due"],
                  [["D3.1 Test plan (this document)", "2026-09-30"],
                   ["D3.2 Measurement data set, open access", "2027-06-30"],
                   ["D3.3 Design guidelines for containerised systems", "2027-12-31"]]),
            h2("Note on scope"),
            para("""
            The project produces design guidelines and a data set. It does not commit Voltara to launch a
            cold-climate product variant; that decision sits with the product roadmap.
            """),
        )))

    ecr_lines = ["ecr_id,date,product,title,raised_by,reason,status,effect_on_cost_eur,approved_by"]
    for ecr, date, product, title, raiser, reason, status, cost, approver in [
        ("ECR-2026-014", "2026-02-09", "VoltStack 2", "Coolant pump supplier change", "p_ruhland",
         "obsolescence", "implemented", 0, "p_lehmann"),
        ("ECR-2026-017", "2026-03-03", "GC-3000", "Session identifier rotation on privilege change",
         "p_toth", "security finding PT-2025-H2", "implemented", 0, "p_kiss"),
        ("ECR-2026-021", "2026-03-24", "VoltStack 2", "Gas sensor batch recalibration procedure",
         "p_nygaard", "field observation", "implemented", 4200, "p_ruhland"),
        ("ECR-2026-026", "2026-04-28", "Helios", "Auxiliary transformer 400 to 630 kVA", "p_nygaard",
         "customer request CR-HEL-07", "implemented", 46800, "p_holm"),
        ("ECR-2026-031", "2026-05-14", "GC-3000", "Drop Modbus RTU gateway in v3.0", "p_kiss",
         "timing budget, ADR-012", "implemented", 0, "p_lehmann"),
        ("ECR-2026-035", "2026-06-02", "VoltStack 2", "Alternative cell qualification, LM Cells",
         "p_varga", "single-source risk", "in progress", 210000, "pending"),
        ("ECR-2026-038", "2026-06-23", "GC-3000", "Event log capacity 50k to 250k entries", "p_kiss",
         "customer requirement", "implemented", 0, "p_kiss"),
        ("ECR-2026-042", "2026-07-16", "VoltStack 2", "Minimum coolant pump stock level 4 to 6",
         "p_nygaard", "service availability", "implemented", 9800, "p_holm"),
    ]:
        ecr_lines.append("%s,%s,%s,%s,%s,%s,%s,%d,%s"
                         % (ecr, date, product, title, b.name(raiser), reason, status, cost,
                            b.name(approver) if approver != "pending" else "pending"))
    docs.append(Doc(
        DRIVE, "change/Engineering_Change_Log.csv", "Engineering change request log 2026", "csv",
        tags=["/tracking", "/compliance"], body="\n".join(ecr_lines)))

    docs.append(Doc(
        DRIVE, "projects/Lessons_Learned_2025.md", "Lessons learned — 2025 projects", "md",
        tags=["/memo", "/summary"],
        body=md(
            h1("Lessons Learned — 2025 Delivery Projects"),
            kv([("Workshop date", "2026-01-29"), ("Facilitator", b.who("p_holm")),
                ("Participants", "delivery, engineering, procurement and finance leads")]),
            h2("What went well"),
            bullets([
                "Factory acceptance testing before shipment eliminated site rework on all three projects.",
                "Standardising the commissioning checklist cut the average commissioning time by four days.",
                "Early involvement of finance in milestone definition improved cash timing.",
            ]),
            h2("What did not"),
            table(["Issue", "Consequence", "Action taken"],
                  [["Single qualified cell supplier", "No fallback when the supplier slipped",
                    "Second source qualification started (ECR-2026-035)"],
                   ["Grid connection permits assumed, not verified",
                    "Two projects waited on the customer's permit",
                    "Permit status is now a gate in the site survey"],
                   ["Change requests priced late",
                    "Margin erosion on two variations",
                    "Change requests are now priced within five working days"],
                   ["Documentation pack assembled at the end",
                    "Handover delayed by up to two weeks",
                    "Documentation is now built up milestone by milestone"]]),
            h2("Carried into 2026"),
            para("""
            The single-source lesson was the most expensive one and is exactly what materialised again on
            Project Helios. The dual sourcing decision due on 29 August 2026 closes it.
            """),
        )))

    docs.append(Doc(
        DRIVE, "procurement/Supplier_Qualification_LM_Cells.md",
        "Supplier qualification report — LM Cells ApS", "md", tags=["/memo", "/compliance"],
        body=md(
            h1("Supplier Qualification Report — LM Cells ApS"),
            kv([("Supplier", "LM Cells ApS, Denmark"), ("Assessment", "technical pre-qualification"),
                ("Completed", "2026-06-29"), ("Result", "PASSED"),
                ("Assessor", b.who("p_petrova")), ("Commercial owner", b.who("p_varga"))]),
            h2("Scope"),
            para("""
            Qualification of a second source for lithium iron phosphate cells for the VoltStack 2 platform,
            to remove the single point of failure identified on Project Helios.
            """),
            h2("Technical assessment"),
            table(["Criterion", "Requirement", "Result"],
                  [["Cell chemistry and format", "LFP, prismatic, compatible module fit", "pass"],
                   ["Capacity retention", "at least 80% after 6,000 equivalent full cycles",
                    "pass (supplier data, 6,400 cycles)"],
                   ["Safety standards", "IEC 62619, UN 38.3", "pass, certificates provided"],
                   ["Low-temperature behaviour", "charge acceptance at -10 °C", "pass with derating"],
                   ["Production capacity", "at least 30% of annual demand", "pass"],
                   ["Committed lead time", "18 weeks from call-off", "accepted"]]),
            h2("Commercial position"),
            para("""
            Unit prices are approximately 4 percent above the incumbent. A 70/30 volume split keeps the
            weighted cost increase at about 1.2 percent while maintaining a live second source.
            """),
            h2("Open items"),
            bullets([
                "Supplier security assessment is OUTSTANDING (tracked under ISO 27001 finding NC-2026-02).",
                "Qualification build of one full module is scheduled for September 2026.",
                "Framework terms are drafted but not signed pending the decision due 29 August 2026.",
            ]),
        )))

    spare_lines = ["part_no,description,location,min_stock,on_hand,on_order,lead_time_weeks,unit_cost_eur"]
    for part, desc, loc, mn, hand, order, lead, cost in [
        ("VS2-CLP-001", "Coolant pump assembly", "München hub", 6, 6, 2, 8, 1840),
        ("VS2-FAN-004", "Condenser fan module", "München hub", 4, 5, 0, 6, 620),
        ("VS2-GSN-002", "Gas detection sensor", "München hub", 10, 12, 0, 4, 310),
        ("VS2-FLT-007", "Coolant filter cartridge", "München hub", 20, 27, 0, 2, 48),
        ("GC3-PSU-001", "Redundant 24 V power supply", "München hub", 6, 4, 4, 5, 290),
        ("GC3-CPU-002", "Controller main board", "München hub", 3, 3, 0, 12, 2450),
        ("VS2-CNT-011", "Contactor, 1000 V DC", "København depot", 4, 4, 0, 9, 780),
        ("VS2-BMS-003", "Module management board", "København depot", 8, 6, 4, 10, 410),
        ("VS2-SEAL-01", "Door seal kit", "København depot", 6, 9, 0, 3, 95),
        ("GC3-SFP-001", "Fibre transceiver, 1 Gbit/s", "Budapest office", 10, 14, 0, 2, 68),
    ]:
        spare_lines.append("%s,%s,%s,%d,%d,%d,%d,%d" % (part, desc, loc, mn, hand, order, lead, cost))
    docs.append(Doc(
        DRIVE, "service/Spare_Parts_Inventory.csv", "Spare parts inventory", "csv",
        tags=["/data", "/report"], body="\n".join(spare_lines)))

    docs.append(Doc(
        DRIVE, "specs/Grid_Code_Compliance_Matrix.xlsx", "Grid code compliance matrix", "xlsx",
        tags=["/compliance", "difficulty:xlsx"],
        sheets={
            "Compliance": [
                ["Market", "Standard", "Requirement", "v2.6.4", "v3.0", "Evidence"],
                ["Denmark", "EN 50549-2", "Q(U) reactive control", "yes", "yes", "SAT-ESB-01"],
                ["Denmark", "Danish grid code", "Frequency response", "yes", "yes", "SAT-ESB-01"],
                ["Germany", "EN 50549-2", "Fixed cos phi", "yes", "yes", "TÜV report 2025"],
                ["Germany", "VDE-AR-N 4110", "Fault ride-through", "partial", "yes",
                 "type approval dossier section 2"],
                ["Sweden", "EN 50549-2", "Q(P) reactive control", "yes", "yes", "TÜV report 2025"],
                ["Sweden", "SvK requirements", "IEEE 1547-2018 category II", "no", "yes",
                 "type approval dossier section 2"],
                ["Hungary", "EN 50549-2", "Voltage control", "yes", "yes", "TÜV report 2025"],
                ["Austria", "TOR Erzeuger Typ B", "Reactive power range", "yes", "yes",
                 "TÜV report 2025"],
                ["All", "IEC 61850 ed. 2.1", "GOOSE and MMS server", "no", "yes",
                 "conformance test report"],
            ],
            "Notes": [["Note"],
                      ["'partial' means the function exists but is not certified for that market."],
                      ["v3.0 entries are pending type approval; submission is due 15 October 2026."],
                      ["The Nordkraft opportunity in Sweden depends on the IEEE 1547-2018 row."]],
        }))

    docs.append(Doc(
        DRIVE, "security/Backup_and_Recovery_Plan.md", "Backup and recovery plan", "md",
        tags=["/compliance", "/data"],
        body=md(
            h1("Backup and Recovery Plan"),
            kv([("Document", "PROC-BCP-04"), ("Revision", "C"), ("Owner", b.who("p_toth")),
                ("Last test", "2026-05-27"), ("Next test", "2026-11-30")]),
            h2("Scope and objectives"),
            table(["System", "RPO", "RTO", "Backup method", "Retention"],
                  [["Northbridge ERP", "1 hour", "4 hours", "vendor managed, replicated", "35 days"],
                   ["File server drives", "4 hours", "8 hours", "snapshot plus offsite copy", "90 days"],
                   ["Voltara Service Portal", "1 hour", "4 hours", "database replication", "30 days"],
                   ["Source control and build", "24 hours", "24 hours", "mirrored repository", "unlimited"],
                   ["AI Box document index", "n/a", "8 hours", "rebuilt from source documents", "n/a"]]),
            h2("Note on the document index"),
            para("""
            The AI Box index is a derived copy of the source drives. It is not backed up separately; recovery
            is a re-index from the source documents, which takes approximately three minutes per hundred
            documents on the appliance. This also means the index inherits the retention of the source.
            """),
            h2("Test results, May 2026"),
            bullets([
                "File server restore of a 40 GB drive completed in 2 hours 51 minutes, within the objective.",
                "ERP restore was performed by the vendor in a sandbox and met the objective.",
                "The service portal failover test revealed a stale DNS entry, corrected on 28 May 2026.",
                "A full business continuity test for the service portal is a NIS2 gap item due "
                "30 November 2026.",
            ]),
        )))
    return docs
