import type { Lang } from "./i18n";

export type HwModule = {
  id: string;
  label: string;
  /** Follow-up chat explanation: outcome + deployment timeline */
  explain: string;
};

export type HwConfig = {
  id: "standard" | "enterprise";
  label: string;
  tops: string;
  power: string;
  agents: string;
  bestFor: string;
};

export type HardwareCopy = {
  // intro
  persona: string;
  intro: string;
  chipLabel: string;
  cardTitle: string;
  // layer 1
  l1Tab: string;
  l1Title: string;
  l1Desc: string;
  l1Badges: string[];
  l1Specs: { title: string; detail: string }[];
  runCheck: string;
  scanning: string;
  scanDone: string;
  // layer 2
  l2Tab: string;
  l2Title: string;
  l2Desc: string;
  l2Badge: string;
  configs: HwConfig[];
  modulesTitle: string;
  modules: HwModule[];
  rolloutLabel: string;
  // chrome
  topsLabel: string;
  powerLabel: string;
  agentsLabel: string;
  bestForLabel: string;
  stamp: string;
};

const EN: HardwareCopy = {
  persona: "Infrastructure & Cybersecurity Architect",
  intro:
    "Welcome. I'll walk you through the **ViVeSec Hardware Ecosystem** — a dual-layer, fully offline architecture engineered for data sovereignty and private enterprise AI. Inspect each layer below.",
  chipLabel: "⚙️ Inspect Hardware Infrastructure",
  cardTitle: "ViVeSec Hardware Ecosystem",
  l1Tab: "Layer 1 · ViVeSec Box",
  l1Title: "ViVeSec Box — Protects & Stores",
  l1Desc:
    "Ransomware-proof data protection in a closed ecosystem. The immutable backup appliance that is physically and logically isolated – so ransomware cannot reach, modify or delete your data.",
  l1Badges: ["Common Criteria Certified", "Quantum-Safe", "Ransomware-Proof"],
  l1Specs: [
    { title: "Immutable Backup", detail: "Read-only snapshots, immune to compromised admin credentials." },
    { title: "Zero Attack Surface", detail: "Closed OS, no exposed external admin planes, no installable apps." },
    { title: "Business Continuity", detail: "Rapid recovery with RTO < 60 minutes capability." },
    { title: "Physical Footprint", detail: "Compact: < 2 kg, max 20 W consumption, 30-minute plug-and-play setup." },
  ],
  runCheck: "⚡ Run Mock Integrity Check",
  scanning: "Analyzing SHA-256 + Post-Quantum Signatures…",
  scanDone: "Success: 100% Immutable and Secure",
  l2Tab: "Layer 2 · ViVeSec AI Box",
  l2Title: "ViVeSec AI Box — Private Enterprise AI",
  l2Desc:
    "Private AI that learns from your company, runs offline, and never leaves your infrastructure. ViVeSec AI Box is the only enterprise AI that works exclusively with your own data stored on ViVeSec Box — no internet, no cloud, no token fees. Offline operation without limited intelligence: the NVIDIA platform's internet-scale knowledge is already built into the model.",
  l2Badge: "100% Offline NVIDIA Platform",
  configs: [
    {
      id: "standard",
      label: "Standard",
      tops: "67 TOPS",
      power: "15–25 W",
      agents: "1–2 active agents in parallel",
      bestFor: "Best for micro-SMEs",
    },
    {
      id: "enterprise",
      label: "Enterprise",
      tops: "2,070 TOPS",
      power: "High performance",
      agents: "Up to 7 parallel agents via MIG partitions",
      bestFor: "Best for mid-large or regulated environments",
    },
  ],
  modulesTitle: "Specialized AI Coworker Modules",
  modules: [
    { id: "legal", label: "Legal Ops", explain: "**Legal Ops** automates clause extraction, obligation tracking and contract risk review entirely on-prem. Typical rollout: **15–45 days**." },
    { id: "finance", label: "Finance Assistant", explain: "**Finance Assistant** delivers KPI deltas, cash-flow forecasting and invoice reconciliation with zero token fees. Typical rollout: **15–45 days**." },
    { id: "compliance", label: "Compliance Officer", explain: "**Compliance Officer** maps controls to NIS2/GDPR, flags gaps and drafts audit-ready evidence locally. Typical rollout: **15–45 days**." },
    { id: "hr", label: "Workforce/HR", explain: "**Workforce/HR** streamlines onboarding, policy Q&A and workforce planning on your private network. Typical rollout: **15–45 days**." },
    { id: "sales", label: "Sales Ops", explain: "**Sales Ops** scores pipeline, drafts outreach and surfaces deal risk without data leaving your premises. Typical rollout: **15–45 days**." },
    { id: "funding", label: "Funding Strategy", explain: "**Funding Strategy** matches grants, structures applications and tracks deadlines with full data sovereignty. Typical rollout: **15–45 days**." },
    { id: "exec", label: "Executive Chief of Staff", explain: "**Executive Chief of Staff** synthesizes cross-functional signals into executive-level decision briefs. Typical rollout: **15–45 days**." },
  ],
  rolloutLabel: "Deployment timeline: 15–45 days rollout",
  topsLabel: "Compute",
  powerLabel: "Power draw",
  agentsLabel: "Parallel agents",
  bestForLabel: "Ideal for",
  stamp: "🔒 Hardware Ecosystem verified. Operations running locally on private closed-circuit network.",
};

