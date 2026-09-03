"""Legal drive: contracts, board minutes, audit and compliance material."""
from __future__ import annotations

from .common import Bible, Doc, bullets, h1, h2, kv, md, numbered, para, table

DRIVE = "drive_legal"


def _contract_blocks(b: Bible, cid: str, preamble: list[tuple[str, str]],
                     extra: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    c = b.contracts[cid]
    blocks: list[tuple[str, str]] = [
        ("h2", "Contract data"),
        ("", f"Counterparty: {c['counterparty']} ({c['counterparty_country']}). "
             f"Type: {c['type']}. Signed: {c['signed']}."),
    ]
    if c.get("effective"):
        blocks.append(("", f"Effective date: {c['effective']}."))
    if c.get("term_months"):
        blocks.append(("", f"Term: {c['term_months']} months."))
    if c.get("value_meur"):
        blocks.append(("", f"Contract value: EUR {c['value_meur']} million."))
    if c.get("annual_fee_eur"):
        blocks.append(("", f"Annual fee: EUR {c['annual_fee_eur']:,}."))
    blocks.append(("", f"Contract owner at Voltara: {b.who(c['owner'])}."))
    blocks += preamble
    if c.get("key_clauses"):
        blocks.append(("h2", "Key clauses"))
        for cl in c["key_clauses"]:
            blocks.append(("h2", f"{cl['ref']} {cl['title']}"))
            blocks.append(("", cl["text"]))
    blocks += extra or []
    return blocks


def build(b: Bible) -> list[Doc]:
    d = b.d
    comp = d["compliance"]
    docs: list[Doc] = []

    # ------------------------------------------------------------- contracts
    docs.append(Doc(
        DRIVE, "contracts/Vestkraft_Supply_Agreement_2025.pdf",
        "Supply and Commissioning Agreement — Vestkraft A/S", "pdf", tags=["/legal"],
        blocks=_contract_blocks(b, "con_vestkraft", [
            ("h2", "Recitals"),
            ("", "Voltara Nordic A/S (the Supplier) and Vestkraft A/S (the Customer) enter into this "
                 "agreement for the supply, installation and commissioning of a 40 MWh battery energy "
                 "storage system at the Customer's site in Esbjerg, Denmark."),
            ("h2", "§ 3 Scope of supply"),
            ("", "The Supplier delivers sixteen VoltStack 2 containerised units, the GC-3000 control system, "
                 "installation, commissioning, site acceptance testing and the documentation package "
                 "described in Annex 2."),
            ("h2", "§ 4 Schedule"),
            ("", "Provisional acceptance shall take place no later than 30 November 2026. Cell delivery "
                 "batch 1 is due by 15 April 2026 and batch 2 by 30 June 2026."),
            ("h2", "§ 5 Price and payment"),
            ("", "The contract price is EUR 12,400,000. Payment terms are 30% on order, 50% on delivery and "
                 "20% on provisional acceptance, net 30 days from invoice date."),
        ], extra=[
            ("h2", "§ 9 Force majeure"),
            ("", "Neither party is liable for failure to perform caused by an event beyond its reasonable "
                 "control. Supplier delay caused by a sub-supplier is expressly excluded from force majeure "
                 "unless the underlying cause would itself qualify."),
            ("h2", "§ 12 Limitation of liability"),
            ("", "The Supplier's aggregate liability under this agreement is limited to the contract price. "
                 "Indirect and consequential losses, including loss of production and loss of profit, are "
                 "excluded. This limitation does not apply to gross negligence or wilful misconduct."),
            ("h2", "§ 13 Insurance"),
            ("", "The Supplier maintains general liability insurance with a minimum cover of EUR 10 million "
                 "per occurrence and erection all-risks insurance covering the works until provisional "
                 "acceptance."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/Nordcell_Framework_Agreement_2024.pdf",
        "Framework Supply Agreement — Nordcell Technologies ApS", "pdf", tags=["/legal"],
        blocks=_contract_blocks(b, "con_nordcell", [
            ("h2", "Recitals"),
            ("", "Voltara Energy Group Zrt. (the Buyer) and Nordcell Technologies ApS (the Supplier) enter "
                 "into this framework agreement for the supply of lithium iron phosphate cells and modules "
                 "for use in the Buyer's storage products."),
            ("h2", "§ 2 Nature of the agreement"),
            ("", "This agreement establishes the terms under which individual call-offs are placed. It does "
                 "not grant exclusivity to either party and does not oblige the Buyer to any minimum volume "
                 "beyond the call-offs actually placed."),
            ("h2", "§ 5 Quality"),
            ("", "Cells are supplied to the specification in Annex 1. The Supplier provides a certificate of "
                 "analysis with each batch and grants the Buyer the right to audit the production site once "
                 "per calendar year."),
        ], extra=[
            ("h2", "§ 9 Warranty"),
            ("", "The Supplier warrants the cells for 24 months from delivery against defects in material "
                 "and workmanship, and warrants a capacity retention of at least 80% after 6,000 equivalent "
                 "full cycles under the reference conditions in Annex 1."),
            ("h2", "§ 14 Term and termination"),
            ("", "The agreement runs for 36 months from 1 January 2025. Either party may terminate for "
                 "material breach not remedied within 30 days of written notice. Termination does not affect "
                 "call-offs already accepted."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/ViVeSec_AI_Box_Agreement_2026.pdf",
        "On-premise AI Platform Agreement — ViVeSec Holdings GmbH", "pdf", tags=["/legal"],
        blocks=_contract_blocks(b, "con_vivesec", [
            ("h2", "Recitals"),
            ("", "ViVeSec Holdings GmbH (the Vendor) supplies an on-premise artificial intelligence "
                 "appliance and the associated software licence to Voltara Energy Group Zrt. (the Customer) "
                 "for use across the Customer's document repositories."),
            ("h2", "§ 2 Deployment"),
            ("", "The appliance is installed in the Customer's data room in Budapest. The Vendor has no "
                 "standing remote access; support sessions are initiated by the Customer and are logged."),
            ("h2", "§ 3 Licence"),
            ("", "The Vendor grants a non-exclusive, non-transferable licence to use the platform software "
                 "on the supplied appliance for the Customer's internal business purposes for the term of "
                 "this agreement."),
        ], extra=[
            ("h2", "§ 6 Support"),
            ("", "The Vendor provides support during business hours with a four-hour response target for "
                 "priority one incidents. Firmware and model updates are supplied on physical media or "
                 "through a Customer-initiated channel."),
            ("h2", "§ 9.2 No training on Customer data"),
            ("", "The Vendor shall not use Customer Data to train, fine-tune or evaluate any model outside "
                 "the Customer's infrastructure. Aggregate telemetry, if enabled by the Customer, is limited "
                 "to counters and contains no document content."),
            ("h2", "§ 11 Data protection"),
            ("", "The parties conclude a data processing agreement in Annex 3. Because all processing takes "
                 "place on Customer-controlled infrastructure, the Vendor acts as a processor only during "
                 "Customer-initiated support sessions."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/Helvetia_Cloud_SLA_2026.pdf",
        "Cloud Infrastructure Service Level Agreement 2026 — Helvetia Cloud AG", "pdf", tags=["/legal"],
        blocks=_contract_blocks(b, "con_helvetia", [
            ("h2", "Recitals"),
            ("", "Helvetia Cloud AG (the Provider) supplies infrastructure services used by Voltara for the "
                 "Voltara Service Portal and for corporate collaboration workloads. Production storage "
                 "system data remains on premise and is not in scope of this agreement."),
            ("h2", "§ 1 Services"),
            ("", "The Provider supplies compute, storage and network services in the Zurich and Frankfurt "
                 "regions, together with a management console and metered usage reporting."),
        ], extra=[
            ("h2", "§ 11 Data protection and cryptographic controls"),
            ("", "All customer data is encrypted at rest with AES-256 keys provisioned per tenant within the "
                 "Provider's hardware security modules. Encryption keys are rotated on a bi-annual cadence; "
                 "the Customer initiates the coordinated rotation window and validates successful re-wrap of "
                 "stored objects."),
            ("", "The Customer may opt into hold-your-own-key mode at any time with thirty days notice to "
                 "the Provider's trust operations team."),
            ("h2", "§ 17.3 Disputes"),
            ("", "Billing disputes must be raised in writing within fifteen business days of invoice "
                 "receipt, failing which the invoice is deemed accepted."),
            ("h2", "§ 19 Term"),
            ("", "The agreement runs for twelve months from 1 January 2026 and renews automatically for "
                 "successive twelve-month periods unless terminated with sixty days notice."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/Danubia_Term_Loan_Agreement_2025.pdf",
        "Term Loan Facility Agreement — Danubia Bank Zrt.", "pdf", tags=["/legal", "/finance"],
        blocks=_contract_blocks(b, "con_danubia", [
            ("h2", "Recitals"),
            ("", "Danubia Bank Zrt. (the Lender) makes available to Voltara Energy Group Zrt. (the Borrower) "
                 "a term loan facility of EUR 6,000,000 for general corporate purposes and for the funding "
                 "of capacity investment."),
            ("h2", "§ 3 Interest"),
            ("", "Interest accrues at three-month EURIBOR plus a margin of 2.85% per annum, payable "
                 "quarterly in arrears. A commitment fee of 0.35% per annum applies to undrawn amounts."),
            ("h2", "§ 5 Repayment"),
            ("", "The facility is repayable in twenty equal quarterly instalments commencing on "
                 "30 June 2025, with a final maturity of 31 March 2030."),
        ], extra=[
            ("h2", "§ 14 Negative pledge"),
            ("", "The Borrower shall not create security over its assets in favour of any third party "
                 "without the prior written consent of the Lender, other than liens arising in the ordinary "
                 "course of business."),
            ("h2", "§ 16 Events of default"),
            ("", "Events of default include non-payment, breach of a financial covenant not remedied within "
                 "20 business days, insolvency and a change of control in which a single new shareholder "
                 "acquires more than 50% of the voting rights."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/Baltic_Grid_NDA_2026.pdf",
        "Mutual Non-Disclosure Agreement — Baltic Grid Partners UAB", "pdf", tags=["/legal"],
        blocks=_contract_blocks(b, "con_baltic_nda", [
            ("h2", "Purpose"),
            ("", "The parties wish to exchange confidential information in connection with the evaluation of "
                 "a potential storage project in Lithuania."),
            ("h2", "§ 1 Confidential information"),
            ("", "Confidential information means any information disclosed by one party to the other in "
                 "connection with the purpose, whether in writing, orally or by inspection, including "
                 "technical specifications, pricing, customer data and business plans."),
            ("h2", "§ 2 Obligations"),
            ("", "Each party shall keep the other party's confidential information secret, use it solely for "
                 "the purpose, and disclose it only to those of its personnel and professional advisers who "
                 "need to know it and who are bound by equivalent obligations."),
            ("h2", "§ 3 Exclusions"),
            ("", "The obligations do not apply to information that is or becomes public without breach, was "
                 "lawfully known before disclosure, is independently developed, or must be disclosed by law "
                 "or by a competent authority."),
            ("h2", "§ 5 Term"),
            ("", "This agreement runs for 36 months from the signature date. The confidentiality obligation "
                 "survives for a further three years after expiry."),
        ])))

    docs.append(Doc(
        DRIVE, "contracts/Regensburg_Liefervertrag_2026.pdf",
        "Liefer- und Montagevertrag — Stadtwerke Regensburg GmbH", "pdf", language="de",
        tags=["/legal", "difficulty:multilingual"],
        blocks=[
            ("h2", "Vertragsdaten"),
            ("", "Vertragspartner: Stadtwerke Regensburg GmbH (DE). Auftragnehmer: Voltara Systems GmbH. "
                 "Unterzeichnet am 22. April 2026. Auftragswert: EUR 3,2 Millionen. "
                 "Vertragsverantwortlich bei Voltara: Gábor Erdélyi, Head of Sales DACH & CEE."),
            ("h2", "§ 1 Vertragsgegenstand"),
            ("", "Gegenstand des Vertrages ist die Lieferung, Montage und Inbetriebnahme eines "
                 "Batteriespeichersystems mit einer Nennkapazität von 8 MWh am Standort Regensburg-Haslbach, "
                 "einschließlich der Netzanschlussdokumentation nach EN 50549-2."),
            ("h2", "§ 4 Termine"),
            ("", "Die Lieferung erfolgt bis zum 30. November 2026. Die vorläufige Abnahme ist für den "
                 "26. Februar 2027 vorgesehen."),
            ("h2", "§ 6 Vergütung und Zahlung"),
            ("", "Die Vergütung beträgt EUR 3.200.000 zuzüglich der gesetzlichen Umsatzsteuer. "
                 "Zahlungsplan: 30% bei Auftragserteilung, 50% bei Lieferung, 20% bei Abnahme. "
                 "Zahlungsziel: 30 Tage netto."),
            ("h2", "§ 9 Vertragsstrafe"),
            ("", "Bei einem vom Auftragnehmer zu vertretenden Verzug beträgt die Vertragsstrafe 0,5% der "
                 "Auftragssumme je angefangener Woche, höchstens jedoch 5% der Auftragssumme."),
            ("h2", "§ 11 Gewährleistung"),
            ("", "Die Gewährleistungsfrist beträgt 24 Monate ab vorläufiger Abnahme. Für die Batteriezellen "
                 "gilt zusätzlich eine Kapazitätsgarantie von 80% nach 6.000 Vollzyklen."),
            ("h2", "§ 15 Anwendbares Recht"),
            ("", "Es gilt deutsches Recht unter Ausschluss des UN-Kaufrechts. Gerichtsstand ist München."),
        ]))

    # ------------------------------------------------------- clause library
    clause_rows = []
    for cid, c in b.contracts.items():
        for cl in c.get("key_clauses", []):
            clause_rows.append([c["counterparty"], cl["ref"], cl["title"], cl["text"]])
    docs.append(Doc(
        DRIVE, "reference/Clause_Library_2026.md", "Clause library 2026", "md", tags=["/legal"],
        body=md(
            h1("Clause Library — 2026"),
            kv([("Owner", b.who("p_sorensen")), ("Maintained by", "Legal operations"),
                ("Last update", "2026-07-06")]),
            para("""
            This library collects the operative commercial clauses from the agreements currently in force. It
            is a working reference only; in case of conflict the executed agreement prevails.
            """),
            h2("Liquidated damages and delay"),
            table(["Agreement", "Clause", "Rate", "Cap"],
                  [["Vestkraft supply agreement", "§ 8.2", "0.5% per commenced week", "10% of contract price"],
                   ["Stadtwerke Regensburg", "§ 9", "0.5% per commenced week", "5% of contract price"],
                   ["Nordcell framework (supplier owes)", "§ 7.1", "2% per week of delay", "8%"]]),
            h2("Termination and notice"),
            table(["Agreement", "Clause", "Notice", "Effect"],
                  [["ViVeSec AI Box", "§ 4.3", "90 days written", "Pro-rated refund applies"],
                   ["Helvetia Cloud SLA", "§ 19", "60 days", "Otherwise auto-renews for 12 months"],
                   ["Nordcell framework", "§ 14", "30 days to remedy breach",
                    "Accepted call-offs unaffected"]]),
            h2("Price adjustment"),
            table(["Agreement", "Clause", "Mechanism"],
                  [["Nordcell framework", "§ 6.4",
                    "Lithium carbonate index, capped at plus or minus 6% per calendar year"],
                   ["ViVeSec AI Box", "§ 4.2", "CPI-linked uplift capped at 4% in year 3"],
                   ["Helvetia Cloud SLA", "§ 17.2", "Net-45 quarterly invoicing on metered usage"]]),
            h2("All catalogued clauses"),
            table(["Counterparty", "Clause", "Title", "Text"], clause_rows),
        )))

    contract_csv = ["contract_id,counterparty,country,type,signed,value_eur,term_months,owner,status"]
    for cid, c in b.contracts.items():
        value = c.get("value_meur")
        value_eur = int(value * 1_000_000) if value else (c.get("annual_fee_eur") or "")
        contract_csv.append(
            f"{cid},{c['counterparty']},{c['counterparty_country']},{c['type']},{c['signed']},"
            f"{value_eur},{c.get('term_months') or ''},{b.name(c['owner'])},in force"
        )
    docs.append(Doc(
        DRIVE, "reference/Contract_Register_2026.csv", "Contract register 2026", "csv",
        tags=["/legal"], body="\n".join(contract_csv)))

    # ---------------------------------------------------------- board minutes
    for meeting in d["board"]["meetings_2026"]:
        if meeting.get("status") == "planned":
            continue
        date = meeting["date"]
        attendees = meeting.get("attendees") or d["board"]["members"]
        docs.append(Doc(
            DRIVE, f"board/Board_Minutes_{date}.md",
            f"Board minutes {date}", "md", tags=["/summary", "/exec"],
            body=md(
                h1(f"Minutes of the Board of Directors — {date}, {meeting['location']}"),
                kv([("Chair", b.name("p_dahl")), ("Secretary", b.name("p_farkas")),
                    ("Present", ", ".join(b.name(x) for x in attendees if x in b.people)),
                    ("Quorum", "present")]),
                h2("1. Opening"),
                para(f"""
                The Chair opened the meeting at 09:30 and confirmed that notice had been given in accordance
                with the articles of association and that a quorum was present. The minutes of the previous
                meeting were approved without amendment.
                """),
                h2("2. Decisions"),
                numbered(meeting["key_decisions"]),
                h2("3. Discussion"),
                para(
                    "The Chief Executive presented the trading position and the delivery status of the "
                    "major projects. The Chief Financial Officer confirmed that the financial covenant "
                    "under the term loan facility was met at the last test date and that the group "
                    "remained in a net cash position. "
                    + ("The Board noted the schedule pressure on Project Helios arising from the cell "
                       "supply delay and asked for a quantified delay damages exposure at the next "
                       "meeting." if date == "2026-05-13" else
                       "The Board noted the audit opinion and thanked the finance team for the "
                       "timely close.")
                ),
                h2("4. Actions"),
                table(["Action", "Owner", "Due"],
                      [["Release the approved capital expenditure and place equipment orders",
                        b.name("p_ruhland"), "2026-07-31"],
                       ["Report the delay damages exposure and recovery plan",
                        b.name("p_novak"), "2026-09-04"],
                       ["Bring the pricing exception back with a margin analysis",
                        b.name("p_lund"), "2026-09-16"]]
                      if date == "2026-05-13" else
                      [["Publish the annual report and file with the registry",
                        b.name("p_sorensen"), "2026-04-30"],
                       ["Communicate the 2026 plan to the management team",
                        b.name("p_barat"), "2026-02-28"]]),
                h2("5. Close"),
                para(f"The Chair closed the meeting at 13:10. The next meeting is scheduled for "
                     f"{d['board']['meetings_2026'][-1]['date']} in "
                     f"{d['board']['meetings_2026'][-1]['location']}."),
            )))

    # ------------------------------------------------------------- ISO audit
    iso = comp["certifications"][0]
    docs.append(Doc(
        DRIVE, "audit/ISO27001_Surveillance_Audit_Report_2026.pdf",
        "ISO/IEC 27001 surveillance audit report 2026", "pdf", tags=["/compliance"],
        blocks=[
            ("h2", "Audit data"),
            ("", f"Standard: {iso['standard']}. Scope: {iso['scope']}. "
                 f"Certificate number: {iso['certificate_no']}. Certification body: {iso['body']}."),
            ("", f"Initial certification: {iso['certified']}. "
                 f"Surveillance audit performed: {iso['last_surveillance_audit']}. "
                 f"Lead auditor: M. Halvorsen. Voltara audit contact: {b.who('p_brandt')}."),
            ("h2", "1. Audit conclusion"),
            ("", "The information security management system continues to conform to the requirements of "
                 "ISO/IEC 27001:2022. Certification is maintained. Two minor non-conformities and three "
                 "opportunities for improvement were raised."),
            ("h2", "2. Non-conformities"),
            *[(kind, text) for nc in iso["findings"] for kind, text in [
                ("h2", f"{nc['id']} — {nc['type']} non-conformity"),
                ("", f"{nc['description']} Clause reference: A.5.18 access rights and A.5.19 supplier "
                     f"relationships. Owner: {b.who(nc['owner'])}. "
                     f"Correction due: {nc['due']}. Status: {nc['status']}."),
            ]],
            ("h2", "3. Opportunities for improvement"),
            ("", "OFI-1 Consider automating the collection of privileged access review evidence rather than "
                 "relying on a quarterly manual export."),
            ("", "OFI-2 The supplier security assessment questionnaire could be tiered by criticality to "
                 "reduce effort on low-risk suppliers."),
            ("", "OFI-3 The incident register would benefit from a field recording the notification "
                 "assessment outcome, not only the rationale text."),
            ("h2", "4. Areas of strength"),
            ("", "The auditor noted the completeness of the risk treatment plan, the quality of the "
                 "incident post-mortems and the disciplined change control in the firmware release process."),
            ("h2", "5. Next audit"),
            ("", "The next surveillance audit is planned for June 2027. Recertification is due in 2028."),
        ]))

    docs.append(Doc(
        DRIVE, "audit/Pentest_Report_2025.pdf",
        "External penetration test report 2025", "pdf", tags=["/compliance"],
        blocks=[
            ("h2", "Engagement data"),
            ("", "Vendor: Sentinel Redteam Oy. Test window: 6 to 17 October 2025. "
                 "Scope: internet-facing services, the Voltara Service Portal, the corporate VPN and the "
                 "GC-3000 web configuration interface. Method: grey box."),
            ("", f"Voltara contact: {b.who('p_toth')}. Report issued: 2025-10-31."),
            ("h2", "1. Summary of findings"),
            ("", "Zero critical, two high, six medium and eleven low findings were identified. No evidence "
                 "of prior compromise was found. All high and medium findings were remediated and retested "
                 "by 30 January 2026."),
            ("h2", "2. High findings"),
            ("", "PT-2025-H1 The corporate VPN allowed legacy authentication without a second factor for a "
                 "subset of service accounts. Remediated by disabling legacy authentication on "
                 "12 November 2025."),
            ("", "PT-2025-H2 The GC-3000 web configuration interface did not rotate the session identifier "
                 "on privilege change, enabling a session fixation attack on the local network. Remediated "
                 "in firmware 2.6.4, released 8 April 2026."),
            ("h2", "3. Medium findings"),
            ("", "Six medium findings covered missing security headers on the service portal, verbose error "
                 "messages, an outdated TLS cipher suite, weak password policy on a legacy administrative "
                 "console, missing rate limiting on the login endpoint and an over-permissive storage "
                 "bucket policy. All were closed by 30 January 2026."),
            ("h2", "4. Retest"),
            ("", "A retest of the high and medium findings was performed on 27 and 28 January 2026. All "
                 "findings were confirmed closed."),
            ("h2", "5. Note on scope"),
            ("", "This report covers the 2025 engagement only. The next external penetration test is "
                 "planned but has not yet been scheduled or performed."),
        ]))

    # --------------------------------------------------------- control matrix
    cm_rows = [["Control", "Annex A reference", "Description", "Implementation", "Owner", "Evidence"]]
    controls = [
        ("A.5.1", "Policies for information security", "implemented", "p_brandt", "ISMS policy set v3"),
        ("A.5.15", "Access control", "implemented", "p_toth", "Access control procedure"),
        ("A.5.18", "Access rights", "implemented with open finding", "p_toth",
         "Quarterly review export — Q1 2026 missing (NC-2026-01)"),
        ("A.5.19", "Information security in supplier relationships", "implemented with open finding",
         "p_varga", "Supplier assessments — 3 outstanding (NC-2026-02)"),
        ("A.5.23", "Cloud services security", "implemented", "p_toth", "Helvetia SLA and DPA"),
        ("A.5.24", "Incident management planning", "implemented", "p_toth", "Incident procedure and log"),
        ("A.6.3", "Awareness and training", "implemented", "p_kovacs", "Training records 2026"),
        ("A.8.2", "Privileged access rights", "implemented", "p_toth", "PAM configuration export"),
        ("A.8.5", "Secure authentication", "implemented", "p_toth", "MFA mandatory since 2026-04-15"),
        ("A.8.7", "Protection against malware", "implemented", "p_toth", "Endpoint policy report"),
        ("A.8.8", "Management of technical vulnerabilities", "implemented", "p_toth",
         "Patch report and 2025 penetration test"),
        ("A.8.16", "Monitoring activities", "implemented", "p_toth", "SIEM use cases"),
        ("A.8.24", "Use of cryptography", "implemented", "p_kiss", "Key management standard"),
        ("A.8.25", "Secure development life cycle", "implemented", "p_kiss", "SDLC standard and ADR log"),
        ("A.8.28", "Secure coding", "implemented", "p_kiss", "Static analysis gate in the build"),
    ]
    for ref, desc, impl, owner, ev in controls:
        cm_rows.append([ref.replace("A.", "CTRL-"), ref, desc, impl, b.name(owner), ev])
    docs.append(Doc(
        DRIVE, "audit/ISMS_Control_Matrix_2026.xlsx", "ISMS control matrix 2026", "xlsx",
        tags=["/compliance", "difficulty:xlsx"],
        sheets={
            "Controls": cm_rows,
            "Summary": [["Field", "Value"],
                        ["Reference", comp["control_matrix_reference"]],
                        ["Annex A controls", 93],
                        ["Implemented", 88],
                        ["Not applicable", 5],
                        ["Open non-conformities", 2],
                        ["Owner", b.name("p_brandt")],
                        ["Last review", "2026-06-10"]],
        }))

    # -------------------------------------------------------------- GDPR
    ropa_rows = [["Activity", "Purpose", "Categories of data", "Legal basis", "Retention",
                  "Recipients", "Owner"]]
    ropa = [
        ("Employee administration", "Employment relationship management",
         "Identity, contact, contract, pay, absence", "Contract and legal obligation",
         "Per retention schedule", "Payroll provider", "p_kovacs"),
        # NB: never write the bare word "None" into a spreadsheet cell — the
        # extractors used for indexing parse it as a missing value and render
        # it as "NaN", which then leaks into generated answers.
        ("Recruitment", "Selection of candidates", "Identity, contact, CV, interview notes",
         "Legitimate interest and consent", "6 months after decision",
         "no external recipient", "p_kovacs"),
        ("Customer relationship management", "Sales and account management",
         "Business contact details", "Legitimate interest", "3 years after last contact",
         "no external recipient", "p_lund"),
        ("Service portal accounts", "Remote monitoring and service delivery",
         "Business contact details, access logs", "Contract", "5 years",
         "no external recipient", "p_nygaard"),
        ("Supplier management", "Procurement and supplier assessment",
         "Business contact details", "Contract", "10 years after expiry",
         "no external recipient", "p_varga"),
        ("Access logging", "Information security",
         "User identifier, timestamp, resource", "Legal obligation and legitimate interest",
         "12 months", "no external recipient", "p_toth"),
        ("Whistleblowing channel", "Handling of reported concerns",
         "Identity where disclosed, report content", "Legal obligation",
         "5 years after closure", "External counsel", "p_brandt"),
        ("AI Box document index", "Internal knowledge retrieval",
         "Personal data contained in indexed documents", "Legitimate interest",
         "Mirrors the source document", "no external recipient", "p_toth"),
    ]
    for row in ropa:
        ropa_rows.append(list(row[:-1]) + [b.name(row[-1])])
    docs.append(Doc(
        DRIVE, "gdpr/GDPR_ROPA_2026.xlsx", "GDPR record of processing activities 2026", "xlsx",
        tags=["/compliance", "difficulty:xlsx"],
        sheets={
            "ROPA": ropa_rows,
            "Governance": [["Field", "Value"],
                           ["Data protection officer", b.name("p_falk")],
                           ["DPO organisation", b.people["p_falk"].get("organisation", "")],
                           ["DPO appointed", comp["gdpr"]["dpo_appointed"]],
                           ["Lead supervisory authority", comp["gdpr"]["lead_authority"]],
                           ["Record last updated", comp["gdpr"]["ropa_last_updated"]],
                           ["Data protection impact assessments completed",
                            comp["gdpr"]["dpias_completed"]],
                           ["Subject requests H1 2026", comp["gdpr"]["subject_requests_h1_2026"]],
                           ["Open subject requests", comp["gdpr"]["open_subject_requests"]]],
        }))

    docs.append(Doc(
        DRIVE, "compliance/NIS2_Gap_Assessment_2026.md", "NIS2 gap assessment 2026", "md",
        tags=["/compliance"],
        body=md(
            h1("NIS2 Gap Assessment — 2026"),
            kv([("Status", comp["nis2"]["status"]), ("Programme owner", b.who("p_brandt")),
                ("Target completion", comp["nis2"]["target_completion"]),
                ("Gaps identified", comp["nis2"]["gaps_identified"]),
                ("Gaps closed", comp["nis2"]["gaps_closed"])]),
            h2("Scope"),
            para("""
            Voltara is in scope of the NIS2 directive as an important entity in the energy sector through its
            role in the operation and remote monitoring of grid-connected storage assets. The assessment
            covered governance, risk management, incident handling, business continuity, supply chain
            security and the security of network and information systems.
            """),
            h2("Open gaps"),
            table(["Gap", "Area", "Owner", "Due"],
                  [["G-03", "Formal management approval and training on cyber risk for the Board",
                    b.name("p_brandt"), "2026-09-30"],
                   ["G-05", "Incident notification procedure aligned to the 24-hour early warning",
                    b.name("p_toth"), "2026-09-30"],
                   ["G-07", "Supply chain security requirements in standard purchase terms",
                    b.name("p_varga"), "2026-10-31"],
                   ["G-09", "Business continuity plan test for the service portal",
                    b.name("p_toth"), "2026-11-30"],
                   ["G-12", "Vulnerability disclosure policy published externally",
                    b.name("p_toth"), "2026-12-15"]]),
            h2("Closed gaps"),
            para("""
            Nine of the fourteen identified gaps were closed in the first half of 2026, including mandatory
            multi-factor authentication, the asset inventory refresh, the risk treatment plan update, the
            encryption standard and the logging retention policy.
            """),
        )))

    docs.append(Doc(
        DRIVE, "compliance/Incident_Register_2026.md", "Incident register 2026 (legal view)", "md",
        tags=["/compliance", "/legal"],
        body=md(
            h1("Incident Register 2026 — Notification Assessment"),
            kv([("Owner", b.who("p_brandt")), ("Data protection officer", b.name("p_falk")),
                ("Classification", "Confidential")]),
            h2("Register"),
            table(["ID", "Date", "Category", "Personal data involved", "Notification assessment",
                   "Closed"],
                  [[i["id"], i["date"], i["category"],
                    "possible" if i["id"] == "INC-2026-014" else "no",
                    "not notifiable" if i["id"] == "INC-2026-014" else "not applicable",
                    i.get("closed", "open")]
                   for i in comp["incidents"]]),
            h2("INC-2026-014 assessment"),
            para("""
            A sales mailbox was accessed by an unauthorised party for approximately four hours following a
            phishing campaign. The mailbox contained business contact details of customer employees. The risk
            assessment concluded that a high risk to the rights and freedoms of natural persons was not
            likely, because the exposure window was short, no bulk export was observed in the audit log and
            the data categories were limited to business contact details. Notification to the supervisory
            authority was therefore not made. The assessment was documented in the internal register in
            accordance with Article 33(5) of the GDPR and reviewed by the data protection officer.
            """),
            h2("Corrective actions"),
            bullets([
                "Multi-factor authentication made mandatory for all users by 15 April 2026.",
                "Phishing simulation and training repeated across all three entities in May 2026.",
                "Conditional access policy tightened for legacy authentication protocols.",
            ]),
        )))

    docs.append(Doc(
        DRIVE, "claims/Nordcell_Delay_Claim_2026-07-30.md",
        "Delay claim letter — Nordcell Technologies ApS", "md", tags=["/legal"],
        body=md(
            h1("Notice of Claim — Delayed Delivery under the Framework Supply Agreement"),
            kv([("From", "Voltara Energy Group Zrt."), ("To", "Nordcell Technologies ApS"),
                ("Date", "2026-07-30"), ("Reference", "CLM-2026-002"),
                ("Prepared by", b.who("p_varga")), ("Approved by", b.who("p_sorensen"))]),
            h2("1. Background"),
            para("""
            Under the framework supply agreement dated 5 November 2024, the committed lead time for accepted
            call-offs is sixteen weeks (clause 7.1). Call-off NC-2026-0042 for the second cell batch of
            Project Helios was accepted on 12 February 2026 with a contractual delivery date of 30 June 2026.
            The supplier has confirmed a revised delivery date of 21 August 2026.
            """),
            h2("2. Claim"),
            para("""
            Clause 7.1 entitles the buyer to a credit of 2% of the value of the delayed call-off per week of
            delay, capped at 8%. On the confirmed revised date the delay is seven weeks and eight days, which
            exceeds the cap. The buyer therefore claims the maximum credit of 8% of the call-off value, being
            EUR 370,400.
            """),
            h2("3. Consequential position"),
            para("""
            The buyer reserves all rights in respect of the downstream consequences of the delay, including
            the delay damages of 0.5% per commenced week that accrue under clause 8.2 of the Vestkraft supply
            agreement, currently modelled at EUR 0.50 million against a cap of EUR 1.24 million.
            """),
            h2("4. Requested response"),
            para("""
            The buyer requests written confirmation of the credit and a firm recovery plan by
            29 August 2026.
            """),
        )))

    # ------------------------------------------- scanned PDF (no text layer)
    docs.append(Doc(
        DRIVE, "contracts/Vestkraft_PAC_Protocol_signed.pdf",
        "Vestkraft provisional acceptance protocol (signed scan)", "pdf_scanned",
        tags=["/legal", "difficulty:scanned"], scanned_pages=3))

    # ----------------------------------------------------------------- AGM
    agm = d["annual_general_meeting"]
    docs.append(Doc(
        DRIVE, f"board/AGM_Minutes_{agm['date']}.md",
        f"Annual general meeting minutes {agm['date']}", "md", tags=["/summary", "/exec"],
        body=md(
            h1(f"Minutes of the Annual General Meeting — {agm['date']}, {agm['location']}"),
            kv([("Chair", b.name(agm["chair"])), ("Secretary", b.name("p_farkas")),
                ("Share capital represented", f"{agm['quorum_pct']}%"),
                ("Quorum", "established")]),
            h2("1. Constitution"),
            para(f"""
            The Chair opened the meeting and confirmed that it had been convened in accordance with the
            articles of association and that {agm['quorum_pct']} percent of the share capital was represented,
            so the meeting was quorate.
            """),
            h2("2. Resolutions"),
            numbered(agm["resolutions"]),
            h2("3. Discussion"),
            para("""
            A shareholder asked about the absence of a dividend. The Chief Financial Officer explained that
            retained earnings are allocated to the capacity expansion programme and that no dividend has been
            paid since the company was founded. A second question concerned the schedule of the Esbjerg
            project; the Chief Executive confirmed that the contractual acceptance date is 30 November 2026
            and that the internal forecast had moved, with the commercial consequences quantified for the
            Board.
            """),
            h2("4. Close"),
            para("""
            The Chair closed the meeting. The next annual general meeting is expected in April 2027.
            """),
        )))

    # ---------------------------------------------------------- authority
    am = d["authority_matrix"]
    auth_rows = []
    for r in am["rules"]:
        limit = ("up to EUR %s" % format(r["limit"], ",d")) if r.get("limit") else (
            "up to %s%%" % r["limit_pct"] if r.get("limit_pct") else "no limit")
        auth_rows.append([r["scope"], limit, r["approver"], r.get("note", "")])
    docs.append(Doc(
        DRIVE, "reference/Authority_Matrix_2026.md", "Authority matrix 2026", "md",
        tags=["/legal", "/compliance", "/finance"],
        body=md(
            h1("Authority Matrix — 2026"),
            kv([("Owner", b.who("p_sorensen")), ("Approved", "Executive Committee, 2026-01-20"),
                ("Currency", am["currency"])]),
            para("""
            This matrix sets who may commit the company and up to what value. It applies to all three legal
            entities. Approval must be documented before the commitment is made; retrospective approval is
            not valid.
            """),
            table(["Scope", "Limit", "Approver", "Note"], auth_rows),
            h2("Segregation of duties"),
            bullets([
                "The person who raises a purchase order may not approve it.",
                "The person who approves an invoice may not release the payment.",
                "Master data changes in the supplier ledger require a second pair of eyes.",
            ]),
            h2("Known control weakness"),
            para("""
            The internal controls review of the procure-to-pay cycle performed on 20 May 2026 found that
            6 percent of purchase orders above EUR 25,000 lacked the required dual approval. Remediation is
            owned by the Head of Finance and is being addressed as part of the ERP migration.
            """),
        )))

    # ------------------------------------------- litigation (grounded negative)
    docs.append(Doc(
        DRIVE, "compliance/Litigation_and_Claims_Register.md",
        "Litigation and claims register", "md", tags=["/legal", "/compliance"],
        body=md(
            h1("Litigation and Claims Register"),
            kv([("As of", b.as_of), ("Owner", b.who("p_sorensen")),
                ("Reviewed with", "external counsel, quarterly")]),
            h2("Litigation"),
            para("""
            Voltara Energy Group and its subsidiaries are NOT party to any pending or threatened litigation,
            arbitration or regulatory proceeding. No provision for litigation is recognised in the accounts
            and no contingent liability for litigation is disclosed.
            """),
            h2("Contractual claims"),
            table(["Reference", "Counterparty", "Direction", "Amount", "Status"],
                  [["CLM-2026-002", "Nordcell Technologies ApS", "claim by Voltara", "EUR 370,400",
                    "served 2026-07-30, response requested by 2026-08-29"],
                   ["(potential)", "Vestkraft A/S", "exposure of Voltara",
                    "up to EUR 1,240,000 (cap)", "delay damages under clause 8.2; "
                    "EUR 0.50 million modelled on the current forecast"]]),
            h2("Warranty claims"),
            para("""
            Warranty work in the first half of 2026 was handled within the normal service process; no claim
            was escalated to a legal dispute. The warranty provision at 30 June 2026 was EUR 0.42 million on
            the Esbjerg project and EUR 0.31 million across the remaining installed base.
            """),
            h2("Insurance notifications"),
            para("""
            No claim was notified under any insurance policy in the first half of 2026.
            """),
        )))

    docs.append(Doc(
        DRIVE, "compliance/Export_Control_Screening_Procedure.md",
        "Export control and sanctions screening procedure", "md", tags=["/compliance", "/legal"],
        body=md(
            h1("Export Control and Sanctions Screening Procedure"),
            kv([("Document", "PROC-EXP-02"), ("Effective", "2025-11-01"),
                ("Owner", b.who("p_brandt")), ("Operational owner", b.who("p_varga"))]),
            h2("1. Scope"),
            para("""
            Applies to every delivery of hardware, software or technical documentation from any Voltara
            entity, and to the granting of remote access to a customer or partner outside the European
            Economic Area.
            """),
            h2("2. Screening steps"),
            numbered([
                "Screen the counterparty and its ultimate beneficial owner against the applicable sanctions "
                "lists at order entry, and again before shipment if more than 90 days have passed.",
                "Classify the goods: storage systems and control software may be subject to dual-use "
                "control depending on the configuration.",
                "Check the end use and the end user; record a written end-use statement where required.",
                "Obtain a licence before shipment where one is required, and file the reference in the "
                "contract record.",
            ]),
            h2("3. Red flags"),
            bullets([
                "The customer is reluctant to state the end use or the site address.",
                "The delivery address is a freight forwarder in a third country.",
                "The order does not fit the customer's stated business.",
                "Payment is offered from an unrelated third party or jurisdiction.",
            ]),
            h2("4. Current position"),
            para("""
            All deliveries in the first half of 2026 were within the European Economic Area and Switzerland.
            No licence was required and no shipment was blocked or delayed by screening.
            """),
        )))

    # ------------------------------------------------- audit evidence (NC-1/2)
    access_rows = [["Review ID", "System", "Reviewer", "Accounts reviewed", "Changes made",
                    "Completed", "Evidence"]]
    for rid, system, reviewer, n, changed, done in [
        ("AR-2026-Q2-01", "Identity provider (privileged roles)", "p_toth", 14, 2, "2026-07-08"),
        ("AR-2026-Q2-02", "Northbridge ERP", "p_novak", 41, 3, "2026-07-10"),
        ("AR-2026-Q2-03", "Voltara Service Portal", "p_nygaard", 27, 1, "2026-07-09"),
        ("AR-2026-Q2-04", "File server drives", "p_toth", 214, 6, "2026-07-11"),
        ("AR-2026-Q2-05", "AI Box platform", "p_toth", 38, 0, "2026-07-11"),
        ("AR-2026-Q2-06", "Build and source control", "p_kiss", 33, 2, "2026-07-07"),
    ]:
        access_rows.append([rid, system, b.name(reviewer), n, changed, done, "signed export archived"])
    docs.append(Doc(
        DRIVE, "audit/Access_Review_Q2_2026.xlsx", "Privileged access review Q2 2026", "xlsx",
        tags=["/compliance", "difficulty:xlsx"],
        sheets={
            "Q2 2026 reviews": access_rows,
            "Context": [["Field", "Value"],
                        ["Control", "A.5.18 access rights"],
                        ["Cadence", "quarterly"],
                        ["Related finding", "NC-2026-01 (Q1 2026 evidence missing)"],
                        ["Finding owner", b.name("p_toth")],
                        ["Correction due", "2026-08-31"],
                        ["Q1 2026 status", "evidence could not be produced; the review was performed "
                                           "but the export was not archived"],
                        ["Q2 2026 status", "completed and archived"],
                        ["Preventive action", "automated quarterly export from the identity provider, "
                                              "planned for Q4 2026"]],
        }))

    supplier_rows = [["Supplier", "Country", "Criticality", "Data or system access",
                      "Assessment status", "Completed", "Owner"]]
    for name, country, crit, access, status, done in [
        ("Nordcell Technologies ApS", "DK", "high", "none", "complete", "2025-03-12"),
        ("Helvetia Cloud AG", "CH", "high", "hosting of the service portal", "complete", "2025-11-20"),
        ("ViVeSec Holdings GmbH", "DE", "high", "on-premise appliance, support sessions",
         "complete", "2026-01-15"),
        ("Northbridge ERP", "SE", "high", "finance data", "complete", "2025-12-04"),
        ("Sentinel Redteam Oy", "FI", "medium", "test scope only", "complete", "2025-09-18"),
        ("LM Cells ApS", "DK", "medium", "none", "OUTSTANDING", "-"),
        ("Kestrel Risk Partners", "UK", "low", "claims data", "OUTSTANDING", "-"),
        ("Alpine Logistics AG", "AT", "low", "shipment data", "OUTSTANDING", "-"),
    ]:
        supplier_rows.append([name, country, crit, access, status, done, b.name("p_varga")])
    docs.append(Doc(
        DRIVE, "audit/Supplier_Security_Assessment_Status.xlsx",
        "Supplier security assessment status", "xlsx",
        tags=["/compliance", "difficulty:xlsx"],
        sheets={
            "Suppliers": supplier_rows,
            "Context": [["Field", "Value"],
                        ["Control", "A.5.19 information security in supplier relationships"],
                        ["Related finding", "NC-2026-02 (three new suppliers not assessed)"],
                        ["Finding owner", b.name("p_varga")],
                        ["Correction due", "2026-08-31"],
                        ["Outstanding count", 3],
                        ["Planned improvement", "tier the questionnaire by criticality "
                                                "(audit opportunity OFI-2)"]],
        }))

    docs.append(Doc(
        DRIVE, "contracts/Data_Processing_Agreement_ViVeSec.pdf",
        "Data processing agreement — ViVeSec Holdings GmbH", "pdf", tags=["/legal", "/compliance"],
        blocks=[
            ("h2", "Agreement data"),
            ("", "Annex 3 to the On-premise AI Platform Agreement dated 27 January 2026. "
                 "Controller: Voltara Energy Group Zrt. Processor: ViVeSec Holdings GmbH. "
                 f"Voltara contact: {b.who('p_toth')}. Data protection officer: {b.name('p_falk')}."),
            ("h2", "1. Subject matter and duration"),
            ("", "The processor processes personal data on behalf of the controller only to the extent "
                 "necessary to provide support for the on-premise appliance. The agreement runs for the "
                 "term of the main agreement."),
            ("h2", "2. Nature and purpose"),
            ("", "Processing takes place exclusively on controller-controlled infrastructure. The processor "
                 "has no standing remote access; a support session is initiated by the controller, is "
                 "time-limited and is logged. Outside such a session the processor processes no personal "
                 "data of the controller."),
            ("h2", "3. Categories of data and data subjects"),
            ("", "Any personal data contained in the documents indexed by the platform, and the identifiers "
                 "of the controller's users. Data subjects are the controller's employees and its business "
                 "contacts."),
            ("h2", "4. Instructions"),
            ("", "The processor processes personal data only on documented instructions from the controller. "
                 "If the processor believes an instruction infringes data protection law it informs the "
                 "controller without delay."),
            ("h2", "5. No training on controller data"),
            ("", "The processor shall not use controller data to train, fine-tune or evaluate any model "
                 "outside the controller's infrastructure. Telemetry, if enabled by the controller, is "
                 "limited to counters and contains no document content."),
            ("h2", "6. Sub-processors"),
            ("", "The processor engages no sub-processor for this service. Any future sub-processor requires "
                 "the controller's prior written authorisation with 30 days notice and a right to object."),
            ("h2", "7. Security measures"),
            ("", "Appliance disk encryption, signed firmware, role-based access, session logging and "
                 "physical security of the controller's data room. The controller is responsible for the "
                 "physical and network security of the installation site."),
            ("h2", "8. Assistance and breach notification"),
            ("", "The processor assists the controller with data subject requests and with breach handling, "
                 "and notifies the controller without undue delay and at the latest within 24 hours of "
                 "becoming aware of a personal data breach affecting controller data."),
            ("h2", "9. Deletion and audit"),
            ("", "On termination the processor returns or deletes any controller data in its possession and "
                 "certifies the deletion. The controller may audit compliance once per calendar year with "
                 "30 days notice."),
        ]))

    ins = d["insurance"]
    docs.append(Doc(
        DRIVE, "insurance/Insurance_Programme_2026.md", "Insurance programme 2026", "md",
        tags=["/legal", "/finance", "/compliance"],
        body=md(
            h1("Insurance Programme — 2026"),
            kv([("Broker", ins["broker"]), ("Policy year", ins["policy_year"]),
                ("Owner", b.who("p_sorensen"))]),
            h2("Policies in force"),
            table(["Cover", "Insurer", "Limit (EUR m)", "Deductible (EUR)", "Note"],
                  [[p["type"], p["insurer"], p["limit_meur"],
                    format(p["deductible_eur"], ",d") if p.get("deductible_eur") else "-",
                    p.get("note", "")]
                   for p in ins["policies"]]),
            h2("Contractual requirements"),
            para("""
            The Vestkraft supply agreement requires general liability cover of at least EUR 10 million per
            occurrence and erection all-risks cover for the works until provisional acceptance. Both are in
            place. The erection all-risks policy expires with provisional acceptance; because the forecast
            acceptance date has moved to January 2027, an extension has been requested from the broker.
            """),
            h2("Claims"),
            para("""
            No claim was notified under any policy in the first half of 2026.
            """),
            h2("Cyber policy condition"),
            para("""
            The cyber policy is conditional on multi-factor authentication being enforced for all users. This
            condition has been satisfied since 15 April 2026; before that date the condition was not met.
            """),
        )))

    docs.append(Doc(
        DRIVE, "contracts/Vestkraft_Change_Request_CR-HEL-07.pdf",
        "Vestkraft change request CR-HEL-07 — auxiliary transformer rating", "pdf",
        tags=["/legal", "/tracking"],
        blocks=[
            ("h2", "Change request data"),
            ("", "Reference: CR-HEL-07. Contract: Supply and Commissioning Agreement dated "
                 "18 September 2025. Customer: Vestkraft A/S. Project: Helios, Esbjerg."),
            ("", "Raised by the customer on 28 April 2026. Priced on 6 May 2026. Approved by the sponsor "
                 f"{b.who('p_holm')} on 14 May 2026 at the steering committee."),
            ("h2", "1. Requested change"),
            ("", "Increase the auxiliary transformer rating from 400 kVA to 630 kVA to accommodate the "
                 "customer's future site expansion, and adapt the low-voltage distribution accordingly."),
            ("h2", "2. Commercial effect"),
            ("", "Additional price: EUR 46,800, invoiced with the delivery milestone. The change is within "
                 "the sponsor's delegated authority and did not require Board approval."),
            ("h2", "3. Schedule effect"),
            ("", "No effect on the contractual provisional acceptance date. The transformer is a long-lead "
                 "item with a 12-week delivery, ordered on 18 May 2026, which is inside the float of the "
                 "site works."),
            ("h2", "4. Technical effect"),
            ("", "The protection settings and the grid-code compliance documentation must be reissued for "
                 "the new rating. The site acceptance test procedure SAT-ESB-01 is unaffected."),
            ("h2", "5. Agreement"),
            ("", "This change request forms an amendment to the agreement. All other terms, including the "
                 "delay damages under clause 8.2 and the warranty under clause 11.1, remain unchanged."),
        ]))

    return docs
