"""HR drive: policies (incl. multilingual set), salary bands (ACL bait), people data."""
from __future__ import annotations

from .common import Bible, Doc, bullets, h1, h2, kv, md, numbered, para, table

DRIVE = "drive_hr"


def build(b: Bible) -> list[Doc]:
    d = b.d
    hr = d["hr"]
    pol = {p["id"]: p for p in hr["policies"]}
    docs: list[Doc] = []

    # ------------------------------------------------------------- handbook
    docs.append(Doc(
        DRIVE, "policies/Employee_Handbook_v6.docx", "Employee Handbook v6", "docx",
        tags=["/hr"],
        blocks=[
            ("h2", "Document control"),
            ("", f"Version {pol['pol_handbook']['version']}, effective "
                 f"{pol['pol_handbook']['effective']}. Supersedes {pol['pol_handbook']['supersedes']}. "
                 f"Owner: {b.who('p_kovacs')}. Approved by the Executive Committee."),
            ("h2", "1. Working time"),
            ("", "Standard working time is 40 hours per week in Hungary and Germany and 37 hours per week in "
                 "Denmark, in line with local collective practice. Core hours are 10:00 to 15:00 local time. "
                 "Overtime must be agreed in advance with the line manager and is compensated with time off "
                 "in lieu unless local law requires payment."),
            ("h2", "2. Remote and hybrid work"),
            ("", "The Remote and Hybrid Work Policy version 2, effective 1 September 2025, applies. "
                 "Employees must be present on site for a minimum of two days per week. Fully remote "
                 "arrangements require written approval from the responsible vice president and are reviewed "
                 "every six months."),
            ("h2", "3. Holiday and leave"),
            ("", "Annual leave entitlement is 25 working days in Hungary, 30 working days in Germany and "
                 "five weeks plus Feriefridage in Denmark. Leave is requested in the HR system at least two "
                 "weeks in advance for periods longer than three days."),
            ("", "Parental leave follows local statutory rules. Voltara tops up statutory parental pay to "
                 "100% of base salary for the first twelve weeks in all three countries."),
            ("h2", "4. Travel and expenses"),
            ("", "The Travel and Expense Policy version 3, effective 1 March 2026, applies. Per diem rates "
                 "are 32 EUR per day in Hungary, 38 EUR per day in Germany and 45 EUR per day in Denmark. "
                 "Flights below four hours are booked in economy class."),
            ("h2", "5. Performance and development"),
            ("", "The annual performance cycle closes on 30 November with a mid-year check-in in June. "
                 "Every employee agrees a development plan with their manager at the start of the cycle. "
                 "Salary review is annual and takes effect on 1 April."),
            ("h2", "6. Code of conduct"),
            ("", "All employees follow the Voltara Code of Conduct. Gifts with a value above 50 EUR must be "
                 "declared in the gift register. Conflicts of interest are disclosed to the line manager and "
                 "recorded by the compliance function."),
            ("h2", "7. Information security"),
            ("", "Multi-factor authentication is mandatory for all users since 15 April 2026. Company data is "
                 "stored only in approved systems. Personal cloud storage services must not be used for "
                 "company documents."),
            ("h2", "8. Whistleblowing"),
            ("", "Concerns can be raised with the line manager, with the HR Director or anonymously through "
                 "the reporting channel operated by the external data protection counsel. Retaliation against "
                 "a person who raises a concern in good faith is a disciplinary offence."),
            ("h2", "9. Works council"),
            ("", "In the German entity a works council of five seats operates under the agreement dated "
                 "1 October 2025. Matters within its co-determination rights are agreed with the council "
                 "before implementation."),
        ]))

    # ------------------------------------------------- salary bands (ACL bait)
    band_rows = [["Band", "Role family", "Level", "Min (EUR)", "Mid (EUR)", "Max (EUR)", "Country"]]
    bands = [
        ("B1", "Engineering", "Junior engineer", 21_600, 26_400, 31_200, "HU"),
        ("B2", "Engineering", "Engineer", 28_800, 35_400, 42_000, "HU"),
        ("B3", "Engineering", "Senior engineer", 38_400, 47_400, 56_400, "HU"),
        ("B4", "Engineering", "Lead / architect", 51_600, 63_600, 75_600, "HU"),
        ("B2", "Engineering", "Engineer", 52_800, 62_400, 72_000, "DE"),
        ("B3", "Engineering", "Senior engineer", 68_400, 80_400, 92_400, "DE"),
        ("B4", "Engineering", "Lead / architect", 86_400, 99_600, 112_800, "DE"),
        ("B3", "Engineering", "Senior engineer", 74_400, 86_400, 98_400, "DK"),
        ("C2", "Commercial", "Account manager", 42_000, 51_600, 61_200, "DE"),
        ("C3", "Commercial", "Senior account manager", 56_400, 68_400, 80_400, "DK"),
        ("F2", "Finance", "Accountant", 24_000, 29_400, 34_800, "HU"),
        ("F3", "Finance", "Controller", 36_000, 44_400, 52_800, "HU"),
        ("M1", "Management", "Team lead", 48_000, 58_800, 69_600, "HU"),
        ("M2", "Management", "Head of function", 72_000, 88_800, 105_600, "DE"),
        ("M3", "Management", "Director", 96_000, 118_800, 141_600, "DK"),
    ]
    for row in bands:
        band_rows.append(list(row))
    docs.append(Doc(
        DRIVE, "compensation/Salary_Bands_2026.xlsx", "Salary bands 2026", "xlsx",
        tags=["/hr", "acl-bait", "difficulty:xlsx"],
        sheets={
            "Bands 2026": band_rows,
            "Policy": [["Field", "Value"],
                       ["Effective", pol["pol_bands"]["effective"]],
                       ["Version", pol["pol_bands"]["version"]],
                       ["Classification", "CONFIDENTIAL — HR access only"],
                       ["Owner", b.name("p_kovacs")],
                       ["Approved by", b.name("p_barat")],
                       ["Basis", "Annual gross base salary, excluding bonus"],
                       ["Bonus target, managers", "12% of base"],
                       ["Bonus split", "Company performance 60% / individual 40%"],
                       ["Average increase, April 2026",
                        f"{hr['compensation_review']['h1_2026_increase_pct']}%"],
                       ["Next review", "1 April 2027"]],
            "Notes": [["Note"],
                      ["Bands are country-specific and are not converted between entities."],
                      ["Placement within a band is agreed between the manager and HR; the midpoint "
                       "corresponds to a fully proficient performer."],
                      ["This document does not contain individual salaries. Individual pay data is held "
                       "in the payroll system and is not part of any document repository."]],
        }))

    # ------------------------------------------------------------- policies
    docs.append(Doc(
        DRIVE, "policies/Remote_Work_Policy_v2.md", "Remote and hybrid work policy v2", "md",
        tags=["/hr"],
        body=md(
            h1("Remote and Hybrid Work Policy — version 2"),
            kv([("Effective", pol["pol_remote"]["effective"]), ("Owner", b.who("p_kovacs")),
                ("Applies to", "All employees of all three legal entities")]),
            h2("1. Principle"),
            para(pol["pol_remote"]["key_rule"]),
            h2("2. On-site presence"),
            para("""
            Employees attend their assigned site for a minimum of two days per week. Teams agree a common
            anchor day so that collaborative work is concentrated. Site presence is not tracked
            automatically; managers are accountable for the arrangement within their team.
            """),
            h2("3. Fully remote arrangements"),
            para("""
            A fully remote arrangement requires written approval from the responsible vice president and is
            reviewed every six months. It is limited to roles that do not require access to laboratory,
            production or site facilities.
            """),
            h2("4. Working from abroad"),
            para("""
            Temporary work from another country is limited to 20 working days per calendar year and requires
            prior HR approval because of tax and social security implications. Work from a country where
            Voltara has no legal entity is not permitted beyond that limit.
            """),
            h2("5. Equipment and security"),
            bullets([
                "Voltara provides a laptop, a monitor and a headset; other home office equipment is not "
                "reimbursed.",
                "Company data may only be processed on Voltara-managed devices.",
                "Multi-factor authentication is mandatory for remote access.",
                "Confidential documents must not be printed outside company premises.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "policies/Travel_and_Expense_Policy_v3_EN.md", "Travel and expense policy v3 (EN)", "md",
        tags=["/hr", "difficulty:multilingual"],
        body=md(
            h1("Travel and Expense Policy — version 3"),
            kv([("Effective", pol["pol_travel"]["effective"]), ("Owner", b.who("p_kovacs")),
                ("Language", "English (master version)")]),
            h2("1. Per diem"),
            table(["Country", "Per diem (EUR per day)"],
                  [["Hungary", 32], ["Germany", 38], ["Denmark", 45], ["Other EU", 40]]),
            h2("2. Travel class"),
            para("""
            Flights with a scheduled duration below four hours are booked in economy class. Longer flights may
            be booked in premium economy with prior approval from the responsible vice president. Rail travel
            is booked in second class within Germany and Hungary and in standard class in Denmark.
            """),
            h2("3. Accommodation"),
            table(["City", "Maximum per night (EUR)"],
                  [["Budapest", 120], ["München", 180], ["København", 195], ["Other", 160]]),
            h2("4. Approval and reimbursement"),
            numbered([
                "Travel is approved in advance by the line manager in the travel system.",
                "Expense claims are submitted within 30 days of the end of the trip.",
                "Receipts are required for every item above 25 EUR.",
                "Reimbursement is paid with the following month's salary run.",
            ]),
            h2("5. Not reimbursable"),
            bullets(["Fines and penalties.", "Mini-bar and in-room entertainment.",
                     "Travel insurance purchased separately from the corporate policy.",
                     "Upgrades that were not approved in advance."]),
        )))

    docs.append(Doc(
        DRIVE, "policies/Utazasi_szabalyzat_v3_HU.pdf", "Utazási és költségtérítési szabályzat v3 (HU)",
        "pdf", language="hu", tags=["/hr", "difficulty:multilingual"],
        blocks=[
            ("h2", "Dokumentum adatai"),
            ("", f"Verzió: 3. Hatályos: {pol['pol_travel']['effective']}. "
                 f"Tulajdonos: {b.who('p_kovacs')}. Ez a szabályzat az angol nyelvű mesterverzió "
                 f"hiteles magyar fordítása."),
            ("h2", "1. Napidíj"),
            ("", "A napidíj mértéke Magyarországon 32 EUR/nap, Németországban 38 EUR/nap, "
                 "Dániában 45 EUR/nap, egyéb európai uniós országban 40 EUR/nap."),
            ("h2", "2. Utazási osztály"),
            ("", "A négy óránál rövidebb menetrend szerinti repülőutakat turistaosztályon kell foglalni. "
                 "Ennél hosszabb utak esetén a felelős alelnök előzetes jóváhagyásával premium economy "
                 "osztály is választható. Vonaton Magyarországon és Németországban másodosztály, "
                 "Dániában standard osztály jár."),
            ("h2", "3. Szállás"),
            ("", "Éjszakánkénti felső határ: Budapest 120 EUR, München 180 EUR, Koppenhága 195 EUR, "
                 "egyéb 160 EUR."),
            ("h2", "4. Jóváhagyás és elszámolás"),
            ("", "Az utazást előzetesen a közvetlen vezető hagyja jóvá az utazási rendszerben. "
                 "A költségelszámolást az utazás befejezésétől számított 30 napon belül kell benyújtani. "
                 "25 EUR feletti tételekhez számla szükséges. A térítés a következő havi bérrel kerül "
                 "kifizetésre."),
            ("h2", "5. Nem téríthető tételek"),
            ("", "Bírságok és pótdíjak; minibár és szobai szórakoztatás; a vállalati biztosításon kívül "
                 "külön vásárolt utasbiztosítás; előzetesen jóvá nem hagyott felminősítések."),
        ]))

    docs.append(Doc(
        DRIVE, "policies/Reisekostenrichtlinie_v3_DE.pdf", "Reise- und Spesenrichtlinie v3 (DE)",
        "pdf", language="de", tags=["/hr", "difficulty:multilingual"],
        blocks=[
            ("h2", "Dokumentinformationen"),
            ("", f"Version: 3. Gültig ab: {pol['pol_travel']['effective']}. "
                 f"Verantwortlich: {b.who('p_kovacs')}. Diese Richtlinie ist die verbindliche deutsche "
                 f"Fassung der englischsprachigen Originalversion."),
            ("h2", "1. Tagespauschale"),
            ("", "Die Tagespauschale beträgt in Ungarn 32 EUR pro Tag, in Deutschland 38 EUR pro Tag, "
                 "in Dänemark 45 EUR pro Tag und in sonstigen EU-Ländern 40 EUR pro Tag."),
            ("h2", "2. Reiseklasse"),
            ("", "Flüge mit einer planmäßigen Dauer unter vier Stunden werden in der Economy Class gebucht. "
                 "Längere Flüge können mit vorheriger Zustimmung des zuständigen Vice President in der "
                 "Premium Economy gebucht werden. Bahnreisen erfolgen in Deutschland und Ungarn in der "
                 "zweiten Klasse, in Dänemark in der Standardklasse."),
            ("h2", "3. Übernachtung"),
            ("", "Höchstsätze pro Nacht: Budapest 120 EUR, München 180 EUR, Kopenhagen 195 EUR, "
                 "sonstige 160 EUR."),
            ("h2", "4. Genehmigung und Abrechnung"),
            ("", "Reisen werden vorab von der Führungskraft im Reisesystem genehmigt. Die Abrechnung ist "
                 "innerhalb von 30 Tagen nach Reiseende einzureichen. Für Positionen über 25 EUR sind "
                 "Belege erforderlich. Die Erstattung erfolgt mit der Gehaltsabrechnung des Folgemonats."),
            ("h2", "5. Nicht erstattungsfähig"),
            ("", "Bußgelder und Strafen; Minibar und Zimmerunterhaltung; separat abgeschlossene "
                 "Reiseversicherungen außerhalb der Konzernpolice; nicht vorab genehmigte Upgrades."),
        ]))

    docs.append(Doc(
        DRIVE, "policies/Rejsepolitik_v3_DA.pdf", "Rejse- og udgiftspolitik v3 (DA)",
        "pdf", language="da", tags=["/hr", "difficulty:multilingual"],
        blocks=[
            ("h2", "Dokumentoplysninger"),
            ("", f"Version: 3. Gældende fra: {pol['pol_travel']['effective']}. "
                 f"Ansvarlig: {b.who('p_kovacs')}. Denne politik er den autoriserede danske oversættelse "
                 f"af den engelske masterversion."),
            ("h2", "1. Dagpenge"),
            ("", "Dagpengesatsen er 32 EUR pr. dag i Ungarn, 38 EUR pr. dag i Tyskland, "
                 "45 EUR pr. dag i Danmark og 40 EUR pr. dag i øvrige EU-lande."),
            ("h2", "2. Rejseklasse"),
            ("", "Flyrejser med en planlagt varighed under fire timer bookes på economy class. Længere "
                 "rejser kan bookes på premium economy efter forudgående godkendelse fra den ansvarlige "
                 "vice president. Togrejser foretages på anden klasse i Tyskland og Ungarn og på "
                 "standardklasse i Danmark."),
            ("h2", "3. Overnatning"),
            ("", "Maksimum pr. nat: Budapest 120 EUR, München 180 EUR, København 195 EUR, øvrige 160 EUR."),
            ("h2", "4. Godkendelse og refusion"),
            ("", "Rejser godkendes på forhånd af nærmeste leder i rejsesystemet. Udgiftsopgørelsen indsendes "
                 "senest 30 dage efter rejsens afslutning. Der kræves kvittering for alle poster over "
                 "25 EUR. Refusion udbetales sammen med den følgende måneds løn."),
            ("h2", "5. Ikke refunderbart"),
            ("", "Bøder og gebyrer; minibar og underholdning på værelset; rejseforsikring købt separat uden "
                 "for koncernpolicen; opgraderinger uden forudgående godkendelse."),
        ]))

    docs.append(Doc(
        DRIVE, "policies/Performance_Management_Guideline_v2.md",
        "Performance management guideline v2", "md", tags=["/hr"],
        body=md(
            h1("Performance Management Guideline — version 2"),
            kv([("Effective", pol["pol_perf"]["effective"]), ("Owner", b.who("p_kovacs"))]),
            h2("1. Cycle"),
            para(pol["pol_perf"]["key_rule"]),
            h2("2. Rating scale"),
            table(["Rating", "Meaning", "Expected distribution"],
                  [["1 — outstanding", "Consistently exceeds every expectation", "up to 10%"],
                   ["2 — strong", "Exceeds expectations in most areas", "25–35%"],
                   ["3 — fully effective", "Meets all expectations", "45–55%"],
                   ["4 — developing", "Meets some expectations, development plan required", "up to 15%"],
                   ["5 — not effective", "Formal performance improvement plan", "up to 5%"]]),
            h2("3. Link to pay"),
            para("""
            The salary review takes effect on 1 April. The individual increase is a function of the rating and
            of the position within the salary band. Employees below the band midpoint with a rating of 1 or 2
            receive priority. Bonus payout is 60% company performance and 40% individual performance, with a
            target of 12% of base salary for managers.
            """),
            h2("4. Calibration"),
            para("""
            Ratings are calibrated at function level before they are communicated. Calibration meetings are
            held in the first two weeks of November and are chaired by the HR Director.
            """),
        )))

    docs.append(Doc(
        DRIVE, "onboarding/Onboarding_Checklist.md", "Onboarding checklist", "md", tags=["/hr"],
        body=md(
            h1("Onboarding Checklist"),
            kv([("Owner", b.who("p_kovacs")), ("Revision", "2026-02")]),
            h2("Before day one"),
            bullets([
                "Signed employment contract returned and filed.",
                "Equipment ordered: laptop, monitor, headset, access badge.",
                "Accounts requested: identity, mail, HR system, drive access matching the role.",
                "Buddy assigned by the hiring manager.",
            ]),
            h2("Day one"),
            bullets([
                "Welcome session with HR: handbook, code of conduct, whistleblowing channel.",
                "Information security induction, including mandatory multi-factor authentication setup.",
                "Site safety induction for anyone entering production or laboratory areas.",
            ]),
            h2("First 30 days"),
            bullets([
                "Development plan agreed with the manager.",
                "Product introduction: VoltStack 2 and GC-3000.",
                "Data protection training completed and recorded.",
            ]),
            h2("First 90 days"),
            bullets([
                "Probation review with the manager and HR.",
                "Feedback session on the onboarding experience.",
            ]),
        )))

    # ------------------------------------------------------------ headcount
    hc_rows = [["Function", "HU", "DE", "DK", "Total", "Plan year end"]]
    for func, hu, de, dk, plan in [
        ("Engineering", 62, 24, 10, 104), ("Manufacturing", 0, 26, 0, 28),
        ("Service", 6, 5, 17, 32), ("Sales & Marketing", 14, 4, 6, 26),
        ("Finance", 12, 1, 1, 15), ("HR", 5, 1, 1, 8),
        ("IT & Security", 9, 0, 0, 10), ("Management & admin", 10, 0, 0, 5),
    ]:
        hc_rows.append([func, hu, de, dk, hu + de + dk, plan])
    hc_rows.append(["TOTAL", 118, 61, 35, 214, 228])
    docs.append(Doc(
        DRIVE, "planning/Headcount_Plan_2026.xlsx", "Headcount plan 2026", "xlsx",
        tags=["/hr", "difficulty:xlsx"],
        sheets={
            "Headcount": hc_rows,
            "Open positions": [["Position", "Entity", "Priority", "Opened", "Status"],
                               ["Senior firmware engineer", "HU", "high", "2026-03-02", "interviewing"],
                               ["Power electronics engineer", "DE", "high", "2026-02-16", "offer out"],
                               ["Field service technician", "DK", "high", "2026-04-07", "interviewing"],
                               ["Test engineer", "HU", "medium", "2026-05-04", "sourcing"],
                               ["Cost controller", "HU", "medium", "2026-05-18", "sourcing"],
                               ["Quality engineer", "DE", "medium", "2026-06-01", "sourcing"],
                               ["Bid manager", "DE", "medium", "2026-06-15", "sourcing"],
                               ["Data engineer", "HU", "low", "2026-06-29", "on hold"]],
            "Assumptions": [["Assumption", "Value"],
                            ["Headcount at 30 June 2026", hr["headcount_fte"]],
                            ["Plan at year end", hr["headcount_plan_year_end"]],
                            ["Voluntary attrition H1 (annualised)",
                             f"{hr['voluntary_attrition_h1_pct']}%"],
                            ["Average time to hire (days)", hr["average_time_to_hire_days"]],
                            ["Open positions", hr["open_positions"]]],
        }))

    docs.append(Doc(
        DRIVE, "reports/Attrition_Report_H1_2026.md", "Attrition report H1 2026", "md",
        tags=["/hr", "/report"],
        body=md(
            h1("Attrition Report — H1 2026"),
            kv([("Owner", b.who("p_kovacs")), ("As of", b.as_of)]),
            h2("Summary"),
            table(["Metric", "H1 2026", "H1 2025"],
                  [["Voluntary attrition (annualised)", f"{hr['voluntary_attrition_h1_pct']}%", "11.2%"],
                   ["Involuntary exits", 2, 4],
                   ["Average tenure at exit", "2.6 years", "2.1 years"],
                   ["Regretted leavers", 5, 9]]),
            h2("By entity"),
            table(["Entity", "Leavers", "Annualised rate"],
                  [["Budapest", 6, "10.2%"], ["München", 2, "6.6%"], ["København", 1, "5.7%"]]),
            h2("Exit interview themes"),
            bullets([
                "Compensation was cited in four of nine exit interviews, mainly in the Budapest engineering "
                "team where market rates moved faster than the April review.",
                "Career progression was cited in three interviews.",
                "Commuting and on-site presence expectations were cited twice.",
            ]),
            h2("Actions"),
            numbered([
                "Off-cycle market adjustment proposal for the Budapest engineering bands, to be tabled with "
                "the FY2027 budget (owner: Anna Kovács).",
                "Introduce a technical career track parallel to the management track (owner: Péter Halász).",
                "Review anchor-day arrangements team by team (owner: line managers).",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "reports/Recruitment_Status_2026-07.md", "Recruitment status July 2026", "md",
        tags=["/hr", "/report"],
        body=md(
            h1("Recruitment Status — July 2026"),
            kv([("Owner", b.who("p_kovacs")), ("Open positions", hr["open_positions"]),
                ("Average time to hire", f"{hr['average_time_to_hire_days']} days")]),
            h2("Pipeline"),
            table(["Stage", "Candidates"],
                  [["Applied", 168], ["Screened", 54], ["First interview", 27],
                   ["Technical interview", 12], ["Offer stage", 3], ["Accepted", 2]]),
            h2("Commentary"),
            para(f"""
            Hiring runs approximately two months behind the plan. At 30 June 2026 headcount stood at
            {hr['headcount_fte']} FTE against a year-end plan of {hr['headcount_plan_year_end']}. The two
            highest-priority engineering roles are at offer stage. The power electronics role in München has
            been open since 16 February 2026 and is the longest-running vacancy.
            """),
        )))

    docs.append(Doc(
        DRIVE, "notes/1on1_Notes_Kiss_2026-06.md", "1:1 notes — Márton Kiss, June 2026", "md",
        tags=["/summary", "/hr"],
        body=md(
            h1("1:1 Notes — Márton Kiss with Péter Halász, 24 June 2026"),
            kv([("Classification", "Confidential — HR and line manager"),
                ("Next 1:1", "2026-07-22")]),
            h2("Discussion"),
            bullets([
                "Meridian is tracking well; the beta freeze landed two days early and the conformance "
                "observations are closed.",
                "Márton raised concern about the type approval dossier workload colliding with the Helios "
                "commissioning support requests in September.",
                "Agreed to protect two days per week for the dossier until submission on 15 October.",
                "Career: interest in a principal engineer track rather than people management. Noted for "
                "the technical career track work.",
            ]),
            h2("Actions"),
            table(["Action", "Owner", "Due"],
                  [["Route Helios firmware questions through the service desk first",
                    "Lars Nygaard", "2026-07-10"],
                   ["Draft the principal engineer role profile", "Anna Kovács", "2026-09-30"]]),
        )))

    docs.append(Doc(
        DRIVE, "reports/Performance_Review_Summary_2025.md",
        "Performance review summary 2025", "md", tags=["/hr"],
        body=md(
            h1("Performance Review Summary — 2025 cycle"),
            kv([("Cycle closed", "2025-11-30"), ("Owner", b.who("p_kovacs")),
                ("Population", "196 employees eligible")]),
            h2("Rating distribution"),
            table(["Rating", "Count", "Share", "Guideline"],
                  [["1 — outstanding", 17, "8.7%", "up to 10%"],
                   ["2 — strong", 58, "29.6%", "25–35%"],
                   ["3 — fully effective", 99, "50.5%", "45–55%"],
                   ["4 — developing", 19, "9.7%", "up to 15%"],
                   ["5 — not effective", 3, "1.5%", "up to 5%"]]),
            h2("Outcome"),
            para(f"""
            The April 2026 salary review applied an average increase of
            {hr['compensation_review']['h1_2026_increase_pct']}%. Three employees entered a formal
            performance improvement plan; two completed it successfully by June 2026. Calibration was
            completed at function level in the first two weeks of November 2025.
            """),
            h2("Note"),
            para("""
            This summary contains aggregated data only. Individual review records and individual pay data are
            held in the HR system and are not stored in any document repository.
            """),
        )))

    docs.append(Doc(
        DRIVE, "contracts/Employment_Contract_Template.docx",
        "Employment contract template (HU entity)", "docx", tags=["/hr", "/legal"],
        blocks=[
            ("h2", "Template control"),
            ("", "Template version 4, approved 2026-01-15. Applies to Voltara Energy Group Zrt. "
                 "Local counsel review completed. Owner: Anna Kovács."),
            ("h2", "1. Parties"),
            ("", "This contract is concluded between Voltara Energy Group Zrt., registered seat Budapest, "
                 "company registration number Cg. 01-10-049217, as employer, and the employee named in "
                 "Schedule 1."),
            ("h2", "2. Position and place of work"),
            ("", "The position, reporting line and place of work are set out in Schedule 1. The place of "
                 "work is the Budapest office; remote work follows the Remote and Hybrid Work Policy."),
            ("h2", "3. Working time"),
            ("", "Standard working time is 40 hours per week. Core hours are 10:00 to 15:00."),
            ("h2", "4. Remuneration"),
            ("", "The gross base salary is stated in Schedule 1 and is paid monthly in arrears. Salary "
                 "review is annual with effect from 1 April. Bonus eligibility, where applicable, follows "
                 "the company bonus scheme."),
            ("h2", "5. Annual leave"),
            ("", "The employee is entitled to 25 working days of annual leave, plus statutory additional "
                 "days where applicable."),
            ("h2", "6. Probation and notice"),
            ("", "The probationary period is three months. After probation the notice period is 30 days for "
                 "the employee and follows the Labour Code for the employer."),
            ("h2", "7. Confidentiality and intellectual property"),
            ("", "The employee keeps company and customer information confidential during and after "
                 "employment. Inventions and works created in the course of employment belong to the "
                 "employer in accordance with applicable law."),
            ("h2", "8. Policies"),
            ("", "The Employee Handbook, the Code of Conduct, the Travel and Expense Policy and the "
                 "information security rules form part of the employment relationship."),
        ]))

    docs.append(Doc(
        DRIVE, "works-council/Betriebsvereinbarung_2025.pdf",
        "Betriebsvereinbarung mit dem Betriebsrat (2025)", "pdf", language="de",
        tags=["/hr", "/legal"],
        blocks=[
            ("h2", "Rahmendaten"),
            ("", f"Betriebsvereinbarung zwischen der Voltara Systems GmbH, München, und dem Betriebsrat. "
                 f"Abgeschlossen am {d['hr']['works_council']['agreement_date']}. "
                 f"Anzahl der Sitze: {d['hr']['works_council']['seats']}."),
            ("h2", "1. Geltungsbereich"),
            ("", "Diese Vereinbarung gilt für alle Arbeitnehmerinnen und Arbeitnehmer der Voltara Systems "
                 "GmbH mit Ausnahme der leitenden Angestellten im Sinne des Betriebsverfassungsgesetzes."),
            ("h2", "2. Arbeitszeit"),
            ("", "Die regelmäßige wöchentliche Arbeitszeit beträgt 40 Stunden. Gleitzeit ist zwischen "
                 "06:00 und 20:00 Uhr möglich, mit einer Kernzeit von 10:00 bis 15:00 Uhr. Ein "
                 "Arbeitszeitkonto wird geführt; der Ausgleichszeitraum beträgt zwölf Monate."),
            ("h2", "3. Mobiles Arbeiten"),
            ("", "Mobiles Arbeiten ist an bis zu drei Tagen pro Woche möglich. Mindestens zwei Tage pro "
                 "Woche ist Anwesenheit am Standort vorgesehen. Für Produktions- und Laborarbeitsplätze "
                 "gilt diese Regelung nicht."),
            ("h2", "4. Technische Einrichtungen"),
            ("", "Die Einführung technischer Einrichtungen, die zur Überwachung von Verhalten oder Leistung "
                 "geeignet sind, bedarf der Zustimmung des Betriebsrats. Auswertungen auf Ebene einzelner "
                 "Beschäftigter sind ausgeschlossen."),
            ("h2", "5. Laufzeit"),
            ("", "Diese Vereinbarung tritt am 1. Oktober 2025 in Kraft und läuft auf unbestimmte Zeit. Sie "
                 "kann mit einer Frist von drei Monaten zum Jahresende gekündigt werden."),
        ]))

    # ------------------------------------------------------------- training
    tr = hr["training"]
    docs.append(Doc(
        DRIVE, "training/Training_Plan_2026.md", "Training plan 2026", "md", tags=["/hr", "/report"],
        body=md(
            h1("Training Plan — 2026"),
            kv([("Owner", b.who("p_kovacs")),
                ("Budget", f"EUR {tr['budget_2026_keur']} thousand"),
                ("Spent in H1", f"EUR {tr['h1_actual_keur']} thousand"),
                ("Average training days per employee, H1", tr["average_days_per_employee_h1"])]),
            h2("Mandatory training"),
            table(["Course", "Cadence", "Completion at 30 June 2026"],
                  [[m["name"], m["cadence"], f"{m['completion_h1_pct']}%"] for m in tr["mandatory"]]),
            para("""
            Completion below 100 percent is followed up by the line manager. The data protection course is
            the furthest behind, mainly among field service staff who were on site during the campaign
            window; a second window opens in September 2026.
            """),
            h2("Development programmes"),
            bullets(tr["programmes"]),
            h2("Budget"),
            table(["Category", "Budget (kEUR)", "H1 actual (kEUR)"],
                  [["Mandatory compliance training", 40, 22],
                   ["Technical and certification", 110, 47],
                   ["Leadership and management", 55, 19],
                   ["Language training", 20, 6],
                   ["Conferences", 15, 2],
                   ["TOTAL", tr["budget_2026_keur"], tr["h1_actual_keur"]]]),
        )))

    hs = hr["health_and_safety"]
    docs.append(Doc(
        DRIVE, "reports/Health_and_Safety_Report_H1_2026.md",
        "Health and safety report H1 2026", "md", tags=["/hr", "/compliance", "/report"],
        body=md(
            h1("Health and Safety Report — H1 2026"),
            kv([("As of", hs["as_of"]), ("Owner", b.who("p_ruhland")),
                ("Reviewed by", b.who("p_kovacs"))]),
            h2("Key figures"),
            table(["Indicator", "H1 2026", "H1 2025"],
                  [["Lost time incidents", hs["lost_time_incidents_h1"], 1],
                   ["Recordable incidents", hs["recordable_incidents_h1"], 3],
                   ["Near misses reported", hs["near_misses_h1"], 7],
                   ["Lost time injury rate", hs["lost_time_injury_rate"], 1.2]]),
            h2("Incidents"),
            para(hs["note"]),
            h2("Preventive activity"),
            bullets([
                f"Emergency drill: {hs['last_drill']}.",
                "Site safety induction completion is 100 percent before first site entry.",
                "Near-miss reporting rose from 7 to 11, which is read as better reporting culture rather "
                "than a deterioration.",
                "Battery-specific hazard training refreshed for all commissioning staff in April 2026.",
            ]),
            h2("Focus for H2"),
            bullets([
                "Winter working procedures for the Esbjerg site, given the revised commissioning window.",
                "Machine safety assessment for the second assembly line before qualification.",
            ]),
        )))

    dv = hr["diversity"]
    docs.append(Doc(
        DRIVE, "reports/Diversity_Report_2025.md", "Diversity and inclusion report 2025", "md",
        tags=["/hr", "/strategy"],
        body=md(
            h1("Diversity and Inclusion Report"),
            kv([("As of", dv["as_of"]), ("Owner", b.who("p_kovacs"))]),
            h2("Representation"),
            table(["Indicator", "Value"],
                  [["Women, total workforce", f"{dv['women_pct_total']}%"],
                   ["Women, engineering", f"{dv['women_pct_engineering']}%"],
                   ["Women, management positions", f"{dv['women_pct_management']}%"],
                   ["Nationalities represented", dv["nationalities"]]]),
            h2("Pay gap"),
            table(["Measure", "Value", "Meaning"],
                  [["Unadjusted gender pay gap", f"{dv['pay_gap_unadjusted_pct']}%",
                    "difference in average pay across the whole workforce"],
                   ["Adjusted gender pay gap", f"{dv['pay_gap_adjusted_pct']}%",
                    "same role, band and location"]]),
            para("""
            The unadjusted gap is driven by role mix: engineering is the largest and best-paid function and
            has the lowest share of women. The adjusted gap of 1.9 percent is within the range explained by
            tenure and is reviewed at each salary round.
            """),
            h2("Actions"),
            numbered([
                "Gender-neutral wording and a salary band reference in every job advertisement.",
                "At least one woman on every interview panel for a management role.",
                "Pay equity check built into the April salary review, per band and location.",
                "Support for the technical career track so progression does not require managing people.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "reports/Exit_Interview_Summary_H1_2026.md",
        "Exit interview summary H1 2026", "md", tags=["/hr", "/summary"],
        body=md(
            h1("Exit Interview Summary — H1 2026"),
            kv([("Owner", b.who("p_kovacs")), ("Interviews conducted", 9),
                ("Classification", "Confidential — HR")]),
            h2("Themes"),
            table(["Theme", "Mentions", "Comment"],
                  [["Compensation", 4, "Mainly Budapest engineering; market rates moved faster than the "
                                       "April review"],
                   ["Career progression", 3, "No senior technical path without moving into management"],
                   ["On-site presence expectation", 2, "Commuting time to the Budapest office"],
                   ["Manager relationship", 1, ""],
                   ["Relocation for personal reasons", 2, "Not actionable"]]),
            h2("What leavers said worked well"),
            bullets([
                "Technical quality of the work and of colleagues.",
                "Clarity of the product direction.",
                "Flexibility within the two-day on-site rule.",
            ]),
            h2("Follow-up"),
            para("""
            The compensation theme feeds the off-cycle market adjustment proposal for the Budapest
            engineering bands, which will be tabled with the 2027 budget. The career progression theme is
            addressed by the technical career track; the role profile is due on 30 September 2026.
            """),
            h2("Note"),
            para("""
            This summary is aggregated. Individual exit interview records are held in the HR system and are
            not stored in any document repository.
            """),
        )))

    docs.append(Doc(
        DRIVE, "policies/Munkavallaloi_kezikonyv_kivonat_HU.md",
        "Munkavállalói kézikönyv – kivonat (HU)", "md", language="hu",
        tags=["/hr", "difficulty:multilingual"],
        body=md(
            h1("Munkavállalói kézikönyv — kivonat (magyar entitás)"),
            kv([("Verzió", "v6"), ("Hatályos", pol["pol_handbook"]["effective"]),
                ("Tulajdonos", b.who("p_kovacs")),
                ("Érvényes", "Voltara Energy Group Zrt., Budapest")]),
            h2("Munkaidő"),
            para("""
            A teljes munkaidő heti 40 óra. A törzsidő 10:00 és 15:00 között van. A túlmunkát előzetesen a
            közvetlen vezetővel kell egyeztetni; a kompenzáció elsődlegesen szabadidőben történik.
            """),
            h2("Távmunka"),
            para("""
            A távmunka-szabályzat v2 alapján hetente legalább két nap irodai jelenlét kötelező. A teljes
            távmunkához a felelős alelnök írásos jóváhagyása szükséges, és félévente felül kell vizsgálni.
            """),
            h2("Szabadság"),
            para("""
            Az éves alapszabadság 25 munkanap, a törvényes pótszabadságokon felül. A háromnál hosszabb
            szabadságot legalább két héttel előre kell kérni a HR-rendszerben.
            """),
            h2("Utazás"),
            para("""
            A napidíj Magyarországon 32 EUR/nap, Németországban 38 EUR/nap, Dániában 45 EUR/nap. A négy
            óránál rövidebb repülőutak turistaosztályon foglalandók.
            """),
            h2("Teljesítményértékelés"),
            para("""
            Az éves ciklus november 30-án zárul, júniusi félidős egyeztetéssel. A béremelés április 1-jén lép
            hatályba.
            """),
            h2("Bejelentések"),
            para("""
            Aggály jelezhető a közvetlen vezetőnek, a HR-igazgatónak, vagy névtelenül a külső jogi tanácsadó
            által üzemeltetett csatornán. A jóhiszemű bejelentővel szembeni megtorlás fegyelmi vétség.
            """),
        )))

    docs.append(Doc(
        DRIVE, "policies/Mitarbeiterhandbuch_Auszug_DE.md",
        "Mitarbeiterhandbuch – Auszug (DE)", "md", language="de",
        tags=["/hr", "difficulty:multilingual"],
        body=md(
            h1("Mitarbeiterhandbuch — Auszug (deutsche Gesellschaft)"),
            kv([("Version", "v6"), ("Gültig ab", pol["pol_handbook"]["effective"]),
                ("Verantwortlich", b.who("p_kovacs")),
                ("Geltungsbereich", "Voltara Systems GmbH, München")]),
            h2("Arbeitszeit"),
            para("""
            Die regelmäßige wöchentliche Arbeitszeit beträgt 40 Stunden, die Kernzeit liegt zwischen 10:00
            und 15:00 Uhr. Ein Arbeitszeitkonto wird gemäß der Betriebsvereinbarung vom 1. Oktober 2025
            geführt; der Ausgleichszeitraum beträgt zwölf Monate.
            """),
            h2("Mobiles Arbeiten"),
            para("""
            Mobiles Arbeiten ist an bis zu drei Tagen pro Woche möglich, mindestens zwei Tage pro Woche ist
            Anwesenheit am Standort vorgesehen. Für Produktions- und Laborarbeitsplätze gilt diese Regelung
            nicht.
            """),
            h2("Urlaub"),
            para("""
            Der Urlaubsanspruch beträgt 30 Arbeitstage pro Kalenderjahr. Urlaub von mehr als drei Tagen ist
            mindestens zwei Wochen im Voraus im HR-System zu beantragen.
            """),
            h2("Reisekosten"),
            para("""
            Die Tagespauschale beträgt in Ungarn 32 EUR, in Deutschland 38 EUR und in Dänemark 45 EUR pro
            Tag. Flüge unter vier Stunden werden in der Economy Class gebucht.
            """),
            h2("Betriebsrat"),
            para("""
            Am Standort München besteht ein Betriebsrat mit fünf Sitzen. Maßnahmen, die der Mitbestimmung
            unterliegen, werden vor der Umsetzung mit dem Betriebsrat abgestimmt.
            """),
        )))

    docs.append(Doc(
        DRIVE, "policies/Medarbejderhaandbog_uddrag_DA.md",
        "Medarbejderhåndbog – uddrag (DA)", "md", language="da",
        tags=["/hr", "difficulty:multilingual"],
        body=md(
            h1("Medarbejderhåndbog — uddrag (det danske selskab)"),
            kv([("Version", "v6"), ("Gældende fra", pol["pol_handbook"]["effective"]),
                ("Ansvarlig", b.who("p_kovacs")),
                ("Omfang", "Voltara Nordic A/S, København")]),
            h2("Arbejdstid"),
            para("""
            Den normale ugentlige arbejdstid er 37 timer. Kernetiden er fra 10:00 til 15:00. Overarbejde
            aftales på forhånd med nærmeste leder og afspadseres som udgangspunkt.
            """),
            h2("Hjemmearbejde"),
            para("""
            Politikken for hjemmearbejde kræver mindst to dage om ugen på kontoret. Fuldt hjemmearbejde
            kræver skriftlig godkendelse fra den ansvarlige vice president og gennemgås hvert halve år.
            """),
            h2("Ferie"),
            para("""
            Medarbejderne har fem ugers ferie samt feriefridage. Ferie på mere end tre dage anmodes mindst to
            uger i forvejen i HR-systemet.
            """),
            h2("Rejseudgifter"),
            para("""
            Dagpengesatsen er 32 EUR i Ungarn, 38 EUR i Tyskland og 45 EUR i Danmark pr. dag. Flyrejser under
            fire timer bookes på economy class.
            """),
            h2("Feltservice"),
            para("""
            For medarbejdere i feltservice gælder desuden sikkerhedsinstruktion før første adgang til et
            anlæg samt de særlige regler for arbejde på Esbjerg-anlægget i vinterperioden.
            """),
        )))

    return docs