const HU: HardwareCopy = {
  persona: "Infrastruktúra- és Kiberbiztonsági Architekt",
  intro:
    "Üdvözlöm. Bemutatom a **ViVeSec Hardver Ökoszisztémát** — egy kétrétegű, teljesen offline architektúrát, amelyet az adatszuverenitásra és a privát vállalati MI-re terveztünk. Vizsgálja meg az egyes rétegeket alább.",
  chipLabel: "⚙️ Hardver-infrastruktúra vizsgálata",
  cardTitle: "ViVeSec Hardver Ökoszisztéma",
  l1Tab: "1. réteg · ViVeSec Box",
  l1Title: "ViVeSec Box — Véd és Tárol",
  l1Desc:
    "Zsarolóvírus-biztos adatvédelem zárt ökoszisztémában. A változtathatatlan mentési eszköz, amely fizikailag és logikailag is izolált – így a zsarolóvírus nem férhet hozzá, nem módosíthatja és nem törölheti az adatait.",
  l1Badges: ["Common Criteria tanúsított", "Kvantum-rezisztens titkosítás", "Zsarolóvírus-biztos"],
  l1Specs: [
    { title: "Változtathatatlan mentés", detail: "Csak olvasható, zsarolóvírus-biztos pillanatképek, melyek immunisak a kompromittált admin jelszavakra." },
    { title: "Nulla támadási felület", detail: "Zárt OS, nincsenek kitett külső adminisztrációs felületek, nincsenek telepíthető alkalmazások." },
    { title: "Üzletmenet-folytonosság", detail: "Gyors helyreállítás, RTO < 60 perc képességgel." },
    { title: "Fizikai méret", detail: "Kompakt: < 2 kg, max. 20 W fogyasztás, 30 perces plug-and-play telepítés." },
  ],
  runCheck: "⚡ Integritás-ellenőrzés futtatása",
  scanning: "SHA-256 + Poszt-kvantum aláírások elemzése…",
  scanDone: "Siker: 100% Változtathatatlan és Biztonságos",
  l2Tab: "2. réteg · ViVeSec AI Box",
  l2Title: "ViVeSec AI Box — Privát Vállalati MI",
  l2Desc:
    "Privát MI, amely a cégétől tanul, offline működik, és soha nem hagyja el az infrastruktúráját. A ViVeSec AI Box az egyetlen vállalati MI, amely kizárólag a ViVeSec Boxon tárolt saját adataival dolgozik — internet, felhő és token-díjak nélkül. Offline működés korlátozott intelligencia nélkül: az NVIDIA platform internet-léptékű tudása már beépített a modellbe.",
  l2Badge: "100% Offline NVIDIA platform",
  configs: [
    {
      id: "standard",
      label: "Standard",
      tops: "67 TOPS",
      power: "15–25 W",
      agents: "1–2 aktív ügynök párhuzamosan",
      bestFor: "Mikro-KKV-knak ideális",
    },
    {
      id: "enterprise",
      label: "Enterprise",
      tops: "2 070 TOPS",
      power: "Nagy teljesítmény",
      agents: "Akár 7 párhuzamos ügynök MIG partíciókkal",
      bestFor: "Közép-/nagyvállalati vagy szabályozott környezetekhez",
    },
  ],
  modulesTitle: "Specializált MI Munkatárs Modulok",
  modules: [
    { id: "legal", label: "Jogi Operáció", explain: "A **Jogi Operáció** automatizálja a klauzula-kivonatolást, kötelezettség-követést és szerződéskockázat-elemzést teljesen helyben. Tipikus bevezetés: **15–45 nap**." },
    { id: "finance", label: "Pénzügyi Asszisztens", explain: "A **Pénzügyi Asszisztens** KPI-eltéréseket, cash-flow előrejelzést és számlaegyeztetést nyújt nulla token díjjal. Tipikus bevezetés: **15–45 nap**." },
    { id: "compliance", label: "Megfelelőségi Tiszt", explain: "A **Megfelelőségi Tiszt** a kontrollokat NIS2/GDPR-hez rendeli, hiányosságokat jelez és auditkész bizonyítékot készít helyben. Tipikus bevezetés: **15–45 nap**." },
    { id: "hr", label: "Munkaerő/HR", explain: "A **Munkaerő/HR** egyszerűsíti a beillesztést, szabályzati kérdéseket és a munkaerő-tervezést a privát hálózaton. Tipikus bevezetés: **15–45 nap**." },
    { id: "sales", label: "Értékesítési Operáció", explain: "Az **Értékesítési Operáció** pontozza a pipeline-t, megkereséseket fogalmaz és üzletkockázatot tár fel anélkül, hogy az adat elhagyná a telephelyet. Tipikus bevezetés: **15–45 nap**." },
    { id: "funding", label: "Pályázati Stratégia", explain: "A **Pályázati Stratégia** pályázatokat párosít, kérelmeket strukturál és határidőket követ teljes adatszuverenitással. Tipikus bevezetés: **15–45 nap**." },
    { id: "exec", label: "Vezérigazgatói Stábfőnök", explain: "A **Vezérigazgatói Stábfőnök** a funkcióközi jelzéseket vezetői szintű döntési összefoglalókká szintetizálja. Tipikus bevezetés: **15–45 nap**." },
  ],
  rolloutLabel: "Bevezetési idő: 15–45 napos kiépítés",
  topsLabel: "Számítási kapacitás",
  powerLabel: "Fogyasztás",
  agentsLabel: "Párhuzamos ügynökök",
  bestForLabel: "Ideális",
  stamp: "🔒 Hardver Ökoszisztéma ellenőrizve. A műveletek helyben, privát zárt hálózaton futnak.",
};

