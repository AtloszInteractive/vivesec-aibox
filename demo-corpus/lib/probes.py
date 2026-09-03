"""Probe questions for score-threshold calibration on the demo corpus.

Two sets:
  * in-corpus  — every question is answerable from exactly one generated
    document; `expect_corpus` is the drive the answer must come from.
  * out-of-corpus — taken verbatim from the bible's `not_in_corpus` block, so
    the "what the corpus does not contain" list stays the single source of
    truth for the negative cases.

Used by score_probe.py to measure the top-1 score distribution and pick
RAG_MIN_SCORE for this corpus (the value calibrated on govdocs does not
transfer — see the repo notes).
"""
from __future__ import annotations

from .common import Bible

IN_CORPUS: list[tuple[str, str, list[str]]] = [
    # (question, drive, every document that legitimately answers it)
    ("What was the revenue in Q2 2026?", "finance", ["Q2_2026_Management_Accounts.md"]),
    ("What was the gross margin in Q2 2026 and why did it fall?", "finance",
     ["Q2_2026_Management_Accounts.md"]),
    ("How much was EBITDA in the first half of 2026?", "finance",
     ["Q2_2026_Management_Accounts.md", "KPI_Dashboard_2026-H1.md"]),
    ("What is the order backlog?", "finance",
     ["Q2_2026_Management_Accounts.md", "KPI_Dashboard_2026-H1.md"]),
    ("Are we compliant with the bank covenant?", "finance",
     ["Danubia_Covenant_Report_2026-06.md"]),
    ("What is the net debt to EBITDA ratio?", "finance", ["Danubia_Covenant_Report_2026-06.md"]),
    ("What is the R&D budget for 2026?", "finance", ["Budget_2026_by_function.xlsx"]),
    ("What is the revenue forecast for the second half of 2026?", "finance",
     ["H2_2026_Forecast.md", "Q2_2026_Management_Accounts.md"]),
    ("How large is the weighted sales pipeline?", "finance",
     ["Pipeline_Review_2026-06.xlsx", "KPI_Dashboard_2026-H1.md"]),
    ("Why did we lose deals in the first half of 2026?", "finance",
     ["Win_Loss_Review_H1_2026.md"]),
    ("What discount is Nordkraft Energi asking for?", "finance", ["Account_Plan_Nordkraft.md"]),
    ("What was the recommendation on the ERP go-live?", "finance",
     ["Decision_Memo_ERP_Golive_Options.md"]),
    ("How much was approved for the second assembly line?", "finance",
     ["Capex_Approval_Orion.md", "Orion_Milestone_Tracker.xlsx"]),

    ("What are the delay damages under the Vestkraft agreement?", "legal",
     ["Vestkraft_Supply_Agreement_2025.pdf", "Clause_Library_2026.md",
      "Nordcell_Delay_Claim_2026-07-30.md"]),
    ("How much is the annual fee for the AI platform?", "legal",
     ["ViVeSec_AI_Box_Agreement_2026.pdf", "Clause_Library_2026.md",
      "Contract_Register_2026.csv"]),
    ("What notice period applies to terminate the AI platform agreement?", "legal",
     ["ViVeSec_AI_Box_Agreement_2026.pdf", "Clause_Library_2026.md"]),
    ("How is the cell price indexed in the supplier framework agreement?", "legal",
     ["Nordcell_Framework_Agreement_2024.pdf", "Clause_Library_2026.md"]),
    ("What uptime does the cloud provider guarantee?", "legal",
     ["Helvetia_Cloud_SLA_2026.pdf", "Clause_Library_2026.md"]),
    ("What did the board decide on 13 May 2026?", "legal", ["Board_Minutes_2026-05-13.md"]),
    ("What non-conformities were raised in the ISO 27001 surveillance audit?", "legal",
     ["ISO27001_Surveillance_Audit_Report_2026.pdf", "ISMS_Control_Matrix_2026.xlsx"]),
    ("Was the March 2026 security incident notified to the authority?", "legal",
     ["Incident_Register_2026.md"]),
    ("What gaps are still open in the NIS2 programme?", "legal",
     ["NIS2_Gap_Assessment_2026.md"]),
    ("How much do we claim from the cell supplier for the delay?", "legal",
     ["Nordcell_Delay_Claim_2026-07-30.md"]),

    ("Why is Project Helios delayed?", "engineering",
     ["Helios_Steering_Minutes_2026-07-09.md", "Helios_Steering_Minutes_2026-06-11.md",
      "Helios_Risk_Register.xlsx", "Decision_Memo_Alternative_Cell_Supplier.md"]),
    ("When is provisional acceptance now forecast for the Esbjerg project?", "engineering",
     ["Helios_Project_Plan_final.docx", "Helios_Steering_Minutes_2026-06-11.md",
      "Helios_Risk_Register.xlsx"]),
    ("What is the biggest open risk on Project Helios?", "engineering",
     ["Helios_Risk_Register.xlsx", "Helios_Steering_Minutes_2026-07-09.md",
      "Weekly_Status_2026-W25.md", "Weekly_Status_2026-W26.md"]),
    ("What is new in firmware version 3.0?", "engineering",
     ["GC3000_Firmware_v3_Release_Notes.md"]),
    ("Why was the Modbus RTU gateway dropped?", "engineering",
     ["Meridian_ADR_012_Timing_Model.md", "GC3000_Interface_Specification.md"]),
    ("What is the round-trip efficiency of VoltStack 2?", "engineering",
     ["VoltStack2_Technical_Specification.md"]),
    ("What are the acceptance criteria for the site acceptance test?", "engineering",
     ["Site_Acceptance_Test_Procedure.md"]),
    ("What is the fleet availability of the installed base?", "engineering",
     ["Field_Service_Report_2026-06.md"]),
    ("Which alternative cell supplier is being qualified?", "engineering",
     ["Decision_Memo_Alternative_Cell_Supplier.md", "Helios_Steering_Minutes_2026-07-09.md",
      "Helios_Risk_Register.xlsx"]),
    ("How many documents are indexed on the AI platform?", "engineering",
     ["AI_Box_Platform_Status_2026-07.md"]),
    ("What is the retention period for the finance drive?", "engineering",
     ["Data_Inventory_2026.md"]),
    ("What is the status of the ERP migration tasks?", "engineering",
     ["Anchor_Task_List_2026-07.md", "Weekly_Status_2026-W21.md"]),

    ("What is the per diem rate in Denmark?", "hr",
     ["Travel_and_Expense_Policy_v3_EN.md", "Rejsepolitik_v3_DA.pdf",
      "Reisekostenrichtlinie_v3_DE.pdf", "Utazasi_szabalyzat_v3_HU.pdf",
      "Employee_Handbook_v6.docx"]),
    ("Mennyi a napidíj Magyarországon?", "hr",
     ["Utazasi_szabalyzat_v3_HU.pdf", "Travel_and_Expense_Policy_v3_EN.md",
      "Reisekostenrichtlinie_v3_DE.pdf", "Rejsepolitik_v3_DA.pdf"]),
    ("Wie hoch ist die Tagespauschale in Deutschland?", "hr",
     ["Reisekostenrichtlinie_v3_DE.pdf", "Travel_and_Expense_Policy_v3_EN.md",
      "Utazasi_szabalyzat_v3_HU.pdf", "Rejsepolitik_v3_DA.pdf"]),
    ("Hvor meget er dagpengesatsen i Danmark?", "hr",
     ["Rejsepolitik_v3_DA.pdf", "Travel_and_Expense_Policy_v3_EN.md",
      "Reisekostenrichtlinie_v3_DE.pdf", "Utazasi_szabalyzat_v3_HU.pdf"]),
    ("How many days per week must employees be on site?", "hr",
     ["Remote_Work_Policy_v2.md", "Employee_Handbook_v6.docx"]),
    ("What is the salary band for a senior engineer in Germany?", "hr",
     ["Salary_Bands_2026.xlsx"]),
    ("What was the voluntary attrition in the first half of 2026?", "hr",
     ["Attrition_Report_H1_2026.md", "Headcount_Plan_2026.xlsx"]),
    ("When does the annual performance cycle close?", "hr",
     ["Performance_Management_Guideline_v2.md", "Employee_Handbook_v6.docx"]),
    ("How many open positions are there?", "hr",
     ["Headcount_Plan_2026.xlsx", "Recruitment_Status_2026-07.md"]),

    ("What does Voltara do?", "public",
     ["Company_Overview_2026.md", "Voltara_Strategy_2026-2028.md",
      "Voltara_Service_Portal_Overview.md", "VoltStack2_Product_Overview.md"]),
    ("How many employees does the company have?", "public", ["Company_Overview_2026.md"]),
    ("What is the revenue ambition for 2028?", "public", ["Voltara_Strategy_2026-2028.md"]),
    ("Which grant programme is TUNDRA-STORE funded from?", "public",
     ["TUNDRA-STORE_Grant_Agreement_Summary.md", "Grant_Pipeline_2026-2027.md",
      "Press_Release_2026-01-14.md"]),
    ("How much CO2 was avoided in 2025?", "public", ["Impact_Report_2025.md"]),
    ("What certifications does the company hold?", "public",
     ["Company_Overview_2026.md", "ISOIEC27001_Certificate.pdf", "ISO9001_Certificate.pdf"]),
    ("What is the cycle life of VoltStack 2?", "public",
     ["VoltStack2_Product_Overview.md"]),
    ("What are the rules on gifts?", "public",
     ["Code_of_Conduct.md", "Employee_Handbook_v6_public_extract.md",
      "Supplier_Code_of_Conduct.md"]),

    # --- added with the corpus extension -----------------------------------
    ("Who does the Head of Finance report to?", "public", ["Org_Chart_2026.md"]),
    ("Who is on the Executive Committee?", "public", ["Org_Chart_2026.md"]),
    ("What is on the product roadmap for 2027?", "public", ["Product_Roadmap_2026-2027.md"]),
    ("How can an employee report a concern anonymously?", "public",
     ["Whistleblowing_Policy.md", "Code_of_Conduct.md"]),
    ("What lead time do we quote to customers?", "public",
     ["FAQ_for_Customers.md", "VoltStack2_Product_Overview.md"]),
    ("Who may approve a purchase order above 25,000 euro?", "legal",
     ["Authority_Matrix_2026.md"]),
    ("Is the company involved in any litigation?", "legal",
     ["Litigation_and_Claims_Register.md"]),
    ("What was decided at the annual general meeting?", "legal",
     ["AGM_Minutes_2026-04-24.md"]),
    ("Which suppliers have not completed the security assessment?", "legal",
     ["Supplier_Security_Assessment_Status.xlsx", "ISO27001_Surveillance_Audit_Report_2026.pdf"]),
    ("What is the liability insurance limit?", "legal",
     ["Insurance_Programme_2026.md"]),
    ("May the AI platform vendor train models on our documents?", "legal",
     ["Data_Processing_Agreement_ViVeSec.pdf", "ViVeSec_AI_Box_Agreement_2026.pdf"]),
    ("How much did the auxiliary transformer change request cost?", "legal",
     ["Vestkraft_Change_Request_CR-HEL-07.pdf"]),
    ("Which cost centre overspent in Q2 2026?", "finance",
     ["Q2_2026_Cost_Centre_Report.xlsx"]),
    ("How much is overdue by more than 90 days?", "finance",
     ["Aged_Receivables_Commentary_2026-06.md", "Aged_Receivables_2026-06.csv"]),
    ("What discount can the Head of Sales approve alone?", "finance",
     ["Pricing_Guideline_2026.md"]),
    ("What is the list price of a VoltStack 2 unit?", "finance",
     ["Pricing_Guideline_2026.md", "Proposal_EWerk_Allgaeu_2026.md"]),
    ("What hedge ratio does the treasury policy require?", "finance",
     ["Treasury_FX_Policy.md"]),
    ("How did July 2026 perform against plan?", "finance", ["Monthly_Flash_2026-07.md"]),
    ("What is the training budget for 2026?", "hr", ["Training_Plan_2026.md"]),
    ("How many lost time incidents were there in the first half of 2026?", "hr",
     ["Health_and_Safety_Report_H1_2026.md"]),
    ("What is the adjusted gender pay gap?", "hr", ["Diversity_Report_2025.md"]),
    ("Mennyi az éves alapszabadság a magyar cégnél?", "hr",
     ["Munkavallaloi_kezikonyv_kivonat_HU.md", "Employee_Handbook_v6.docx"]),
    ("Wie viele Urlaubstage gibt es in der deutschen Gesellschaft?", "hr",
     ["Mitarbeiterhandbuch_Auszug_DE.md", "Employee_Handbook_v6.docx"]),
    ("Hvor mange timer er den normale arbejdsuge i Danmark?", "hr",
     ["Medarbejderhaandbog_uddrag_DA.md", "Employee_Handbook_v6.docx"]),
    ("What is missing from the type approval dossier?", "engineering",
     ["Meridian_Type_Approval_Dossier_Index.md"]),
    ("What security level does the controller target?", "engineering",
     ["GC3000_Cybersecurity_Concept.md"]),
    ("What did the Esbjerg site survey find about the noise limit?", "engineering",
     ["Helios_Site_Survey_Esbjerg.md"]),
    ("When is provisional acceptance for the Regensburg project?", "engineering",
     ["Regensburg_Project_Plan.md"]),
    ("Which alternative cell supplier passed qualification and at what price premium?", "engineering",
     ["Supplier_Qualification_LM_Cells.md", "Decision_Memo_Alternative_Cell_Supplier.md"]),
    ("What was the main lesson learned from the 2025 projects?", "engineering",
     ["Lessons_Learned_2025.md"]),
    ("How is the AI Box index backed up?", "engineering",
     ["Backup_and_Recovery_Plan.md", "Data_Inventory_2026.md"]),
    ("Which markets require IEEE 1547-2018?", "engineering",
     ["Grid_Code_Compliance_Matrix.xlsx"]),
    ("How many coolant pumps do we keep in stock?", "engineering",
     ["Spare_Parts_Inventory.csv", "Field_Service_Report_2026-06.md"]),
]


