"""Finance drive: management accounts, budget, forecast, sales & exec material."""
from __future__ import annotations

from .common import Bible, Doc, bullets, h1, h2, kv, md, numbered, para, table

DRIVE = "drive_finance"


def build(b: Bible) -> list[Doc]:
    d = b.d
    fin = d["finance"]
    q1, q2 = b.quarters["Q1 2026"], b.quarters["Q2 2026"]
    q4_25 = b.quarters["Q4 2025"]
    h1_26 = fin["half_year_2026"]
    bal = fin["balance_and_cash"]
    fy25 = b.years[2025]
    fy24 = b.years[2024]
    fy26 = b.years[2026]
    docs: list[Doc] = []

    # ---------------------------------------------------------------- Q2 2026
    docs.append(Doc(
        DRIVE, "reports/Q2_2026_Management_Accounts.md",
        "Q2 2026 Management Accounts", "md", tags=["/finance", "/report"],
        facts=["finance.quarterly.Q2 2026"],
        body=md(
            h1("Q2 2026 Management Accounts — Voltara Energy Group"),
            kv([("Reporting period", "1 April 2026 – 30 June 2026"),
                ("Prepared by", b.who("p_novak")),
                ("Approved by", b.who("p_sorensen")),
                ("Status", "Management accounts, unaudited"),
                ("Issued", "2026-07-21")]),
            h2("1. Revenue"),
            para(f"""
            Group revenue for Q2 2026 was EUR {q2['revenue_meur']} million against a plan of
            EUR {q2['plan_revenue_meur']} million, a variance of {q2['variance_pct']}%. First-half revenue
            reached EUR {h1_26['revenue_meur']} million, exactly in line with the half-year plan of
            EUR {h1_26['plan_revenue_meur']} million, because the Q1 overachievement of
            {q1['variance_pct']}% offset the Q2 shortfall.
            """),
            table(["Line", "Q1 2026", "Q2 2026", "H1 2026", "H1 plan"],
                  [["Revenue (EUR m)", q1["revenue_meur"], q2["revenue_meur"],
                    h1_26["revenue_meur"], h1_26["plan_revenue_meur"]],
                   ["Gross margin", f"{q1['gross_margin_pct']}%", f"{q2['gross_margin_pct']}%",
                    f"{h1_26['gross_margin_pct']}%", "44.0%"],
                   ["Gross profit (EUR m)", q1["gross_profit_meur"], q2["gross_profit_meur"],
                    round(q1["gross_profit_meur"] + q2["gross_profit_meur"], 2), "12.28"],
                   ["Operating expenses (EUR m)", q1["opex_meur"], q2["opex_meur"],
                    h1_26["opex_meur"], "9.60"],
                   ["EBITDA (EUR m)", q1["ebitda_meur"], q2["ebitda_meur"],
                    h1_26["ebitda_meur"], "2.68"],
                   ["EBITDA margin", f"{q1['ebitda_margin_pct']}%", f"{q2['ebitda_margin_pct']}%",
                    f"{h1_26['ebitda_margin_pct']}%", "9.6%"]]),
            h2("2. Gross margin"),
            para(f"""
            Gross margin fell to {q2['gross_margin_pct']}% in Q2 2026 from {q1['gross_margin_pct']}% in Q1.
            Two effects explain the decline. First, the lithium cell price indexation under the Nordcell
            framework agreement (clause 6.4, capped at plus or minus 6% per calendar year) was applied in
            full from 1 April 2026. Second, the warranty provision was increased by EUR 0.18 million
            following the revised commissioning schedule on Project Helios.
            """),
            h2("3. Variance commentary"),
            para(q2["variance_commentary"]),
            h2("4. Operating expenses"),
            para(f"""
            Operating expenses were EUR {q2['opex_meur']} million, up from EUR {q1['opex_meur']} million in Q1.
            The increase is driven by R&D headcount on Project Meridian, the external programme management
            cost of Project Anchor, and the first full quarter of the ViVeSec AI Box subscription
            (EUR {b.contracts['con_vivesec']['annual_fee_eur']:,} per year, charged to IT & Security).
            """),
            h2("5. Cash and balance sheet"),
            kv([("Cash at 30 June 2026", f"EUR {bal['cash_meur']} million"),
                ("Total debt", f"EUR {bal['total_debt_meur']} million"),
                ("Net debt", f"EUR {bal['net_debt_meur']} million (net cash)"),
                ("Order backlog", f"EUR {bal['order_backlog_meur']} million"),
                ("Inventory", f"EUR {bal['inventory_meur']} million"),
                ("DSO", f"{bal['dso_days']} days"),
                ("DPO", f"{bal['dpo_days']} days")]),
            h2("6. Outlook"),
            para(f"""
            Management maintains the full-year 2026 plan of EUR {fy26['plan_revenue_meur']} million revenue
            and EUR {fy26['plan_ebitda_meur']} million EBITDA, but flags the Helios provisional acceptance
            date as the single largest swing factor. See the H2 2026 forecast document for the downside case.
            """),
        )))

    # ---------------------------------------------------------------- Q1 2026
    docs.append(Doc(
        DRIVE, "reports/Q1_2026_Management_Accounts.md",
        "Q1 2026 Management Accounts", "md", tags=["/finance"],
        body=md(
            h1("Q1 2026 Management Accounts — Voltara Energy Group"),
            kv([("Reporting period", "1 January 2026 – 31 March 2026"),
                ("Prepared by", b.who("p_novak")),
                ("Status", "Management accounts, unaudited"),
                ("Issued", "2026-04-20")]),
            h2("Headline figures"),
            table(["Metric", "Q1 2026", "Plan", "Variance"],
                  [["Revenue (EUR m)", q1["revenue_meur"], q1["plan_revenue_meur"],
                    f"+{q1['variance_pct']}%"],
                   ["Gross margin", f"{q1['gross_margin_pct']}%", "44.0%", "+0.6pp"],
                   ["Operating expenses (EUR m)", q1["opex_meur"], "4.62", "-0.9%"],
                   ["EBITDA (EUR m)", q1["ebitda_meur"], "1.21", "+8.3%"]]),
            h2("Commentary"),
            para(f"""
            Revenue of EUR {q1['revenue_meur']} million was {q1['variance_pct']}% ahead of plan, driven by the
            early delivery of the first Nordcell cell batch on Project Helios (11 April, four days ahead of the
            contractual milestone) and by stronger service revenue in the Nordic region. Gross margin of
            {q1['gross_margin_pct']}% benefited from a favourable product mix: VoltStack 2 units carried a higher
            share of the quarter than service and spare parts.
            """),
            para("""
            Operating expenses came in slightly below plan because two engineering positions in Budapest were
            filled in April rather than February. No exceptional items were recorded in the quarter.
            """),
            h2("Covenant position"),
            para("""
            The Danubia Bank term loan covenant (net debt to EBITDA not exceeding 3.0x, tested quarterly on a
            rolling twelve-month basis) was comfortably met; the group was in a net cash position throughout
            the quarter.
            """),
        )))

    # ---------------------------------------------------------------- Q4 2025
    docs.append(Doc(
        DRIVE, "reports/Q4_2025_Management_Accounts.md",
        "Q4 2025 Management Accounts", "md", tags=["/finance"],
        body=md(
            h1("Q4 2025 Management Accounts — Voltara Energy Group"),
            kv([("Reporting period", "1 October 2025 – 31 December 2025"),
                ("Prepared by", b.who("p_novak")),
                ("Issued", "2026-01-23")]),
            h2("Headline figures"),
            table(["Metric", "Q4 2025", "Q4 2024"],
                  [["Revenue (EUR m)", q4_25["revenue_meur"], "10.4"],
                   ["Gross margin", f"{q4_25['gross_margin_pct']}%", "41.8%"],
                   ["EBITDA (EUR m)", q4_25["ebitda_meur"], "1.02"]]),
            h2("Commentary"),
            para(f"""
            Q4 2025 was the strongest quarter in the company's history with revenue of
            EUR {q4_25['revenue_meur']} million, closing the year at EUR {fy25['revenue_meur']} million,
            up from EUR {fy24['revenue_meur']} million in 2024. Gross margin of {q4_25['gross_margin_pct']}%
            reflected the first deliveries under the Vestkraft agreement signed on 18 September 2025.
            """),
            para(f"""
            Full-year EBITDA reached EUR {fy25['ebitda_meur']} million, an EBITDA margin of
            {fy25['ebitda_margin_pct']}%, against {fy24['ebitda_margin_pct']}% in 2024.
            """),
        )))

    # ------------------------------------------------------- FY2025 annual
    docs.append(Doc(
        DRIVE, "reports/FY2025_Annual_Report_Summary.md",
        "FY2025 Annual Report — Summary", "md", tags=["/finance", "/exec"],
        body=md(
            h1("FY2025 Annual Report — Summary"),
            kv([("Financial year", "1 January 2025 – 31 December 2025"),
                ("Auditor", fy25["auditor"]),
                ("Audit report date", fy25["audit_report_date"]),
                ("Audit opinion", fy25["audit_opinion"]),
                ("Approved for issue by the Board", "2026-02-11")]),
            h2("Consolidated results"),
            table(["EUR million", "FY2025", "FY2024", "Change"],
                  [["Revenue", fy25["revenue_meur"], fy24["revenue_meur"],
                    "+%.1f%%" % ((fy25["revenue_meur"] / fy24["revenue_meur"] - 1) * 100)],
                   ["Gross profit", fy25["gross_profit_meur"], "15.7", "+36.3%"],
                   ["Operating expenses", fy25["opex_meur"], "12.6", "+28.6%"],
                   ["EBITDA", fy25["ebitda_meur"], fy24["ebitda_meur"], "+67.7%"],
                   ["EBITDA margin", f"{fy25['ebitda_margin_pct']}%",
                    f"{fy24['ebitda_margin_pct']}%", "+2.6pp"]]),
            h2("Quarterly revenue split 2025"),
            table(["Quarter", "Revenue (EUR m)"],
                  [[q, b.quarters[q]["revenue_meur"]]
                   for q in ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]]),
            h2("Auditor's opinion"),
            para(f"""
            {fy25['auditor']} issued an unqualified opinion on the consolidated financial statements on
            {fy25['audit_report_date']}. No material weaknesses in internal control were reported. One
            observation was raised on the timeliness of intercompany reconciliation between the Hungarian and
            Danish entities; management has addressed this within Project Anchor.
            """),
            h2("Dividend"),
            para("""
            No dividend was proposed or paid in respect of the 2025 financial year. Retained earnings are
            allocated to the capacity expansion programme approved by the Board.
            """),
        )))

    # ---------------------------------------------------------------- budget
    budget = fin["budget_2026_by_function"]
    rows = [["Function", "Budget 2026 (kEUR)", "H1 actual (kEUR)", "H1 % of budget", "Owner"]]
    owner_map = {"R&D": b.name("p_lehmann"), "Manufacturing overhead": b.name("p_ruhland"),
                 "Sales & Marketing": b.name("p_lund"), "G&A": b.name("p_sorensen"),
                 "IT & Security": b.name("p_toth")}
    for r in budget["rows"]:
        rows.append([r["function"], r["budget"], r["h1_actual"],
                     round(r["h1_actual"] / r["budget"] * 100, 1), owner_map[r["function"]]])
    total_b = sum(r["budget"] for r in budget["rows"])
    total_a = sum(r["h1_actual"] for r in budget["rows"])
    rows.append(["TOTAL", total_b, total_a, round(total_a / total_b * 100, 1), ""])
    docs.append(Doc(
        DRIVE, "budget/Budget_2026_by_function.xlsx",
        "Budget 2026 by function", "xlsx", tags=["/finance", "difficulty:xlsx"],
        sheets={
            "Budget 2026": rows,
            "Revenue plan": [["Quarter", "Plan (EUR m)", "Actual (EUR m)", "Variance %"],
                             ["Q1 2026", q1["plan_revenue_meur"], q1["revenue_meur"], q1["variance_pct"]],
                             ["Q2 2026", q2["plan_revenue_meur"], q2["revenue_meur"], q2["variance_pct"]],
                             # quarter not closed yet -> spelled out, so an
                             # extractor cannot render the blank as "NaN"
                             ["Q3 2026", 15.6, "not closed", "not closed"],
                             ["Q4 2026", 16.5, "not closed", "not closed"],
                             ["FY2026", fy26["plan_revenue_meur"], "not closed", "not closed"]],
            "Assumptions": [["Assumption", "Value"],
                            ["Headcount at year end (FTE)", d["hr"]["headcount_plan_year_end"]],
                            ["Average salary increase (April 2026)",
                             f"{d['hr']['compensation_review']['h1_2026_increase_pct']}%"],
                            ["Lithium cell price indexation", "+6% (cap under Nordcell clause 6.4)"],
                            ["EUR/DKK", 7.46],
                            ["EUR/HUF", 392.0],
                            ["Target EBITDA margin", f"{fy26['plan_ebitda_margin_pct']}%"]],
        }))

    # -------------------------------------------------------------- forecast
    fc = fin["forecast_2026_h2"]
    docs.append(Doc(
        DRIVE, "forecast/H2_2026_Forecast.md",
        "H2 2026 Forecast", "md", tags=["/finance", "/memo"],
        body=md(
            h1("H2 2026 Forecast"),
            kv([("Prepared by", b.who("p_novak")),
                ("Reviewed by", b.who("p_sorensen")),
                ("Forecast date", "2026-07-24"),
                ("Confidence", fc["confidence"])]),
            h2("Base case"),
            table(["Metric", "H1 2026 actual", "H2 2026 forecast", "FY2026 forecast", "FY2026 plan"],
                  [["Revenue (EUR m)", h1_26["revenue_meur"], fc["revenue_meur"],
                    round(h1_26["revenue_meur"] + fc["revenue_meur"], 1), fy26["plan_revenue_meur"]],
                   ["EBITDA (EUR m)", h1_26["ebitda_meur"], fc["ebitda_meur"],
                    round(h1_26["ebitda_meur"] + fc["ebitda_meur"], 2), fy26["plan_ebitda_meur"]]]),
            h2("Key risks"),
            bullets(fc["key_risks"]),
            h2("Downside case"),
            para("""
            If provisional acceptance on Project Helios slips beyond 31 December 2026, EUR 2.5 million of
            revenue and EUR 0.9 million of EBITDA move into 2027. In that scenario FY2026 revenue lands at
            approximately EUR 57.5 million and EBITDA at EUR 5.7 million. The covenant remains met in both
            cases because the group holds a net cash position.
            """),
            h2("Upside case"),
            para("""
            Closing the Nordkraft Energi opportunity in November 2026 would add EUR 1.1 million of revenue in
            the year through the advance payment milestone. This is not included in the base case; the
            opportunity is weighted at 60% in the pipeline review.
            """),
        )))

    # ------------------------------------------------------------- covenant
    docs.append(Doc(
        DRIVE, "treasury/Danubia_Covenant_Report_2026-06.md",
        "Danubia Bank covenant report — 30 June 2026", "md", tags=["/finance", "/compliance"],
        body=md(
            h1("Covenant Compliance Report — Danubia Bank Zrt. Term Loan"),
            kv([("Facility", "EUR 6.0 million term loan, signed 2025-03-31"),
                ("Interest", "3M EURIBOR + 2.85%"),
                ("Test date", "2026-06-30"),
                ("Prepared by", b.who("p_novak")),
                ("Certified by", b.who("p_sorensen"))]),
            h2("Financial covenant (clause 12.1)"),
            para("""
            Net debt to EBITDA shall not exceed 3.0x, tested quarterly on a rolling twelve-month basis.
            """),
            table(["Component", "Value"],
                  [["Total debt", f"EUR {bal['total_debt_meur']} million"],
                   ["Cash and equivalents", f"EUR {bal['cash_meur']} million"],
                   ["Net debt", f"EUR {bal['net_debt_meur']} million"],
                   ["Rolling 12-month EBITDA", "EUR 5.29 million"],
                   ["Net debt / EBITDA", f"{bal['net_debt_to_ebitda']}x"],
                   ["Covenant limit", "3.00x"],
                   ["Status", "COMPLIANT"]]),
            h2("Reporting covenant (clause 12.4)"),
            para("""
            Management accounts are due within 45 days of quarter end and audited accounts within 120 days of
            year end. The Q2 2026 management accounts were delivered on 21 July 2026 (day 21) and the FY2025
            audited accounts on 20 March 2026 (day 79). Both obligations were met.
            """),
        )))

    # ------------------------------------------------------------- cash flow
    docs.append(Doc(
        DRIVE, "reports/Cash_Flow_Statement_H1_2026.md",
        "Cash flow statement H1 2026", "md", tags=["/finance"],
        body=md(
            h1("Cash Flow Statement — H1 2026"),
            kv([("Period", "1 January 2026 – 30 June 2026"),
                ("Basis", "Management accounts, indirect method")]),
            table(["EUR million", "H1 2026"],
                  [["EBITDA", h1_26["ebitda_meur"]],
                   ["Change in working capital", -0.71],
                   ["Interest paid", -0.14],
                   ["Tax paid", -0.02],
                   ["Operating cash flow", bal["operating_cash_flow_h1_meur"]],
                   ["Capital expenditure", -bal["capex_h1_meur"]],
                   ["Free cash flow", bal["free_cash_flow_h1_meur"]],
                   ["Opening cash (1 Jan 2026)", 9.12],
                   ["Closing cash (30 Jun 2026)", bal["cash_meur"]]]),
            h2("Commentary"),
            para(f"""
            Free cash flow was negative EUR {abs(bal['free_cash_flow_h1_meur'])} million in the first half,
            driven by capital expenditure of EUR {bal['capex_h1_meur']} million of which EUR 1.24 million
            relates to Project Orion, and by an increase in inventory to EUR {bal['inventory_meur']} million
            ahead of the second-half delivery schedule. Days sales outstanding rose to {bal['dso_days']} days,
            mostly because of a single Vestkraft milestone invoice settled in early July.
            """),
        )))

    # ---------------------------------------------------------------- KPI
    docs.append(Doc(
        DRIVE, "reports/KPI_Dashboard_2026-H1.md",
        "KPI dashboard H1 2026", "md", tags=["/exec", "/report", "/data"],
        body=md(
            h1("KPI Dashboard — H1 2026"),
            kv([("As of", b.as_of), ("Owner", b.who("p_somogyi"))]),
            table(["KPI", "H1 2026", "Target", "Status"],
                  [["Revenue (EUR m)", h1_26["revenue_meur"], h1_26["plan_revenue_meur"], "on plan"],
                   ["EBITDA margin", f"{h1_26['ebitda_margin_pct']}%", "9.6%", "below"],
                   ["Order backlog (EUR m)", bal["order_backlog_meur"], 38.0, "above"],
                   ["Weighted pipeline (EUR m)", d["sales"]["pipeline_weighted_meur"], 20.0, "above"],
                   ["Win rate", f"{d['sales']['h1_2026_results']['win_rate_pct']}%", "55%", "above"],
                   ["Headcount (FTE)", d["hr"]["headcount_fte"], 218, "below"],
                   ["Voluntary attrition", f"{d['hr']['voluntary_attrition_h1_pct']}%", "10%", "good"],
                   ["Open ISO 27001 non-conformities", 2, 0, "action needed"],
                   ["On-time project milestones", "71%", "85%", "below"],
                   ["Safety incidents (lost time)", 0, 0, "good"]]),
            h2("Traffic-light summary"),
            bullets([
                "GREEN — commercial: backlog, pipeline and win rate all ahead of target.",
                "AMBER — delivery: Project Helios and Project Anchor are both behind schedule.",
                "AMBER — profitability: EBITDA margin is 1.6 percentage points below the half-year target.",
                "GREEN — people: attrition below target, although hiring runs two months behind plan.",
            ]),
        )))

    # ------------------------------------------------------------ pipeline
    sales = d["sales"]
    pipe_rows = [["Account", "Country", "Value (EUR m)", "Stage", "Probability %",
                  "Weighted (EUR m)", "Owner", "Expected close"]]
    for o in sales["top_opportunities"]:
        pipe_rows.append([o["account"], o["country"], o["value_meur"], o["stage"],
                          o["probability_pct"], round(o["value_meur"] * o["probability_pct"] / 100, 2),
                          b.name(o["owner"]), o.get("expected_close", "")])
    docs.append(Doc(
        DRIVE, "sales/Pipeline_Review_2026-06.xlsx",
        "Pipeline review June 2026", "xlsx", tags=["/sales", "difficulty:xlsx"],
        sheets={
            "Top opportunities": pipe_rows,
            "Summary": [["Metric", "Value"],
                        ["As of", sales["as_of"]],
                        ["Gross pipeline (EUR m)", sales["pipeline_gross_meur"]],
                        ["Weighted pipeline (EUR m)", sales["pipeline_weighted_meur"]],
                        ["Open opportunities", sales["open_opportunities"]],
                        ["Average sales cycle (days)", sales["average_sales_cycle_days"]],
                        ["Won H1 2026 (count)", sales["h1_2026_results"]["won_count"]],
                        ["Won H1 2026 (EUR m)", sales["h1_2026_results"]["won_value_meur"]],
                        ["Lost H1 2026 (count)", sales["h1_2026_results"]["lost_count"]],
                        ["Lost H1 2026 (EUR m)", sales["h1_2026_results"]["lost_value_meur"]],
                        ["Win rate %", sales["h1_2026_results"]["win_rate_pct"]]],
            "Stage definitions": [["Stage", "Probability %", "Exit criterion"],
                                  ["discovery", 10, "Qualified need and budget holder identified"],
                                  ["qualification", 25, "Technical fit confirmed, site survey booked"],
                                  ["proposal", 40, "Written offer submitted"],
                                  ["negotiation", 60, "Commercial terms under discussion"],
                                  ["contracting", 85, "Legal review of signed-off terms"]],
        }))

    docs.append(Doc(
        DRIVE, "sales/Account_Plan_Nordkraft.md",
        "Account plan — Nordkraft Energi AB", "md", tags=["/sales"],
        body=md(
            h1("Account Plan — Nordkraft Energi AB"),
            kv([("Account owner", b.who("p_lund")),
                ("Country", "Sweden"),
                ("Opportunity value", "EUR 9.8 million"),
                ("Stage", "negotiation (60%)"),
                ("Expected close", "2026-11-30"),
                ("Last updated", "2026-07-02")]),
            h2("Opportunity"),
            para("""
            Nordkraft Energi operates 14 wind sites in southern and central Sweden and is tendering for a
            frequency-regulation storage portfolio of 60 MWh split across three sites. Voltara is shortlisted
            with two competitors. The scope matches the VoltStack 2 platform with GC-3000 control.
            """),
            h2("Decision unit"),
            table(["Role", "Name", "Position", "Disposition"],
                  [["Economic buyer", "Erik Lindqvist", "CFO", "neutral, focused on total cost"],
                   ["Technical buyer", "Maja Holmberg", "Head of Grid Assets", "positive"],
                   ["User buyer", "Anders Sjögren", "Operations Manager", "positive"],
                   ["Coach", "Maja Holmberg", "", "shares tender feedback informally"]]),
            h2("Commercial position"),
            para("""
            Nordkraft has requested a 7% discount against list price for the full 60 MWh portfolio in exchange
            for a single framework award. The requested discount exceeds the delegated authority of the Head of
            Sales and has been escalated. The Board deferred the decision at its meeting on 13 May 2026 and
            will revisit it on 16 September 2026.
            """),
            h2("Risks"),
            bullets([
                "Lead time: the customer requires first delivery in Q2 2027; this depends on the Orion line "
                "being qualified on schedule by 31 March 2027.",
                "Price: a competitor is understood to be bidding approximately 5% below our list price.",
                "Reference: the customer has asked for a site visit to the Esbjerg installation, which is not "
                "yet commissioned.",
            ]),
            h2("Next steps"),
            numbered([
                "Deliver revised technical proposal by 2026-08-28 (owner: Sofie Lund).",
                "Prepare margin analysis for the Board paper by 2026-09-04 (owner: Tamás Novák).",
                "Confirm Esbjerg reference visit window once the energisation date is firm (owner: Lars Nygaard).",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "sales/Proposal_EWerk_Allgaeu_2026.md",
        "Proposal — E-Werk Allgäu GmbH", "md", tags=["/sales"],
        body=md(
            h1("Commercial Proposal — E-Werk Allgäu GmbH"),
            kv([("Proposal number", "PRO-2026-0114"),
                ("Date", "2026-06-12"),
                ("Valid until", "2026-09-30"),
                ("Prepared by", b.who("p_erdelyi")),
                ("Value", "EUR 4.1 million")]),
            h2("Scope"),
            bullets([
                "Two VoltStack 2 containerised systems, 2.5 MWh each, installed at the Kempten substation.",
                "GC-3000 grid controller with IEC 61850 station bus integration.",
                "Site acceptance testing and grid-code compliance documentation for EN 50549-2.",
                "Five-year service agreement with 24-hour response and remote monitoring through the "
                "Voltara Service Portal.",
            ]),
            h2("Pricing"),
            table(["Item", "Quantity", "Unit price (EUR)", "Total (EUR)"],
                  [["VoltStack 2 (2.5 MWh)", 2, 1_420_000, 2_840_000],
                   ["GC-3000 controller and integration", 1, 386_000, 386_000],
                   ["Installation and commissioning", 1, 512_000, 512_000],
                   ["Service agreement, 5 years", 1, 362_000, 362_000],
                   ["TOTAL", "", "", 4_100_000]]),
            h2("Commercial terms"),
            kv([("Payment", "30% on order, 50% on delivery, 20% on acceptance"),
                ("Delivery", "34 weeks from order confirmation"),
                ("Warranty", "24 months from provisional acceptance"),
                ("Governing law", "German law")]),
            h2("Assumptions"),
            bullets([
                "Grid connection permit is obtained by the customer before delivery.",
                "Civil works and the concrete foundation are in the customer's scope.",
                "Prices are firm until 30 September 2026 and are subject to the lithium index thereafter.",
            ]),
        )))

    wl = sales["h1_2026_results"]
    docs.append(Doc(
        DRIVE, "sales/Win_Loss_Review_H1_2026.md",
        "Win/loss review H1 2026", "md", tags=["/sales", "/report"],
        body=md(
            h1("Win/Loss Review — H1 2026"),
            kv([("Period", "1 January – 30 June 2026"), ("Owner", b.who("p_lund"))]),
            h2("Summary"),
            table(["Metric", "Value"],
                  [["Opportunities won", wl["won_count"]],
                   ["Value won (EUR m)", wl["won_value_meur"]],
                   ["Opportunities lost", wl["lost_count"]],
                   ["Value lost (EUR m)", wl["lost_value_meur"]],
                   ["Win rate", f"{wl['win_rate_pct']}%"],
                   ["Average deal size won (EUR m)",
                    round(wl["won_value_meur"] / wl["won_count"], 2)]]),
            h2("Loss reasons"),
            table(["Reason", "Count", "Value (EUR m)"],
                  [["Price", 3, 4.4], ["Lead time", 1, 1.8]]),
            h2("Analysis"),
            para("""
            All three price-related losses were in the sub-5 MWh segment where a local competitor bids
            aggressively on hardware only. Voltara's proposals in that segment carry a full five-year service
            package that the customer often does not value at proposal stage. The single lead-time loss was the
            Stadtwerke Passau tender, where a 34-week delivery could not be shortened before the Orion line is
            available.
            """),
            h2("Actions"),
            numbered([
                "Introduce a hardware-only price list for deals below 5 MWh, with service as a visible option "
                "(owner: Sofie Lund, due 2026-09-15).",
                "Publish an indicative delivery calendar tied to the Orion qualification milestones "
                "(owner: Stefan Ruhland, due 2026-10-01).",
                "Add a reference case from the Regensburg project to the DACH proposal template "
                "(owner: Gábor Erdélyi, due 2026-08-31).",
            ]),
        )))

    # ----------------------------------------------------------------- memos
    docs.append(Doc(
        DRIVE, "memos/Decision_Memo_ERP_Golive_Options.md",
        "Decision memo — Project Anchor go-live options", "md", tags=["/memo", "/finance"],
        body=md(
            h1("Decision Memo — Project Anchor ERP Go-Live Options"),
            kv([("To", "Executive Committee"),
                ("From", b.who("p_novak")),
                ("Date", "2026-07-17"),
                ("Decision required by", "2026-08-14"),
                ("Classification", "Internal — Finance")]),
            h2("Background"),
            para("""
            Project Anchor replaces the legacy finance system with Northbridge ERP across all three legal
            entities. The approved budget is EUR 1.15 million; EUR 0.81 million has been spent. The second data
            migration dry-run completed on 14 July 2026, one day ahead of plan, but confirmed that 4.1% of open
            receivable items in the Danish entity cannot be migrated automatically.
            """),
            h2("Options"),
            table(["Option", "Go-live", "One-off cost", "Risk"],
                  [["A — proceed as planned", "2026-09-30", "EUR 0",
                    "High: manual clean-up of 4.1% of DK receivables during quarter-end close"],
                   ["B — phased, DK entity in wave 2", "2026-09-30 (HU, DE) / 2026-11-30 (DK)",
                    "EUR 74,000", "Medium: two parallel ledgers for two months"],
                   ["C — defer to 2027-01-01", "2027-01-01", "EUR 138,000",
                    "Low technically, but the legacy system's support contract ends 2026-12-31"]]),
            h2("Recommendation"),
            para("""
            Option B. The additional cost of EUR 74,000 is within the remaining project contingency of
            EUR 0.12 million, it keeps the legacy system decommissioning inside 2026, and it removes the
            interaction between the migration and the Q3 quarter-end close in the entity with the highest
            open-item count.
            """),
            h2("Financial impact"),
            bullets([
                "Option B keeps total project spend at EUR 1.12 million, EUR 0.03 million under budget.",
                "No impact on the FY2026 EBITDA forecast; the cost is capitalised as part of the ERP asset.",
                "The reporting covenant deadline of 45 days after quarter end is protected under all options.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "memos/Capex_Approval_Orion.md",
        "Capex approval — Project Orion", "md", tags=["/memo", "/finance", "/tracking"],
        body=md(
            h1("Capital Expenditure Approval — Project Orion"),
            kv([("Request", "Second assembly line, München plant"),
                ("Amount", "EUR 4.6 million"),
                ("Requested by", b.who("p_ruhland")),
                ("Sponsor", b.who("p_lehmann")),
                ("Approved", "Board meeting, 2026-05-13"),
                ("Completion", "2027-03-31")]),
            h2("Investment case"),
            para("""
            The München plant is running at 86% of nameplate capacity on a single assembly line. The second
            line increases module output by 40% and removes the single point of failure in serial production.
            Without it, the delivery calendar cannot support the pipeline beyond the first quarter of 2027,
            which was already the stated reason for one lost tender in the first half of 2026.
            """),
            h2("Financials"),
            table(["Item", "EUR"],
                  [["Assembly and test equipment", 2_950_000],
                   ["Building works and utilities", 980_000],
                   ["Qualification and validation", 385_000],
                   ["Contingency (6%)", 285_000],
                   ["TOTAL", 4_600_000]]),
            table(["Metric", "Value"],
                  [["Payback period", "3.1 years"],
                   ["IRR", "24.6%"],
                   ["NPV at 9% over 8 years", "EUR 3.4 million"],
                   ["Capitalised from", "2027-04-01"],
                   ["Depreciation", "8 years, straight line"]]),
            h2("Funding"),
            para("""
            Funded from operating cash flow and existing cash reserves. No additional debt is required and the
            Danubia Bank covenant is unaffected because the group remains in a net cash position.
            """),
        )))

    # ------------------------------------------------------------------ exec
    docs.append(Doc(
        DRIVE, "exec/Board_Pack_2026-09-16_draft.md",
        "Board pack 16 September 2026 (draft)", "md", tags=["/exec"],
        body=md(
            h1("Board Pack — 16 September 2026 (DRAFT)"),
            kv([("Meeting", "Board of Directors, København"),
                ("Chair", b.name("p_dahl")),
                ("Secretary", b.name("p_farkas")),
                ("Circulated", "2026-09-09 (planned)"),
                ("Status", "DRAFT — not yet approved for circulation")]),
            h2("1. Agenda"),
            numbered([
                "Minutes of the meeting held on 13 May 2026 — approval.",
                "CEO report and H1 2026 performance.",
                "Financial review: H1 actuals and H2 forecast.",
                "Project Helios — schedule recovery plan and Nordcell claim.",
                "Nordkraft Energi pricing exception — decision deferred from 13 May 2026.",
                "Project Orion progress report.",
                "Information security: ISO 27001 surveillance findings and NIS2 programme.",
                "Any other business.",
            ]),
            h2("2. Executive summary"),
            para(f"""
            H1 2026 revenue of EUR {h1_26['revenue_meur']} million is exactly on plan, but EBITDA of
            EUR {h1_26['ebitda_meur']} million is EUR 0.19 million behind, driven by cell price indexation and a
            higher warranty provision. Order backlog stands at EUR {bal['order_backlog_meur']} million.
            The two amber items are Project Helios, now forecast for provisional acceptance on
            22 January 2027, and Project Anchor, where the recommendation is a phased go-live.
            """),
            h2("3. Decisions requested"),
            bullets([
                "Approve the Nordkraft Energi discount of up to 7% conditional on a firm 60 MWh framework "
                "award and first delivery no earlier than Q2 2027.",
                "Note the phased Project Anchor go-live (Option B) approved by the Executive Committee.",
                "Approve the additional warranty provision of EUR 0.18 million recorded in Q2 2026.",
            ]),
            h2("4. Matters arising from 13 May 2026"),
            table(["Item", "Owner", "Status"],
                  [["Orion capex release", b.name("p_ruhland"), "Equipment ordered 24 July 2026"],
                   ["ViVeSec AI Box rollout", b.name("p_toth"), "Live since 15 June 2026, 38 users enabled"],
                   ["Nordkraft pricing exception", b.name("p_lund"), "On this agenda"]]),
        )))

    for month, body in (
        ("2026-07", """
        June closed the first half exactly on the revenue plan. The Helios cell delivery slipped again and we
        have now served a formal delay claim on Nordcell under clause 7.1 of the framework agreement. The
        alternative supplier qualification with LM Cells is on track for a decision by 29 August. On the
        positive side, the Orion equipment order was placed a week early and the AI Box platform has been in
        production use since mid-June with 38 users and 156 indexed documents.
        """),
        ("2026-06", """
        May was dominated by the Board meeting in München, which approved the Orion investment and the ViVeSec
        AI Box rollout, and deferred the Nordkraft pricing decision to September. The Meridian firmware v3.0
        beta freeze landed two days early. Hiring remains two months behind plan with 14 open positions.
        """),
    ):
        docs.append(Doc(
            DRIVE, f"exec/CEO_Monthly_Brief_{month}.md",
            f"CEO monthly brief {month}", "md", tags=["/exec", "/summary"],
            body=md(
                h1(f"CEO Monthly Brief — {month}"),
                kv([("From", b.who("p_barat")), ("To", "All managers"),
                    ("Distribution", "Internal")]),
                h2("Where we stand"),
                para(body),
                h2("Priorities for the coming month"),
                bullets([
                    "Recover the Helios schedule and close the alternative cell supplier decision.",
                    "Confirm the phased Anchor go-live and protect the quarter-end close.",
                    "Close the two open ISO 27001 non-conformities before 31 August.",
                    "Fill the four highest-priority engineering vacancies.",
                ]),
            )))

    # ------------------------------------------------------- project finance
    helios = b.projects["prj_helios"]
    docs.append(Doc(
        DRIVE, "projects/Helios_Project_Financials_2026-06.md",
        "Project Helios financials — June 2026", "md", tags=["/finance", "/tracking"],
        body=md(
            h1("Project Helios — Financial Status, June 2026"),
            kv([("Customer", helios["customer"]),
                ("Contract value", f"EUR {helios['value_meur']} million"),
                ("Percentage complete", f"{helios['percent_complete']}%"),
                ("Revenue recognised to date", "EUR 7.19 million"),
                ("Prepared by", b.who("p_novak"))]),
            h2("Revenue recognition"),
            para(f"""
            Revenue is recognised over time on a cost-to-cost basis. At 30 June 2026 the project was
            {helios['percent_complete']}% complete, giving cumulative revenue of EUR 7.19 million against
            cumulative costs of EUR 5.02 million. EUR 1.1 million of revenue originally planned for Q2 2026
            moved to Q3 following the cell delivery delay.
            """),
            h2("Margin"),
            table(["Metric", "At contract award", "Current estimate"],
                  [["Contract value (EUR m)", 12.4, 12.4],
                   ["Estimated total cost (EUR m)", 8.55, 8.91],
                   ["Estimated gross margin", "31.0%", "28.1%"],
                   ["Warranty provision (EUR m)", 0.24, 0.42]]),
            h2("Delay damages exposure"),
            para("""
            Under clause 8.2 of the supply agreement, delay damages accrue at 0.5% of the contract price per
            commenced week, capped at 10% of the contract price. On the current forecast acceptance date of
            22 January 2027, eight weeks past the contractual date of 30 November 2026, the exposure is
            EUR 0.50 million against a maximum of EUR 1.24 million. A counter-claim of EUR 0.37 million has
            been served on Nordcell under clause 7.1 of the framework supply agreement.
            """),
        )))

    # ------------------------------------------------------ invoice registers
    def _invoice_rows(quarter: str, first_no: int, months: list[str]) -> str:
        customers = [
            ("Vestkraft A/S", "DK", "Helios milestone"),
            ("Stadtwerke Regensburg GmbH", "DE", "Advance payment"),
            ("E-Werk Kempten", "DE", "Service agreement"),
            ("Nordisk Vind A/S", "DK", "Spare parts"),
            ("Pannon Energia Zrt.", "HU", "Site survey"),
            ("Alpen Energie AG", "AT", "Service agreement"),
            ("Vestkraft A/S", "DK", "Variation order"),
            ("Stadtwerke Passau", "DE", "Feasibility study"),
        ]
        amounts = [1_240_000, 960_000, 41_500, 128_400, 22_800, 63_200, 84_900, 18_500]
        lines = ["invoice_no,date,customer,country,description,net_eur,vat_eur,gross_eur,due_date,status"]
        no = first_no
        for m_idx, month in enumerate(months):
            for i, (cust, ctry, desc) in enumerate(customers):
                amount = amounts[(i + m_idx) % len(amounts)]
                vat = round(amount * (0.25 if ctry == "DK" else 0.19), 2)
                day = 3 + (i * 3) % 25
                status = "paid" if m_idx < len(months) - 1 or i < 4 else "open"
                lines.append(
                    f"INV-{quarter}-{no:04d},{month}-{day:02d},{cust},{ctry},{desc},"
                    f"{amount:.2f},{vat:.2f},{amount + vat:.2f},{month}-{min(day + 24, 28):02d},{status}"
                )
                no += 1
        return "\n".join(lines)

    docs.append(Doc(
        DRIVE, "invoices/Invoice_Register_2026-Q1.csv",
        "Invoice register Q1 2026", "csv", tags=["/finance"],
        body=_invoice_rows("2026Q1", 1001, ["2026-01", "2026-02", "2026-03"])))
    docs.append(Doc(
        DRIVE, "invoices/Invoice_Register_2026-Q2.csv",
        "Invoice register Q2 2026", "csv", tags=["/finance"],
        body=_invoice_rows("2026Q2", 2001, ["2026-04", "2026-05", "2026-06"])))

    # --------------------------------------------------------- Orion tracker
    orion = b.projects["prj_orion"]
    orion_rows = [["Milestone", "Owner", "Due", "Actual", "Status", "Comment"]]
    for m in orion["milestones"]:
        orion_rows.append([m["name"], b.name(orion["manager"]), m["due"],
                           m.get("actual") or "", m["status"], ""])
    docs.append(Doc(
        DRIVE, "projects/Orion_Milestone_Tracker.xlsx",
        "Project Orion milestone tracker", "xlsx", tags=["/tracking", "difficulty:xlsx"],
        sheets={
            "Milestones": orion_rows,
            "Capex drawdown": [["Period", "Planned (kEUR)", "Actual (kEUR)", "Cumulative (kEUR)"],
                               ["2026-Q2", 240, 218, 218],
                               ["2026-Q3", 1_450, "not incurred", "not incurred"],
                               ["2026-Q4", 1_610, "not incurred", "not incurred"],
                               ["2027-Q1", 1_300, "not incurred", "not incurred"],
                               ["TOTAL", 4_600, 218, 218]],
            "Summary": [["Field", "Value"],
                        ["Project", orion["name"]],
                        ["Capex approved (EUR m)", orion["capex_meur"]],
                        ["Approved by", orion["approved_by"]],
                        ["Manager", b.name(orion["manager"])],
                        ["Sponsor", b.name(orion["sponsor"])],
                        ["Start", orion["start"]],
                        ["Planned completion", orion["planned_completion"]],
                        ["Percent complete", orion["percent_complete"]],
                        ["Status", orion["status"]]],
        }))

    # -------------------------------------------------------- cost centres
    cc_rows = [["Cost centre", "Owner", "Entity", "Budget Q2 (kEUR)", "Actual Q2 (kEUR)",
                "Variance (kEUR)", "Comment"]]
    for code, owner, entity, budget, actual, comment in [
        ("CC-1000 R&D firmware", "p_kiss", "HU", 890, 934, "Meridian type approval preparation"),
        ("CC-1100 R&D battery systems", "p_petrova", "DE", 720, 705, ""),
        ("CC-2000 Manufacturing", "p_ruhland", "DE", 1_060, 1_048, ""),
        ("CC-2100 Quality & compliance", "p_brandt", "DE", 210, 232, "ISO surveillance audit fees"),
        ("CC-3000 Field service", "p_nygaard", "DK", 540, 566, "Two unplanned coolant pump replacements"),
        ("CC-4000 Sales Nordics", "p_lund", "DK", 380, 371, ""),
        ("CC-4100 Sales DACH & CEE", "p_erdelyi", "DE", 420, 402, ""),
        ("CC-5000 Finance", "p_novak", "HU", 300, 318, "Anchor external programme management"),
        ("CC-5100 Procurement", "p_varga", "HU", 160, 158, ""),
        ("CC-6000 HR", "p_kovacs", "HU", 190, 181, "Two hires slipped to Q3"),
        ("CC-7000 IT & security", "p_toth", "HU", 300, 291, ""),
        ("CC-8000 Executive & admin", "p_somogyi", "HU", 250, 254, ""),
    ]:
        cc_rows.append([code, b.name(owner), entity, budget, actual, actual - budget, comment])
    docs.append(Doc(
        DRIVE, "reports/Q2_2026_Cost_Centre_Report.xlsx", "Cost centre report Q2 2026", "xlsx",
        tags=["/finance", "/report", "difficulty:xlsx"],
        sheets={
            "Q2 2026": cc_rows,
            "Notes": [["Note"],
                      ["Figures are management accounts, unaudited."],
                      ["A positive variance means spend above budget."],
                      ["Project capitalised cost is excluded; see the Orion tracker."]],
        }))

    # ------------------------------------------------------------ receivables
    ar = d["finance_operations"]["aged_receivables"]
    ar_lines = ["customer,country,invoice_no,invoice_date,due_date,amount_eur,bucket,status"]
    ar_data = [
        ("Vestkraft A/S", "DK", "INV-2026Q2-2001", "2026-04-03", "2026-05-03", 1_240_000,
         "current", "paid 2026-07-04"),
        ("Stadtwerke Regensburg GmbH", "DE", "INV-2026Q2-2002", "2026-04-06", "2026-05-06",
         960_000, "current", "open"),
        ("E-Werk Kempten", "DE", "INV-2026Q2-2003", "2026-04-09", "2026-05-09", 41_500,
         "1-30 days", "open"),
        ("Nordisk Vind A/S", "DK", "INV-2026Q2-2004", "2026-04-12", "2026-05-12", 128_400,
         "1-30 days", "open"),
        ("Pannon Energia Zrt.", "HU", "INV-2026Q2-2005", "2026-04-15", "2026-05-15", 22_800,
         "31-60 days", "open, reminder sent"),
        ("Alpen Energie AG", "AT", "INV-2026Q2-2006", "2026-04-18", "2026-05-18", 63_200,
         "31-60 days", "open, reminder sent"),
        ("Stadtwerke Passau", "DE", "INV-2026Q1-1042", "2026-02-11", "2026-03-13", 18_500,
         "61-90 days", "disputed, feasibility study scope"),
        ("Baltic Grid Partners UAB", "LT", "INV-2026Q1-1017", "2026-01-22", "2026-02-21", 9_400,
         "over 90 days", "escalated to collection"),
    ]
    for cust, ctry, no, idate, ddate, amount, bucket, status in ar_data:
        ar_lines.append("%s,%s,%s,%s,%s,%.2f,%s,%s"
                        % (cust, ctry, no, idate, ddate, amount, bucket, status))
    docs.append(Doc(
        DRIVE, "receivables/Aged_Receivables_2026-06.csv", "Aged receivables 30 June 2026", "csv",
        tags=["/finance"], body="\n".join(ar_lines)))

    docs.append(Doc(
        DRIVE, "reports/Aged_Receivables_Commentary_2026-06.md",
        "Aged receivables commentary June 2026", "md", tags=["/finance", "/report"],
        body=md(
            h1("Aged Receivables — 30 June 2026"),
            kv([("Total receivables", f"EUR {ar['total_meur']} million"),
                ("DSO", f"{bal['dso_days']} days"),
                ("Bad debt provision", f"EUR {ar['bad_debt_provision_keur']} thousand"),
                ("Prepared by", b.who("p_novak"))]),
            h2("Ageing"),
            table(["Bucket", "EUR million", "Share"],
                  [["Current", ar["buckets_meur"]["current"],
                    "%.1f%%" % (ar["buckets_meur"]["current"] / ar["total_meur"] * 100)],
                   ["1–30 days", ar["buckets_meur"]["d1_30"],
                    "%.1f%%" % (ar["buckets_meur"]["d1_30"] / ar["total_meur"] * 100)],
                   ["31–60 days", ar["buckets_meur"]["d31_60"],
                    "%.1f%%" % (ar["buckets_meur"]["d31_60"] / ar["total_meur"] * 100)],
                   ["61–90 days", ar["buckets_meur"]["d61_90"],
                    "%.1f%%" % (ar["buckets_meur"]["d61_90"] / ar["total_meur"] * 100)],
                   ["Over 90 days", ar["buckets_meur"]["over_90"],
                    "%.1f%%" % (ar["buckets_meur"]["over_90"] / ar["total_meur"] * 100)],
                   ["TOTAL", ar["total_meur"], "100.0%"]]),
            h2("Commentary"),
            para(f"""
            Days sales outstanding rose to {bal['dso_days']} days, above the internal target of 55 days. The
            single largest driver was the {ar['largest_overdue']}, which fell outside the quarter-end cut-off
            and does not indicate a collection problem.
            """),
            h2("Watch list"),
            bullets([
                "Stadtwerke Passau, EUR 18,500 — disputed scope on a feasibility study; the account manager "
                "has been asked to close the dispute by 31 August 2026.",
                "Baltic Grid Partners, EUR 9,400 — over 90 days and escalated to collection; fully provided "
                "for in the bad debt provision.",
            ]),
        )))

    fo = d["finance_operations"]
    docs.append(Doc(
        DRIVE, "policies/Payment_Terms_Policy.md", "Payment terms policy", "md",
        tags=["/finance", "/legal"],
        body=md(
            h1("Payment Terms Policy"),
            kv([("Version", "3"), ("Effective", "2026-01-01"), ("Owner", b.who("p_sorensen"))]),
            h2("Customers"),
            table(["Item", "Standard"],
                  [["Payment term", f"{fo['payment_terms_policy']['customers_default_days']} days net"],
                   ["Project milestone plan", "30% on order, 50% on delivery, 20% on acceptance"],
                   ["Early settlement discount",
                    f"{fo['payment_terms_policy']['early_settlement_discount_pct']}% if paid within "
                    f"{fo['payment_terms_policy']['early_settlement_days']} days"],
                   ["Late payment", fo["payment_terms_policy"]["late_interest"]],
                   ["Credit limit", "set per customer by Finance; orders above the limit require "
                                    "CFO approval or an advance payment"]]),
            h2("Suppliers"),
            para(f"""
            The standard supplier payment term is {fo['payment_terms_policy']['suppliers_default_days']} days
            net from a valid invoice. Deviations are agreed by Procurement together with Finance. Payment is
            released only after the goods receipt and the invoice are matched to the purchase order.
            """),
            h2("Exceptions"),
            para("""
            Any term longer than 60 days on the customer side, or shorter than 30 days on the supplier side,
            requires the approval of the Chief Financial Officer.
            """),
        )))

    pr = fo["pricing"]
    docs.append(Doc(
        DRIVE, "policies/Pricing_Guideline_2026.md", "Pricing guideline 2026", "md",
        tags=["/finance", "/sales"],
        body=md(
            h1("Pricing Guideline — 2026"),
            kv([("Owner", b.who("p_sorensen")), ("Effective", "2026-01-01"),
                ("Classification", "Confidential — Finance and Sales")]),
            h2("List prices"),
            table(["Item", "List price (EUR)"],
                  [["VoltStack 2, 2.5 MWh containerised unit", format(pr["list_price_voltstack2_eur"], ",d")],
                   ["GC-3000 controller and integration", format(pr["list_price_gc3000_eur"], ",d")],
                   ["Service agreement, annual",
                    f"{pr['standard_service_pct_of_capex']}% of the system capital value"]]),
            h2("Margin thresholds"),
            table(["Threshold", "Value", "Consequence"],
                  [["Target gross margin", f"{pr['target_gross_margin_pct']}%", "no approval needed"],
                   ["Floor gross margin", f"{pr['floor_gross_margin_pct']}%",
                    "below this the offer requires CFO approval"]]),
            h2("Discount authority"),
            table(["Discount", "Approver"],
                  [["up to 4%", "Head of Sales"],
                   ["up to 8%", "CEO and CFO jointly"],
                   ["above 8%", "Board of Directors"]]),
            h2("Indexation"),
            para("""
            Offers are firm for 90 days. Beyond that, prices are subject to the lithium carbonate index in
            line with the cell supply framework agreement, where the annual adjustment is capped at plus or
            minus 6 percent.
            """),
            h2("Open exception"),
            para("""
            Nordkraft Energi AB has requested a 7 percent discount against list price for a 60 MWh framework
            award. Because the request is a framework rather than a single order, it was referred to the
            Board and deferred at the meeting on 13 May 2026 to the meeting on 16 September 2026.
            """),
        )))

    fx = fo["fx_policy"]
    docs.append(Doc(
        DRIVE, "treasury/Treasury_FX_Policy.md", "Treasury and FX policy", "md",
        tags=["/finance"],
        body=md(
            h1("Treasury and Foreign Exchange Policy"),
            kv([("Owner", b.who("p_sorensen")), ("Effective", "2025-04-01"),
                ("Hedge ratio", f"{fx['hedge_ratio_pct']}% of the identified exposure")]),
            h2("Exposures"),
            para(f"""
            The group reports in EUR. Transaction exposure arises in {', '.join(fx['exposure_currencies'])}
            through customer milestone payments and local payroll. Translation exposure on the Hungarian and
            Danish entities is not hedged.
            """),
            h2("Instruments"),
            para(f"""
            Only {fx['instrument']} may be used. Options, swaps and any instrument with leverage are not
            permitted. Hedging is for risk reduction; speculative positions are prohibited.
            """),
            h2("Current position"),
            para(f"""
            {fx['note']} At 30 June 2026 the group held forward contracts covering
            {fx['hedge_ratio_pct']} percent of the identified DKK milestone exposure through to the end of
            the first quarter of 2027.
            """),
            h2("Cash and liquidity"),
            table(["Item", "Value at 2026-06-30"],
                  [["Cash and equivalents", f"EUR {bal['cash_meur']} million"],
                   ["Total debt", f"EUR {bal['total_debt_meur']} million"],
                   ["Net cash", f"EUR {abs(bal['net_debt_meur'])} million"],
                   ["Minimum liquidity policy", "EUR 4.0 million"],
                   ["Undrawn facilities", "none"]]),
        )))

    docs.append(Doc(
        DRIVE, "exec/Monthly_Flash_2026-07.md", "Monthly flash report July 2026", "md",
        tags=["/report", "/finance", "/exec"],
        body=md(
            h1("Monthly Flash — July 2026"),
            kv([("Prepared by", b.who("p_novak")), ("Issued", "2026-08-05"),
                ("Basis", "flash estimate, not closed")]),
            h2("Headlines"),
            table(["Metric", "July 2026", "Plan", "YTD 2026", "YTD plan"],
                  [["Revenue (EUR m)", 4.62, 4.90, 32.52, 32.80],
                   ["Gross margin", "43.4%", "44.0%", "43.7%", "44.0%"],
                   ["Operating expenses (EUR m)", 1.71, 1.68, 11.45, 11.28],
                   ["EBITDA (EUR m)", 0.30, 0.48, 2.79, 3.16]]),
            h2("Commentary"),
            para("""
            July revenue was EUR 0.28 million below plan. The Helios cell batch did not arrive within the
            month, so the associated progress billing did not occur. The shortfall is expected to reverse in
            August or September depending on the confirmed delivery date of 21 August 2026.
            """),
            h2("Cash"),
            para("""
            The Vestkraft milestone invoice of EUR 1.24 million was settled on 4 July, which restored the
            cash position after the quarter-end dip. Closing cash for July was EUR 9.7 million.
            """),
            h2("Watch items"),
            bullets([
                "Helios cell delivery on 21 August 2026 — the single largest swing factor for Q3.",
                "Anchor go-live decision — the phased option carries an additional cost of EUR 74,000.",
                "Nordkraft pricing exception — on the Board agenda for 16 September 2026.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "memos/Decision_Memo_Nordkraft_Discount.md",
        "Decision memo — Nordkraft Energi discount request", "md", tags=["/memo", "/sales", "/exec"],
        body=md(
            h1("Decision Memo — Nordkraft Energi AB Discount Request"),
            kv([("To", "Board of Directors"), ("From", f"{b.who('p_lund')} and {b.who('p_novak')}"),
                ("Date", "2026-09-04"), ("Decision requested", "Board meeting 2026-09-16"),
                ("Status", "draft for review")]),
            h2("Request"),
            para("""
            Nordkraft Energi AB has asked for a 7 percent discount against list price in exchange for a
            single framework award covering a 60 MWh portfolio across three sites in southern and central
            Sweden. The opportunity is valued at EUR 9.8 million and is currently weighted at 60 percent.
            """),
            h2("Why it needs a Board decision"),
            para("""
            The pricing guideline allows the Chief Executive and the Chief Financial Officer jointly to
            approve a discount of up to 8 percent. A 7 percent discount is therefore within executive
            authority for a single order. It was referred to the Board because the request covers a
            multi-site framework rather than one order, and because it sets a reference point for the whole
            Nordic market.
            """),
            h2("Margin effect"),
            table(["Scenario", "Price (EUR m)", "Gross margin", "Gross profit (EUR m)"],
                  [["List price", 9.80, "32.0%", 3.14],
                   ["4% discount", 9.41, "29.2%", 2.75],
                   ["7% discount", 9.11, "26.8%", 2.44],
                   ["Floor (24%)", 8.76, "24.0%", 2.10]]),
            para("""
            At 7 percent the gross margin is 26.8 percent, above the 24 percent floor, so no separate
            exception to the margin policy is required.
            """),
            h2("Conditions recommended"),
            numbered([
                "The discount applies only to a firm framework award for the full 60 MWh.",
                "First delivery no earlier than the second quarter of 2027, so the commitment depends on the "
                "Orion line being qualified by 31 March 2027.",
                "Prices firm for 90 days, then subject to the lithium index.",
                "A five-year service agreement is included in the scope at the standard rate.",
            ]),
            h2("Risk if declined"),
            para("""
            A competitor is understood to be bidding approximately 5 percent below our list price. Three of
            the four losses in the first half of 2026 were price-related, all in the segment below 5 MWh; the
            Nordkraft opportunity is a different segment, so the loss pattern is not directly transferable.
            """),
        )))

    return docs
