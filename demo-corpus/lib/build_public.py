"""Public drive: company profile, press releases, product material, strategy, grants."""
from __future__ import annotations

from .common import Bible, Doc, bullets, h1, h2, h3, kv, md, numbered, para, table

DRIVE = "drive_public"


def build(b: Bible) -> list[Doc]:
    d = b.d
    co = d["company"]
    fy25 = b.years[2025]
    tundra = b.projects["prj_tundra"]
    ib_sites = d["installed_base"]["sites"]

    def fleet_at(cutoff: str) -> tuple[int, float, int]:
        sub = [s for s in ib_sites if s["commissioned"] <= cutoff]
        return len(sub), round(sum(s["capacity_mwh"] for s in sub), 1), \
            len({s["country"] for s in sub})

    docs: list[Doc] = []

    # ------------------------------------------------------- company profile
    entity_rows = [[e["name"], e["country"], e["city"], e["role"], e["headcount"]]
                   for e in co["legal_entities"]]
    docs.append(Doc(
        DRIVE, "company/Company_Overview_2026.md", "Company overview 2026", "md",
        tags=["/search", "/presentation"],
        body=md(
            h1(f"{co['name']} — Company Overview 2026"),
            kv([("Founded", co["founded"]), ("Headquarters", co["headquarters"]),
                ("Industry", co["industry"]), ("Employees", f"{co['employees_fte']} FTE"),
                ("Revenue FY2025", f"EUR {fy25['revenue_meur']} million")]),
            h2("What we do"),
            para(co["description"]),
            h2("Group structure"),
            table(["Legal entity", "Country", "City", "Role", "Headcount"], entity_rows),
            h2("Leadership"),
            table(["Name", "Role", "Location"],
                  [[b.name(p), b.people[p]["title"], b.people[p]["location"]]
                   for p in ["p_barat", "p_lehmann", "p_sorensen", "p_holm", "p_somogyi"]]),
            h2("Products"),
            bullets([f"{p['name']} — {p['status']}" for p in d["products"]]),
            h2("Certifications"),
            bullets([f"{c['standard']} — {c['scope']} (certified {c['certified']})"
                     for c in d["compliance"]["certifications"]]),
            h2("Key figures"),
            table(["Metric", "FY2024", "FY2025"],
                  [["Revenue (EUR m)", b.years[2024]["revenue_meur"], fy25["revenue_meur"]],
                   ["Gross margin", f"{b.years[2024]['gross_margin_pct']}%",
                    f"{fy25['gross_margin_pct']}%"],
                   ["EBITDA (EUR m)", b.years[2024]["ebitda_meur"], fy25["ebitda_meur"]],
                   ["Employees at year end", 187, 206]]),
        )))

    docs.append(Doc(
        DRIVE, "company/Voltara_Cegbemutato_2026_HU.md", "Voltara cégbemutató 2026 (HU)", "md",
        language="hu", tags=["difficulty:multilingual", "/presentation"],
        body=md(
            h1("Voltara Energy Group — Cégbemutató 2026"),
            kv([("Alapítás éve", co["founded"]), ("Központ", "Budapest"),
                ("Létszám", f"{co['employees_fte']} fő"),
                ("Árbevétel (2025)", f"{fy25['revenue_meur']} millió EUR")]),
            h2("Mivel foglalkozunk"),
            para("""
            A Voltara hálózati méretű akkumulátoros energiatároló rendszereket (BESS) és az ezeket vezérlő
            GC-sorozatú hálózati vezérlőket tervez, gyárt és szervizel. Fő piacaink Skandinávia, a
            német nyelvterület és Közép-Európa. Ügyfeleink áramszolgáltatók, önkormányzati energiacégek és
            ipari telephelyek.
            """),
            h2("Csoportszerkezet"),
            table(["Társaság", "Ország", "Város", "Szerep", "Létszám"], entity_rows),
            h2("Termékeink"),
            bullets([
                "VoltStack 2 — konténeres energiatároló, egységenként 2,5 MWh, sorozatgyártásban.",
                "GC-3000 hálózati vezérlő — a v3.0 firmware fejlesztés alatt, típusengedélyezés 2026 őszén.",
                "Voltara Service Portal — távfelügyelet és karbantartás-ütemezés, 41 aktív telephely.",
            ]),
            h2("Főbb számok"),
            table(["Mutató", "2024", "2025"],
                  [["Árbevétel (millió EUR)", b.years[2024]["revenue_meur"], fy25["revenue_meur"]],
                   ["Fedezeti hányad", f"{b.years[2024]['gross_margin_pct']}%",
                    f"{fy25['gross_margin_pct']}%"],
                   ["EBITDA (millió EUR)", b.years[2024]["ebitda_meur"], fy25["ebitda_meur"]]]),
            h2("Tanúsítványaink"),
            para("""
            ISO/IEC 27001:2022 információbiztonsági irányítási rendszer csoportszinten, valamint
            ISO 9001:2015 a müncheni gyártásra és szervizre.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Voltara_Unternehmensprofil_2026_DE.md", "Voltara Unternehmensprofil 2026 (DE)",
        "md", language="de", tags=["difficulty:multilingual", "/presentation"],
        body=md(
            h1("Voltara Energy Group — Unternehmensprofil 2026"),
            kv([("Gegründet", co["founded"]), ("Hauptsitz", "Budapest"),
                ("Mitarbeitende", f"{co['employees_fte']} VZÄ"),
                ("Umsatz 2025", f"{fy25['revenue_meur']} Mio. EUR")]),
            h2("Was wir tun"),
            para("""
            Voltara entwickelt, fertigt und wartet netzgekoppelte Batteriespeichersysteme sowie die
            Netzregler der GC-Serie. Hauptmärkte sind Skandinavien, der deutschsprachige Raum und
            Mitteleuropa. Zu den Kunden zählen Energieversorger, Stadtwerke und Industriestandorte.
            """),
            h2("Standort München"),
            para("""
            Die Voltara Systems GmbH in München verantwortet Fertigung, Typprüfung, Qualität und Compliance
            sowie den Vertrieb im DACH-Raum. Der Standort beschäftigt 61 Mitarbeitende. Eine zweite
            Montagelinie befindet sich im Bau und soll die Modulausbringung um 40 Prozent erhöhen.
            """),
            h2("Kennzahlen"),
            table(["Kennzahl", "2024", "2025"],
                  [["Umsatz (Mio. EUR)", b.years[2024]["revenue_meur"], fy25["revenue_meur"]],
                   ["Rohertragsmarge", f"{b.years[2024]['gross_margin_pct']}%",
                    f"{fy25['gross_margin_pct']}%"],
                   ["EBITDA (Mio. EUR)", b.years[2024]["ebitda_meur"], fy25["ebitda_meur"]]]),
            h2("Zertifizierungen"),
            para("ISO/IEC 27001:2022 konzernweit sowie ISO 9001:2015 für Fertigung und Service in München."),
        )))

    docs.append(Doc(
        DRIVE, "company/Voltara_Virksomhedsprofil_2026_DA.md", "Voltara virksomhedsprofil 2026 (DA)",
        "md", language="da", tags=["difficulty:multilingual", "/presentation"],
        body=md(
            h1("Voltara Energy Group — Virksomhedsprofil 2026"),
            kv([("Grundlagt", co["founded"]), ("Hovedkontor", "Budapest"),
                ("Medarbejdere", f"{co['employees_fte']} FTE"),
                ("Omsætning 2025", f"{fy25['revenue_meur']} mio. EUR")]),
            h2("Hvad vi laver"),
            para("""
            Voltara udvikler, producerer og servicerer batterilagringsanlæg til elnettet samt GC-seriens
            netregulatorer. Hovedmarkederne er Norden, det tysktalende område og Centraleuropa. Kunderne er
            energiselskaber, kommunale forsyningsselskaber og industrivirksomheder.
            """),
            h2("Voltara Nordic A/S"),
            para("""
            Voltara Nordic A/S i København står for salg, projektlevering og service i Norden og har 35
            medarbejdere. Selskabet leverer i øjeblikket et 40 MWh anlæg til Vestkraft A/S i Esbjerg.
            """),
            h2("Nøgletal"),
            table(["Nøgletal", "2024", "2025"],
                  [["Omsætning (mio. EUR)", b.years[2024]["revenue_meur"], fy25["revenue_meur"]],
                   ["Bruttomargin", f"{b.years[2024]['gross_margin_pct']}%",
                    f"{fy25['gross_margin_pct']}%"],
                   ["EBITDA (mio. EUR)", b.years[2024]["ebitda_meur"], fy25["ebitda_meur"]]]),
            h2("Certificeringer"),
            para("ISO/IEC 27001:2022 for hele koncernen og ISO 9001:2015 for produktion og service i München."),
        )))

    # ------------------------------------------------------- press releases
    press = [
        ("2025-09-22", "Voltara to deliver 40 MWh storage system for Vestkraft",
         """
         Voltara Energy Group has signed an agreement with the Danish utility Vestkraft A/S for the supply,
         installation and commissioning of a 40 MWh battery energy storage system at Esbjerg. The contract has
         a value of EUR 12.4 million and provisional acceptance is scheduled for late 2026. The system is
         based on sixteen VoltStack 2 containerised units under GC-3000 control and will provide frequency
         regulation and peak shaving services to the Danish grid.
         """,
         "This is the largest single order in our history and a strong signal for the Nordic market."),
        ("2026-03-02", "Voltara reports record 2025 results",
         f"""
         Voltara Energy Group today published its audited results for the 2025 financial year. Revenue reached
         EUR {fy25['revenue_meur']} million, up from EUR {b.years[2024]['revenue_meur']} million in 2024, and
         EBITDA improved to EUR {fy25['ebitda_meur']} million, an EBITDA margin of
         {fy25['ebitda_margin_pct']} percent. The auditor issued an unqualified opinion. The company employs
         over two hundred people across Hungary, Germany and Denmark.
         """,
         "Growth came from larger utility-scale projects and from a rising share of recurring service revenue."),
        ("2026-05-14", "Voltara to double module capacity at its München plant",
         """
         Following a decision of its Board of Directors, Voltara Energy Group will invest EUR 4.6 million in a
         second assembly line at its München plant. The investment increases module output by 40 percent and
         is scheduled for completion by the end of the first quarter of 2027. Construction started in June
         2026 and the main equipment order was placed in July 2026.
         """,
         "The expansion removes the single point of failure in our serial production and shortens delivery times."),
        ("2026-01-14", "TUNDRA-STORE project starts under Horizon Europe",
         f"""
         A six-partner consortium coordinated by {tundra['coordinator']} has started the TUNDRA-STORE project
         under the Horizon Europe programme. The project investigates battery performance and degradation
         below minus twenty degrees Celsius and runs for {tundra['duration_months']} months from January 2026.
         The total grant is EUR {tundra['grant_value_meur']} million, of which Voltara receives
         EUR {tundra['voltara_share_meur']} million as work package leader.
         """,
         "Cold-climate performance is a genuine engineering constraint in the Nordic and Baltic markets."),
        ("2025-06-15", "Voltara certified to ISO/IEC 27001:2022",
         """
         Voltara Energy Group has been certified to ISO/IEC 27001:2022 for its group-wide information security
         management system. The certificate, number IS 742 118, was issued by Certiva Assurance Ltd. on
         12 June 2025 and covers the development, manufacture, delivery and remote service of battery energy
         storage systems and grid controllers across all three legal entities.
         """,
         "Our customers operate critical infrastructure; they are entitled to ask how we protect their data."),
        ("2026-07-24", "Voltara places the main equipment order for the München line expansion",
         """
         Voltara Energy Group has placed the main equipment order for the second assembly line at its München
         plant, one week ahead of the internal milestone date. The line is part of the EUR 4.6 million
         investment approved by the Board on 13 May 2026 and is scheduled for qualification by the end of the
         first quarter of 2027. Building works are on schedule for completion in December 2026.
         """,
         "Committing to a delivery date is only credible if the capacity behind it is real."),
    ]
    for date, title, body_text, quote in press:
        docs.append(Doc(
            DRIVE, f"press/Press_Release_{date}.md", title, "md", tags=["/search"],
            body=md(
                h1(title),
                kv([("Date", date), ("Location", "Budapest"), ("Contact", b.who("p_somogyi")),
                    ("Media enquiries", "press@voltara-energy.example")]),
                para(body_text),
                h2("Quote"),
                para(f'"{quote}" said {b.who("p_barat")}.'),
                h2("About Voltara Energy Group"),
                para(co["description"]),
            )))

    # ---------------------------------------------------------- product info
    vs2 = next(p for p in d["products"] if p["id"] == "prod_vs2")
    docs.append(Doc(
        DRIVE, "products/VoltStack2_Product_Overview.md", "VoltStack 2 product overview", "md",
        tags=["/presentation", "/sales"],
        body=md(
            h1("VoltStack 2 — Product Overview"),
            para("""
            VoltStack 2 is a containerised battery energy storage system for grid-scale and industrial
            applications. Each unit provides 2.5 MWh of installed energy in a 20-foot ISO container and can be
            combined into blocks of up to sixteen units behind a single GC-3000 controller.
            """),
            h2("Why customers choose it"),
            bullets([
                f"Round-trip efficiency of {vs2['round_trip_efficiency_pct']} percent at half-rate operation.",
                f"Cycle life of {vs2['cycle_life']} cycles to 80 percent state of health.",
                "Sub-100 millisecond response from idle to full power for frequency services.",
                "Liquid cooling with a closed loop, allowing operation from minus 20 to plus 50 degrees.",
                "Grid-code compliance for EN 50549-2 and IEEE 1547-2018 through the GC-3000.",
                "Remote monitoring and predictive maintenance through the Voltara Service Portal.",
            ]),
            h2("Typical applications"),
            table(["Application", "Typical size", "Key requirement"],
                  [["Frequency containment reserve", "5–40 MWh", "Response time and availability"],
                   ["Peak shaving for industry", "2.5–10 MWh", "Round-trip efficiency"],
                   ["Renewable firming", "10–60 MWh", "Cycle life and depth of discharge"],
                   ["Grid deferral for municipal utilities", "5–20 MWh", "Footprint and noise"]]),
            h2("Delivery"),
            para("""
            Standard delivery is 34 weeks from order confirmation, including factory acceptance testing.
            Installation, commissioning and site acceptance testing are performed by the Voltara field service
            organisation. The standard warranty is 24 months from provisional acceptance and can be extended
            to 60 months with a service agreement.
            """),
        )))

    docs.append(Doc(
        DRIVE, "products/GC3000_Datasheet.md", "GC-3000 grid controller datasheet", "md",
        tags=["/sales", "/search"],
        body=md(
            h1("GC-3000 Grid Controller — Datasheet"),
            kv([("Current production firmware", "v2.6.4, released 8 April 2026"),
                ("Next release", "v3.0, type approval submission due 15 October 2026")]),
            h2("Function"),
            para("""
            The GC-3000 is the control and protection unit for VoltStack storage blocks. It manages power
            set points, grid-code compliance functions, protection and communication with the customer's
            supervisory system.
            """),
            h2("Key data"),
            table(["Parameter", "v2.6.4", "v3.0 (in development)"],
                  [["Control loop cycle", "20 ms", "10 ms"],
                   ["IEC 61850", "client only", "full server, GOOSE and MMS"],
                   ["IEEE 1547-2018 ride-through", "not supported", "category II"],
                   ["Modbus TCP", "supported", "supported"],
                   ["Modbus RTU gateway", "supported", "not supported"],
                   ["Event log capacity", "50,000 entries", "250,000 entries"],
                   ["Redundant pair failover", "not supported", "under 100 ms"],
                   ["Secure boot", "not supported", "hardware root of trust"]]),
            h2("Environment"),
            bullets(["Operating temperature minus 25 to plus 60 degrees Celsius.",
                     "DIN rail mounting, 6 HP width.",
                     "Redundant 24 V DC supply.",
                     "Two 1 Gbit/s fibre ports and two copper ports."]),
        )))

    docs.append(Doc(
        DRIVE, "products/Voltara_Service_Portal_Overview.md", "Voltara Service Portal overview", "md",
        tags=["/data", "/sales"],
        body=md(
            h1("Voltara Service Portal — Overview"),
            para("""
            The Voltara Service Portal is the remote monitoring and maintenance planning system for installed
            VoltStack fleets. It is currently connected to 41 active sites across six countries.
            """),
            h2("Capabilities"),
            bullets([
                "Live state of charge, power and temperature per container and per block.",
                "Automatic alarm routing to the field service organisation with severity-based escalation.",
                "Predictive maintenance indicators based on internal resistance and capacity trends.",
                "Remote firmware distribution for controller updates where the site permits it.",
                "Service history and spare part consumption per site.",
                "Availability reporting against contractual service levels.",
            ]),
            h2("Connectivity and security"),
            para("""
            Sites connect through a customer-provided VPN. The portal never initiates a connection into the
            site network; all sessions are established outbound from the site gateway. Access is role-based
            and every remote command is logged with the operator identity.
            """),
            h2("Service levels"),
            table(["Service level", "Response target", "Coverage"],
                  [["Standard", "next business day", "business hours"],
                   ["Enhanced", "8 hours", "extended hours"],
                   ["Critical", "4 hours", "24 hours, 7 days"]]),
        )))

    # ------------------------------------------------------------- strategy
    docs.append(Doc(
        DRIVE, "strategy/Voltara_Strategy_2026-2028.md", "Voltara strategy 2026-2028", "md",
        tags=["/presentation", "/strategy", "/exec"],
        body=md(
            h1("Voltara Strategy 2026–2028"),
            kv([("Owner", b.who("p_barat")), ("Approved", "Board meeting 2026-02-11"),
                ("Horizon", "three years")]),
            h2("1. Ambition"),
            para(f"""
            Grow from EUR {fy25['revenue_meur']} million of revenue in 2025 to above EUR 90 million in 2028
            while lifting the EBITDA margin above 13 percent, by becoming the default storage partner for
            mid-sized utilities in the Nordics and the German-speaking market.
            """),
            h2("2. Where we play"),
            table(["Segment", "Priority", "Rationale"],
                  [["Municipal and mid-size utilities, DACH", "grow",
                    "Fragmented, underserved by the large integrators, decision cycles we can win"],
                   ["Nordic utilities and independent power producers", "grow",
                    "Existing references and a local delivery organisation"],
                   ["Industrial peak shaving", "selective",
                    "Attractive margin but long sales cycles and high customisation"],
                   ["Residential and commercial small scale", "avoid",
                    "Different cost structure and channel model"]]),
            h2("3. How we win"),
            numbered([
                "Grid-code depth: certified compliance in every market we sell into, delivered through the "
                "GC-3000 rather than through project-specific engineering.",
                "Delivery reliability: a second assembly line and a second qualified cell supplier so that "
                "we can commit to dates and keep them.",
                "Service attach: a five-year service agreement on every system, lifting recurring revenue "
                "from EUR 7.4 million towards EUR 18 million by 2028.",
                "Cold-climate engineering leadership through the TUNDRA-STORE research programme.",
            ]),
            h2("4. What has to be true"),
            bullets([
                "Firmware v3.0 achieves type approval by the end of 2026.",
                "The Orion second line is qualified by 31 March 2027.",
                "A second cell supplier is live before the Nordcell agreement renewal in 2027.",
                "Engineering headcount grows in line with the plan; hiring is currently two months behind.",
            ]),
            h2("5. Financial frame"),
            table(["Year", "Revenue (EUR m)", "EBITDA margin"],
                  [["2025 actual", fy25["revenue_meur"], f"{fy25['ebitda_margin_pct']}%"],
                   ["2026 plan", b.years[2026]["plan_revenue_meur"],
                    f"{b.years[2026]['plan_ebitda_margin_pct']}%"],
                   ["2027 ambition", 74.0, "12.0%"],
                   ["2028 ambition", 92.0, "13.5%"]]),
            h3("Note on scope"),
            para("""
            This document sets the direction for 2026 to 2028. It is not a budget. The detailed budget is
            approved annually by the Board; the 2027 budget is prepared in November 2026.
            """),
        )))

    docs.append(Doc(
        DRIVE, "strategy/TUNDRA-STORE_Grant_Agreement_Summary.md",
        "TUNDRA-STORE grant agreement summary", "md", tags=["/strategy"],
        body=md(
            h1("TUNDRA-STORE — Grant Agreement Summary"),
            kv([("Programme", tundra["programme"]),
                ("Grant agreement number", tundra["grant_agreement"]),
                ("Coordinator", tundra["coordinator"]),
                ("Partners", tundra["consortium_partners"]),
                ("Total grant", f"EUR {tundra['grant_value_meur']} million"),
                ("Voltara share", f"EUR {tundra['voltara_share_meur']} million"),
                ("Start", tundra["start"]), ("Duration", f"{tundra['duration_months']} months"),
                ("Voltara coordinator", b.who("p_weber")),
                ("Technical lead", b.who("p_petrova"))]),
            h2("Objective"),
            para(tundra["scope"]),
            h2("Voltara role"),
            para("""
            Voltara leads work package 3, which covers system-level thermal management strategies and the
            translation of the degradation model into design guidelines for containerised systems. Voltara
            also contributes test hardware to the cold chamber campaign in Trondheim from November 2026.
            """),
            h2("Reporting obligations"),
            bullets([
                "Deliverable D1.1 requirements report — submitted 26 June 2026, due 30 June 2026.",
                "First periodic report — due 28 February 2027.",
                "Financial statements per reporting period, certified where the threshold applies.",
                "Open access publication of measurement data within six months of the relevant work package.",
            ]),
            h2("Funding rate and cost eligibility"),
            para("""
            The funding rate for the innovation activities is 70 percent for profit-making entities.
            Personnel costs are claimed on actual hours at the certified hourly rate; indirect costs are
            claimed at the flat rate of 25 percent of eligible direct costs.
            """),
        )))

    docs.append(Doc(
        DRIVE, "strategy/Grant_Pipeline_2026-2027.md", "Grant pipeline 2026-2027", "md",
        tags=["/strategy"],
        body=md(
            h1("Grant and Programme Pipeline 2026–2027"),
            kv([("Owner", b.who("p_weber")), ("Last update", "2026-07-08")]),
            h2("Active"),
            table(["Programme", "Project", "Voltara share (EUR)", "Status"],
                  [["Horizon Europe HORIZON-CL5-2025-D3-02", "TUNDRA-STORE", "460,000", "running"]]),
            h2("Submitted"),
            table(["Programme", "Working title", "Requested (EUR)", "Decision expected"],
                  [["Innovation Fund small-scale", "Second-life module reuse pilot", "780,000",
                    "2026-11-15"],
                   ["Bavarian state programme BayVFP", "Digital twin for storage commissioning", "310,000",
                    "2026-09-30"]]),
            h2("Under preparation"),
            table(["Programme", "Working title", "Target call", "Owner"],
                  [["Horizon Europe CL5 2027", "Grid-forming control for weak grids", "2027 call",
                    b.name("p_kiss")],
                   ["Danish EUDP", "Cold-climate commissioning methods", "autumn 2026",
                    b.name("p_petrova")]]),
            h2("Principles"),
            bullets([
                "Only apply where the work is on the product roadmap regardless of funding.",
                "Never accept a consortium role that requires disclosure of cell supplier terms.",
                "Cap total grant-funded effort at 12 percent of engineering capacity.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "strategy/Impact_Report_2025.md", "Impact report 2025", "md",
        tags=["/strategy", "/presentation"],
        body=md(
            h1("Impact Report 2025"),
            kv([("Reporting year", 2025), ("Owner", b.who("p_weber")), ("Published", "2026-04-15")]),
            h2("Installed base"),
            table(["Metric", "2024", "2025"],
                  [["Cumulative installed capacity (MWh)",
                    fleet_at("2024-12-31")[1], fleet_at("2025-12-31")[1]],
                   ["Connected sites", fleet_at("2024-12-31")[0], fleet_at("2025-12-31")[0]],
                   ["Countries", fleet_at("2024-12-31")[2], fleet_at("2025-12-31")[2]]]),
            h2("Estimated system effects"),
            table(["Indicator", "2025"],
                  [["Energy shifted (GWh)", 214],
                   ["Estimated avoided curtailment of renewable generation (GWh)", 61],
                   ["Estimated avoided CO2 emissions (tonnes)", 24_800],
                   ["Peak capacity provided to grid operators (MW)", 93]]),
            h2("Own operations"),
            table(["Indicator", "2024", "2025"],
                  [["Scope 1 emissions (tonnes CO2e)", 412, 388],
                   ["Scope 2 emissions, market-based (tonnes CO2e)", 690, 341],
                   ["Share of renewable electricity at own sites", "48%", "82%"],
                   ["Production waste recycled", "71%", "78%"]]),
            h2("Method and limitations"),
            para("""
            System effects are estimated from operational telemetry and published grid emission factors. The
            figures are indicative and are not assured by a third party. Voltara does not hold an
            environmental management system certification; the environmental programme is run inside the
            ISO 9001 framework at the München plant.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Code_of_Conduct.md", "Code of conduct", "md", tags=["/hr", "/compliance"],
        body=md(
            h1("Code of Conduct"),
            kv([("Version", "3"), ("Effective", "2025-05-01"), ("Owner", b.who("p_brandt")),
                ("Applies to", "All employees, officers and contractors")]),
            h2("1. Integrity"),
            para("""
            We compete on the quality of our products and our delivery. We do not offer or accept bribes,
            kickbacks or improper payments of any kind, directly or through third parties. Facilitation
            payments are prohibited without exception.
            """),
            h2("2. Gifts and hospitality"),
            para("""
            Gifts and hospitality with a value above 50 EUR must be declared in the gift register. Gifts to
            public officials are not permitted. Hospitality must be proportionate, infrequent and openly
            given.
            """),
            h2("3. Conflicts of interest"),
            para("""
            Any personal interest that could influence a business decision must be disclosed to the line
            manager and recorded by the compliance function. Employees do not participate in decisions where
            they have a conflict.
            """),
            h2("4. Fair competition"),
            para("""
            We do not exchange competitively sensitive information with competitors, and we do not agree on
            prices, markets or customers. Contacts with competitors in industry associations follow the
            association's competition law guidance.
            """),
            h2("5. Export control and sanctions"),
            para("""
            Deliveries are screened against applicable sanctions lists before shipment. Storage systems and
            control software may be subject to export control; procurement and legal must be consulted before
            any delivery outside the European Economic Area.
            """),
            h2("6. Speaking up"),
            para("""
            Concerns can be raised with the line manager, with the HR Director, with the compliance function
            or anonymously through the external reporting channel. Retaliation against a person who reports a
            concern in good faith is a disciplinary offence.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Employee_Handbook_v6_public_extract.md",
        "Employee handbook v6 — public extract", "md", tags=["/hr"],
        body=md(
            h1("Employee Handbook v6 — Public Extract"),
            para("""
            This extract contains the sections of the Employee Handbook that are made available to all staff
            on the public drive. The complete handbook, including sections that reference personnel data
            handling, is held in the HR drive.
            """),
            h2("Working time"),
            para("""
            Standard working time is 40 hours per week in Hungary and Germany and 37 hours per week in
            Denmark. Core hours are 10:00 to 15:00 local time.
            """),
            h2("Remote work"),
            para("""
            The Remote and Hybrid Work Policy requires a minimum of two on-site days per week. Fully remote
            arrangements require written approval from the responsible vice president.
            """),
            h2("Travel"),
            para("""
            Per diem rates are 32 EUR per day in Hungary, 38 EUR per day in Germany and 45 EUR per day in
            Denmark. Flights below four hours are booked in economy class.
            """),
            h2("Performance cycle"),
            para("""
            The annual performance cycle closes on 30 November with a mid-year check-in in June. Salary
            review takes effect on 1 April.
            """),
        )))

    # ------------------------------------------------------------ org chart
    org = d["org_structure"]
    reports = org["reports_to"]
    direct = {}
    for person, boss in reports.items():
        if boss:
            direct.setdefault(boss, []).append(person)
    docs.append(Doc(
        DRIVE, "company/Org_Chart_2026.md", "Organisation chart 2026", "md",
        tags=["/hr", "/search", "/data"],
        body=md(
            h1("Organisation Chart — 2026"),
            kv([("As of", d["meta"]["as_of"]), ("Owner", b.who("p_kovacs")),
                ("Employees", f"{co['employees_fte']} FTE")]),
            h2("Executive Committee"),
            para("The Executive Committee meets every Tuesday at 09:00, in Budapest and online."),
            table(["Name", "Role", "Entity", "Location"],
                  [[b.name(p), b.people[p]["title"],
                    b.entities[b.people[p]["entity"]]["name"] if b.people[p].get("entity") else "",
                    b.people[p]["location"]]
                   for p in org["executive_committee"]]),
            h2("Reporting lines"),
            table(["Name", "Role", "Reports to"],
                  [[b.name(p), b.people[p]["title"],
                    b.name(reports[p]) if reports[p] else "the Board of Directors"]
                   for p in reports]),
            h2("Teams"),
            *[md(h3(f"{b.name(boss)} — {b.people[boss]['title']}"),
                 bullets([f"{b.name(x)} — {b.people[x]['title']} ({b.people[x]['location']})"
                          for x in sorted(members, key=lambda i: b.name(i))]))
              for boss, members in sorted(direct.items(), key=lambda kv2: b.name(kv2[0]))],
            h2("Note"),
            para("""
            This chart shows reporting lines only. It does not contain personal data beyond name, role and
            work location, and it contains no pay information. Individual pay data is held in the payroll
            system and salary bands are held in the HR drive.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/FAQ_for_Customers.md", "Frequently asked questions for customers", "md",
        tags=["/sales", "/search"],
        body=md(
            h1("Frequently Asked Questions — Customers"),
            h2("Delivery and lead time"),
            para("""
            Standard delivery is 34 weeks from order confirmation for a VoltStack 2 block, including factory
            acceptance testing. From the second quarter of 2027, when the second assembly line in München is
            qualified, we expect to shorten this. We do not quote a shorter lead time before the line is
            qualified.
            """),
            h2("Warranty"),
            para("""
            The standard warranty is 24 months from provisional acceptance. It can be extended to 60 months
            against an annual service fee. Battery cells additionally carry a capacity guarantee of 80 percent
            after 6,000 equivalent full cycles under the reference conditions in the technical specification.
            """),
            h2("Grid compliance"),
            para("""
            The GC-3000 controller supports EN 50549-2 and, from firmware version 3.0, IEEE 1547-2018
            category II ride-through and full IEC 61850 edition 2.1. Type approval for version 3.0 is
            scheduled for submission in October 2026; until then version 2.6.4 is the production release.
            """),
            h2("Remote access and data"),
            para("""
            Sites connect to the Voltara Service Portal through a customer-provided VPN. The portal never
            initiates a connection into the site network; every session is established outbound from the site
            gateway, and every remote command is logged with the operator identity.
            """),
            h2("Security and certification"),
            para("""
            Voltara holds ISO/IEC 27001:2022 for its group-wide information security management system and
            ISO 9001:2015 for manufacturing and service at the München plant. An external penetration test is
            commissioned periodically; the most recent report is from October 2025.
            """),
            h2("Payment"),
            para("""
            Standard customer payment terms are 30 days net. Project contracts typically use a milestone plan
            of 30 percent on order, 50 percent on delivery and 20 percent on acceptance.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Careers_Overview.md", "Careers at Voltara", "md", tags=["/hr"],
        body=md(
            h1("Careers at Voltara"),
            para(f"""
            Voltara employs {co['employees_fte']} people across Budapest, München and København, and plans to
            reach {d['hr']['headcount_plan_year_end']} by the end of 2026.
            """),
            h2("How we work"),
            bullets([
                "Hybrid by default: a minimum of two on-site days per week.",
                "English is the corporate language; local languages are used within each site.",
                "Annual salary review with effect from 1 April; performance cycle closes 30 November.",
                "A technical career track is being introduced alongside the management track.",
            ]),
            h2("Where we are hiring"),
            table(["Position", "Location", "Priority"],
                  [["Senior firmware engineer", "Budapest", "high"],
                   ["Power electronics engineer", "München", "high"],
                   ["Field service technician", "København", "high"],
                   ["Test engineer", "Budapest", "medium"],
                   ["Cost controller", "Budapest", "medium"],
                   ["Quality engineer", "München", "medium"],
                   ["Bid manager", "München", "medium"]]),
            h2("Our hiring process"),
            para("""
            Screening call, technical interview, a practical exercise relevant to the role, and a final
            conversation with the hiring manager. The average time from application to offer is 47 days; we
            are working to shorten it.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Supplier_Code_of_Conduct.md", "Supplier code of conduct", "md",
        tags=["/compliance", "/legal"],
        body=md(
            h1("Supplier Code of Conduct"),
            kv([("Version", "2"), ("Effective", "2025-07-01"), ("Owner", b.who("p_varga")),
                ("Applies to", "All suppliers of goods and services to any Voltara entity")]),
            h2("1. Legal compliance"),
            para("""
            Suppliers comply with all applicable laws in the countries where they operate, including labour,
            environmental, competition, sanctions and export control law.
            """),
            h2("2. Labour and human rights"),
            bullets([
                "No forced labour, bonded labour or child labour in any part of the supply chain.",
                "Freedom of association and the right to collective bargaining are respected.",
                "Working hours and wages meet at least the applicable legal minimum.",
                "No discrimination on any protected ground.",
            ]),
            h2("3. Health, safety and environment"),
            para("""
            Suppliers provide a safe working environment, manage chemical and battery hazards appropriately,
            and hold the permits required for their activity. Suppliers of cells and modules must be able to
            demonstrate compliance with IEC 62619 and the transport requirements of UN 38.3.
            """),
            h2("4. Business integrity"),
            para("""
            Suppliers do not offer or accept bribes or improper payments. Gifts and hospitality towards
            Voltara personnel above a value of 50 EUR must be declined; Voltara personnel are required to
            declare them.
            """),
            h2("5. Information security"),
            para("""
            Suppliers with access to Voltara systems or data complete a security assessment before access is
            granted, and again every two years. As of 30 June 2026 three recently onboarded suppliers had not
            yet completed the assessment; this is tracked as a non-conformity in the information security
            management system.
            """),
            h2("6. Audit and consequences"),
            para("""
            Voltara may audit a supplier's production site once per calendar year. Material breach of this
            code that is not remedied within 30 days of written notice is grounds for termination.
            """),
        )))

    docs.append(Doc(
        DRIVE, "company/Whistleblowing_Policy.md", "Whistleblowing policy", "md",
        tags=["/compliance", "/hr"],
        body=md(
            h1("Whistleblowing Policy"),
            kv([("Version", "2"), ("Effective", "2025-05-01"), ("Owner", b.who("p_brandt")),
                ("External channel operator", b.people["p_falk"].get("organisation", ""))]),
            h2("1. What can be reported"),
            para("""
            Any suspected breach of law, of the Code of Conduct or of an internal policy: corruption, fraud,
            competition law breaches, safety violations, environmental harm, data protection breaches,
            discrimination or harassment.
            """),
            h2("2. How to report"),
            numbered([
                "To the line manager, if the concern does not involve them.",
                "To the HR Director or to the Head of Compliance & Quality.",
                "Anonymously through the external channel operated by the company's external counsel.",
            ]),
            h2("3. Handling"),
            para("""
            Receipt is acknowledged within seven days. An initial assessment is completed within 30 days and
            feedback is given to the reporter within three months, unless anonymity makes that impossible.
            Reports and their outcome are retained for five years after closure.
            """),
            h2("4. Protection"),
            para("""
            Retaliation against a person who reports a concern in good faith is a disciplinary offence.
            Protection applies even if the concern later proves unfounded, provided the report was made in
            good faith.
            """),
        )))

    docs.append(Doc(
        DRIVE, "products/Product_Roadmap_2026-2027.md", "Product roadmap 2026-2027", "md",
        tags=["/presentation", "/sales", "/strategy"],
        body=md(
            h1("Product Roadmap 2026–2027"),
            kv([("Owner", b.who("p_lehmann")), ("Last update", "2026-07-15"),
                ("Audience", "Customers and partners — indicative, not contractual")]),
            h2("GC-3000 controller"),
            table(["Release", "Timing", "Content"],
                  [["v2.6.4", "released 2026-04-08", "Current production release; SOC estimator and "
                    "security fixes"],
                   ["v3.0", "type approval submission 2026-10-15, first shipment 2026-12-18",
                    "IEC 61850 server, IEEE 1547-2018 category II, 10 ms control loop, secure boot, "
                    "redundant pair"],
                   ["v3.1", "2027 H1 (indicative)", "Over-the-air upgrade, extended diagnostics"]]),
            h2("VoltStack platform"),
            table(["Item", "Timing", "Content"],
                  [["VoltStack 2", "in serial production", "2.5 MWh per container, 89.4% round-trip "
                    "efficiency, 8,000 cycles"],
                   ["Second assembly line, München", "qualification by 2027-03-31", "+40% module output"],
                   ["Cold-climate variant", "design study through 2027",
                    "Fed by the TUNDRA-STORE research results; no committed launch date"]]),
            h2("Service portal"),
            bullets([
                "Per-department usage reporting — September 2026.",
                "Predictive maintenance indicators from internal resistance trends — in production.",
                "Customer-facing availability reporting against contractual service levels — in production.",
            ]),
            h2("Not on the roadmap"),
            para("""
            Residential and small commercial storage, hydrogen systems and solar inverters are outside the
            product strategy and are not planned.
            """),
        )))

    # -------------------------------------------------------- certificates
    for std, cert_no, issued, valid, scope in [
        ("ISO/IEC 27001:2022", "IS 742 118", "2025-06-12", "2028-06-11",
         "Group-wide information security management system covering the development, manufacture, "
         "delivery and remote service of battery energy storage systems and grid controllers."),
        ("ISO 9001:2015", "QM 118 402", "2025-04-14", "2028-04-13",
         "Manufacturing and service of battery energy storage systems at the München plant."),
    ]:
        slug = std.split(":")[0].replace("/", "").replace(" ", "")
        docs.append(Doc(
            DRIVE, f"certificates/{slug}_Certificate.pdf", f"{std} certificate", "pdf",
            tags=["/compliance"],
            blocks=[
                ("h2", "Certificate of registration"),
                ("", f"Certification body: Certiva Assurance Ltd."),
                ("", f"Certificate number: {cert_no}."),
                ("", f"Standard: {std}."),
                ("", f"Certificate holder: {co['name']}, {co['headquarters']}."),
                ("h2", "Scope of certification"),
                ("", scope),
                ("h2", "Validity"),
                ("", f"Original certification date: {issued}. Valid until: {valid}, subject to the "
                     f"successful completion of annual surveillance audits."),
                ("", "The most recent surveillance audit was completed on 3 June 2026 and certification "
                     "was maintained."),
                ("h2", "Conditions"),
                ("", "This certificate remains the property of the certification body and must be returned "
                     "on request. The scope of certification and the validity of this certificate can be "
                     "verified in the public register of the certification body."),
            ]))

    return docs