const DE: HardwareCopy = {
  persona: "Infrastruktur- und Cybersicherheits-Architekt",
  intro:
    "Willkommen. Ich führe Sie durch das **ViVeSec Hardware-Ökosystem** — eine zweischichtige, vollständig offline betriebene Architektur für Datensouveränität und private Unternehmens-KI. Prüfen Sie jede Schicht unten.",
  chipLabel: "⚙️ Hardware-Infrastruktur prüfen",
  cardTitle: "ViVeSec Hardware-Ökosystem",
  l1Tab: "Schicht 1 · ViVeSec Box",
  l1Title: "ViVeSec Box — Schützt & Speichert",
  l1Desc:
    "Ransomware-sicherer Datenschutz in einem geschlossenen Ökosystem. Die unveränderliche Backup-Appliance, die physisch und logisch isoliert ist – so kann Ransomware Ihre Daten weder erreichen, verändern noch löschen.",
  l1Badges: ["Common Criteria zertifiziert", "Quantensicher", "Ransomware-sicher"],
  l1Specs: [
    { title: "Unveränderliche Backup-Appliance", detail: "Schreibgeschützte Snapshots, immun gegen kompromittierte Admin-Anmeldedaten." },
    { title: "Null Angriffsfläche", detail: "Geschlossene Betriebsumgebung, keine exponierten externen Admin-Ebenen, keine installierbaren Apps." },
    { title: "Geschäftskontinuität", detail: "Schnelle Wiederherstellung mit RTO < 60 Minuten." },
    { title: "Physischer Footprint", detail: "Kompakt: < 2 kg, max. 20 W Verbrauch, 30-Minuten Plug-and-Play-Einrichtung." },
  ],
  runCheck: "⚡ Mock-Integritätsprüfung starten",
  scanning: "Analysiere SHA-256 + Post-Quanten-Signaturen…",
  scanDone: "Erfolg: 100% Unveränderlich und Sicher",
  l2Tab: "Schicht 2 · ViVeSec AI Box",
  l2Title: "ViVeSec AI Box — Private Unternehmens-KI",
  l2Desc:
    "Private KI, die von Ihrem Unternehmen lernt, offline läuft und Ihre Infrastruktur nie verlässt. Die ViVeSec AI Box ist die einzige Unternehmens-KI, die ausschließlich mit Ihren eigenen, auf der ViVeSec Box gespeicherten Daten arbeitet — kein Internet, keine Cloud, keine Token-Gebühren. Offline-Betrieb ohne eingeschränkte Intelligenz: Das internet-weite Wissen der NVIDIA-Plattform ist bereits im Modell integriert.",
  l2Badge: "100% Offline NVIDIA-Plattform",
  configs: [
    {
      id: "standard",
      label: "Standard",
      tops: "67 TOPS",
      power: "15–25 W",
      agents: "1–2 aktive Agenten parallel",
      bestFor: "Ideal für Kleinstunternehmen",
    },
    {
      id: "enterprise",
      label: "Enterprise",
      tops: "2.070 TOPS",
      power: "Hohe Leistung",
      agents: "Bis zu 7 parallele Agenten über MIG-Partitionen",
      bestFor: "Ideal für mittlere/große oder regulierte Umgebungen",
    },
  ],
  modulesTitle: "Spezialisierte KI-Coworker-Module",
  modules: [
    { id: "legal", label: "Legal Ops", explain: "**Legal Ops** automatisiert Klausel-Extraktion, Pflichtenverfolgung und Vertragsrisikoprüfung vollständig vor Ort. Typische Einführung: **15–45 Tage**." },
    { id: "finance", label: "Finanz-Assistent", explain: "Der **Finanz-Assistent** liefert KPI-Abweichungen, Cashflow-Prognosen und Rechnungsabgleich ohne Token-Gebühren. Typische Einführung: **15–45 Tage**." },
    { id: "compliance", label: "Compliance Officer", explain: "Der **Compliance Officer** ordnet Kontrollen NIS2/DSGVO zu, erkennt Lücken und erstellt auditfähige Nachweise lokal. Typische Einführung: **15–45 Tage**." },
    { id: "hr", label: "Personal/HR", explain: "**Personal/HR** vereinfacht Onboarding, Richtlinien-Q&A und Personalplanung in Ihrem privaten Netzwerk. Typische Einführung: **15–45 Tage**." },
    { id: "sales", label: "Sales Ops", explain: "**Sales Ops** bewertet die Pipeline, formuliert Ansprache und deckt Deal-Risiken auf, ohne dass Daten Ihr Gelände verlassen. Typische Einführung: **15–45 Tage**." },
    { id: "funding", label: "Förderstrategie", explain: "Die **Förderstrategie** ordnet Förderungen zu, strukturiert Anträge und verfolgt Fristen mit voller Datensouveränität. Typische Einführung: **15–45 Tage**." },
    { id: "exec", label: "Executive Chief of Staff", explain: "Der **Executive Chief of Staff** verdichtet bereichsübergreifende Signale zu Entscheidungs-Briefings auf Führungsebene. Typische Einführung: **15–45 Tage**." },
  ],
  rolloutLabel: "Bereitstellungszeit: 15–45 Tage Einführung",
  topsLabel: "Rechenleistung",
  powerLabel: "Leistungsaufnahme",
  agentsLabel: "Parallele Agenten",
  bestForLabel: "Ideal für",
  stamp: "🔒 Hardware-Ökosystem verifiziert. Betrieb läuft lokal in einem privaten, geschlossenen Netzwerk.",
};

const MAP: Record<string, HardwareCopy> = { EN, HU, DE };

export function getHardwareCopy(lang: Lang): HardwareCopy {
  // Danish (DA) and any unknown locale fall back elegantly to English.
  return MAP[lang] ?? EN;
}