def out_of_corpus(b: Bible) -> list[tuple[str, str]]:
    """(question, topic) pairs straight from the bible's not_in_corpus block."""
    return [(item["sample_question"], item["topic"]) for item in b.d["not_in_corpus"]]


def extra_out_of_corpus() -> list[tuple[str, str]]:
    """Plausible-sounding questions about neighbouring but absent topics."""
    return [
        ("What is the warranty on the solar inverter product line?",
         "no solar inverter product exists"),
        ("Who is the head of the Warsaw office?", "no Polish entity"),
        ("What was the outcome of the 2026 employee engagement survey?",
         "no engagement survey document"),
        ("How much did we spend on marketing events in Q2 2026?",
         "no marketing event cost breakdown"),
        ("What is the notice period in the Nordkraft framework agreement?",
         "Nordkraft is an opportunity, not a signed contract"),
        ("What is the pension contribution rate for Danish employees?",
         "pension terms are not in any policy document"),
        ("How many employees were made redundant in 2026?",
         "there was no redundancy programme"),
        ("What is the revenue share agreement with our distributors?",
         "the company sells direct; there are no distributors"),
        ("Which of our sites is ISO 45001 certified?",
         "only ISO 27001 and ISO 9001 are held"),
        ("What did the board decide about the share buyback?",
         "no buyback was proposed or discussed"),
    ]


def build(b: Bible) -> dict:
    return {
        "in_corpus": [
            {"question": q, "drive": drive, "expect_documents": docs}
            for q, drive, docs in IN_CORPUS
        ],
        "out_of_corpus": [
            {"question": q, "reason": topic, "source": "bible.not_in_corpus"}
            for q, topic in out_of_corpus(b)
        ] + [
            {"question": q, "reason": topic, "source": "neighbouring-topic"}
            for q, topic in extra_out_of_corpus()
        ],
    }
