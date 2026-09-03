import type { Lang } from "./i18n";

/* ============================================================================
 * CORPORATE DATA INSIGHT SUMMARY  ( /data )
 *
 * A 100% local platform snapshot: the left column reports the ViVeSecBox side
 * (what the drive sync handed over), the right column the AI Box side (index +
 * runtime). The figures come from the adapter's own status document
 * (`platformInsight` server fn) — anything the box does not report is shown as
 * "no data" instead of a plausible-looking number.
 * ==========================================================================*/

/* ============================================================================
 * Localized dashboard copy (reactive to the platform language selector).
 * Hungarian mirrors the embedded diagnostics prompt exactly.
 * ==========================================================================*/
export type DataInsightCopy = {
  persona: string;
  intro: string;
  chipLabel: string;
  cardTitle: string;
  classification: string;
  classificationValue: string;
  networkMode: string;
  networkModeValue: string;
  kpiVvsUsers: string;
  kpiSecuredFiles: string;
  kpiDataSize: string;
  kpiMessages: string;
  filesUnit: string;
  encryptedNote: string;
  auditedNote: string;
  backupTitle: string;
  lastBackupTime: string;
  lastBackupSize: string;
  fileDistribution: string;
  fDocs: string;
  fEmails: string;
  fImages: string;
  fVideos: string;
  fAudio: string;
  fOther: string;
  aiTitle: string;
  activeUsers: string;
  totalRequests: string;
  functionalDist: string;
  memos: string;
  searches: string;
  summaries: string;
  presentations: string;
  emailDrafts: string;
  computeProfile: string;
  llmEngine: string;
  tokenSpeed: string;
  gpuAlloc: string;
  gpuAllocValue: (n: number) => string;
  powerDraw: string;
  auditTitle: string;
  confidence: string;
  confidenceValue: string;
  sources: string;
  sourcesValue: string;
  auditId: string;
  stamp: string;
  /* ---- live telemetry (only what the box actually reports) ---- */
  noData: string;
  noDataHint: string;
  boxTitle: string;
  kpiDrives: string;
  kpiFolders: string;
  kpiIndexedChunks: string;
  perDrive: string;
  storageMode: string;
  wsFs: string;
  wsFsOn: string;
  wsFsOff: string;
  indexTitle: string;
  docs: string;
  pages: string;
  chunks: string;
  corpora: string;
  runtime: string;
  activeSessions: string;
  generatedFiles: string;
  features: string;
  watchdog: string;
  loading: string;
  loadFailed: string;
};

const HU: DataInsightCopy = {
  persona: "Rendszermonitorozó Motor",
  intro:
    "Összeállítom a **Vállalati Adatbetekintő Összefoglalót** — egy teljesen lokális, air-gapped diagnosztikai pillanatképet a ViVeSecBox és az AI Box állapotáról. Az adatok kizárólag a belső hálózaton kerültek feldolgozásra.",
  chipLabel: "Adatbetekintő összefoglaló",
  cardTitle: "VIVESEC LOKÁLIS RENDSZERÁLLAPOT & ADATBIZTONSÁGI JELENTÉS",
  classification: "Adatbiztonsági besorolás",
  classificationValue: "BELSŐ / BIZALMAS",
  networkMode: "Hálózati üzemmód",
  networkModeValue: "Zárt LAN (Air-Gapped) · Public Cloud kitettség: 0%",
  kpiVvsUsers: "VVS Felhasználók száma",
  kpiSecuredFiles: "Titkosított fájlok száma",
  kpiDataSize: "Biztonságos adatvagyon mérete",
  kpiMessages: "Helyileg tárolt AI üzenetek",
  filesUnit: "db",
  encryptedNote: "AES-256 SIV módban zárolva",
  auditedNote: "Audit naplózva",
  backupTitle: "Adattárolási és mentési összegzés",
  lastBackupTime: "Utolsó sikeres mentés ideje",
  lastBackupSize: "Biztonsági mentés mérete",
  fileDistribution: "Fájltípusok szerinti megoszlás",
  fDocs: "📄 Dokumentumok (PDF, Word, Excel, ERP)",
  fEmails: "📧 E-mailek és üzenetváltások",
  fImages: "🖼️ Képi állományok",
  fVideos: "🎥 Videó fájlok",
  fAudio: "🎵 Hanganyagok és hangfelvételek",
  fOther: "📦 Egyéb strukturálatlan állományok",
  aiTitle: "AI Box inferencia összegzés",
  activeUsers: "Aktív felhasználók száma",
  totalRequests: "Összes feldolgozott AI kérelem",
  functionalDist: "Lekérdezések funkcionális megoszlása",
  memos: "📝 Döntési memók és emlékeztetők",
  searches: "🔍 Szemantikus keresések (RAG)",
  summaries: "📋 Találkozó összefoglalók",
  presentations: "🖥️ Prezentációs vázlatok",
  emailDrafts: "✉️ Követő e-mail tervezetek",
  computeProfile: "Számítási profil & telemetria",
  llmEngine: "Lokális LLM motor",
  tokenSpeed: "token/mp",
  gpuAlloc: "GPU erőforrás lefoglalás",
  gpuAllocValue: (n) => `${n} / 7 aktív partíció`,
  powerDraw: "Fogyasztás",
  auditTitle: "Adatkontroll & Audit info",
  confidence: "Confidence Score",
  confidenceValue: "100% (Determinisztikus rendszermetrikák)",
  sources: "Felhasznált források",
  sourcesValue: "ViVeSecBox Host OS API · pgvector System Catalog · AI Box Log Orchestrator",
  auditId: "Audit ID",
  stamp: "A diagnosztika 100%-ban lokálisan, a ViVeSecBoxon készült. Semmilyen adat nem hagyta el a belső hálózatot.",
  noData: "nincs adat",
  noDataHint: "a box jelenleg nem szolgáltat ehhez adatot",
  boxTitle: "ViVeSecBox — tárolás és mentés",
  kpiDrives: "Drive-ok száma",
  kpiFolders: "Mappák száma",
  kpiIndexedChunks: "Indexelt szövegrészek",
  perDrive: "Megoszlás drive szerint",
  storageMode: "Tároló üzemmód",
  wsFs: "Fájlmentési csatorna",
  wsFsOn: "csatlakoztatva",
  wsFsOff: "nincs kapcsolat",
  indexTitle: "Tudásbázis (index)",
  docs: "Dokumentumok",
  pages: "Oldalak",
  chunks: "Szövegrészek",
  corpora: "Korpuszok (drive-ok)",
  runtime: "Futásidő és erőforrás",
  activeSessions: "Aktív beszélgetések",
  generatedFiles: "Tárolt generált fájlok",
  features: "Engedélyezett funkciók",
  watchdog: "Jelenlét-figyelő (mp)",
  loading: "Rendszerállapot lekérése a boxról…",
  loadFailed: "A rendszerállapot nem kérhető le a boxról.",
};

const EN: DataInsightCopy = {
  persona: "System Monitoring Engine",
  intro:
    "Compiling the **Corporate Data Insight Summary** — a fully local, air-gapped diagnostic snapshot of ViVeSecBox and AI Box state. All data was processed on the internal network only.",
  chipLabel: "Corporate Data Insight",
  cardTitle: "VIVESEC LOCAL SYSTEM STATE & DATA SECURITY REPORT",
  classification: "Data classification",
  classificationValue: "INTERNAL / CONFIDENTIAL",
  networkMode: "Network mode",
  networkModeValue: "Closed LAN (Air-Gapped) · Public Cloud exposure: 0%",
  kpiVvsUsers: "Number of VVS Users",
  kpiSecuredFiles: "Number of Secured Files",
  kpiDataSize: "Size of Secured Data",
  kpiMessages: "Secured Messages",
  filesUnit: "",
  encryptedNote: "Locked in AES-256 SIV mode",
  auditedNote: "Audit logged",
  backupTitle: "Backup Summary",
  lastBackupTime: "Last backup time",
  lastBackupSize: "Last backup size",
  fileDistribution: "File type distribution",
  fDocs: "📄 Documents (PDF, Word, Excel, ERP)",
  fEmails: "📧 Emails & correspondence",
  fImages: "🖼️ Images",
  fVideos: "🎥 Videos",
  fAudio: "🎵 Sound files & recordings",
  fOther: "📦 Other unstructured files",
  aiTitle: "AI Box Summary",
  activeUsers: "Active users",
  totalRequests: "Total AI requests processed",
  functionalDist: "Functional request distribution",
  memos: "📝 Decision memos & reminders",
  searches: "🔍 Semantic searches (RAG)",
  summaries: "📋 Meeting summaries",
  presentations: "🖥️ Presentation outlines",
  emailDrafts: "✉️ Follow-up email drafts",
  computeProfile: "Compute profile & telemetry",
  llmEngine: "Local LLM engine",
  tokenSpeed: "tokens/sec",
  gpuAlloc: "GPU resource allocation",
  gpuAllocValue: (n) => `${n} / 7 active partitions`,
  powerDraw: "Power draw",
  auditTitle: "Data Control & Audit Info",
  confidence: "Confidence Score",
  confidenceValue: "100% (Deterministic system metrics)",
  sources: "Sources",
  sourcesValue: "ViVeSecBox Host OS API · pgvector System Catalog · AI Box Log Orchestrator",
  auditId: "Audit ID",
  stamp: "Diagnostics generated 100% locally on the ViVeSecBox. No data left the internal network.",
  noData: "no data",
  noDataHint: "the box does not report this yet",
  boxTitle: "ViVeSecBox — storage & backup",
  kpiDrives: "Drives",
  kpiFolders: "Folders",
  kpiIndexedChunks: "Indexed passages",
  perDrive: "Breakdown per drive",
  storageMode: "Storage mode",
  wsFs: "File-save channel",
  wsFsOn: "connected",
  wsFsOff: "not connected",
  indexTitle: "Knowledge base (index)",
  docs: "Documents",
  pages: "Pages",
  chunks: "Passages",
  corpora: "Corpora (drives)",
  runtime: "Runtime & resources",
  activeSessions: "Active conversations",
  generatedFiles: "Stored generated files",
  features: "Licensed features",
  watchdog: "Presence watchdog (s)",
  loading: "Reading platform state from the box…",
  loadFailed: "Platform state could not be read from the box.",
};

const DE: DataInsightCopy = {
  ...EN,
  persona: "System-Monitoring-Engine",
  intro:
    "Ich erstelle die **Corporate Data Insight Summary** — eine vollständig lokale, air-gapped Diagnose des ViVeSecBox- und AI-Box-Status. Alle Daten wurden ausschließlich im internen Netzwerk verarbeitet.",
  chipLabel: "Corporate Data Insight",
  cardTitle: "VIVESEC LOKALER SYSTEMSTATUS & DATENSICHERHEITSBERICHT",
  classification: "Datenklassifizierung",
  classificationValue: "INTERN / VERTRAULICH",
  networkMode: "Netzwerkmodus",
  networkModeValue: "Geschlossenes LAN (Air-Gapped) · Public-Cloud-Exposition: 0%",
  kpiVvsUsers: "Anzahl VVS-Benutzer",
  kpiSecuredFiles: "Anzahl gesicherter Dateien",
  kpiDataSize: "Größe der gesicherten Daten",
  kpiMessages: "Gesicherte Nachrichten",
  encryptedNote: "Im AES-256-SIV-Modus gesperrt",
  auditedNote: "Audit protokolliert",
  backupTitle: "Backup-Zusammenfassung",
  lastBackupTime: "Letzte Sicherung",
  lastBackupSize: "Größe der letzten Sicherung",
  fileDistribution: "Dateityp-Verteilung",
  fDocs: "📄 Dokumente (PDF, Word, Excel, ERP)",
  fEmails: "📧 E-Mails & Korrespondenz",
  fImages: "🖼️ Bilder",
  fVideos: "🎥 Videos",
  fAudio: "🎵 Audiodateien & Aufnahmen",
  fOther: "📦 Sonstige unstrukturierte Dateien",
  aiTitle: "AI-Box-Zusammenfassung",
  activeUsers: "Aktive Benutzer",
  totalRequests: "Verarbeitete AI-Anfragen gesamt",
  functionalDist: "Funktionale Anfrageverteilung",
  memos: "📝 Entscheidungsmemos & Erinnerungen",
  searches: "🔍 Semantische Suchen (RAG)",
  summaries: "📋 Meeting-Zusammenfassungen",
  presentations: "🖥️ Präsentationsentwürfe",
  emailDrafts: "✉️ Follow-up-E-Mail-Entwürfe",
  computeProfile: "Rechenprofil & Telemetrie",
  llmEngine: "Lokale LLM-Engine",
  tokenSpeed: "Tokens/Sek.",
  gpuAlloc: "GPU-Ressourcenzuweisung",
  gpuAllocValue: (n) => `${n} / 7 aktive Partitionen`,
  powerDraw: "Leistungsaufnahme",
  auditTitle: "Datenkontrolle & Audit-Info",
  sources: "Quellen",
  stamp: "Diagnose zu 100% lokal auf der ViVeSecBox erstellt. Keine Daten haben das interne Netzwerk verlassen.",
  noData: "keine Daten",
  noDataHint: "die Box liefert dafür derzeit keine Werte",
  boxTitle: "ViVeSecBox — Speicher & Backup",
  kpiDrives: "Laufwerke",
  kpiFolders: "Ordner",
  kpiIndexedChunks: "Indizierte Passagen",
  perDrive: "Verteilung nach Laufwerk",
  storageMode: "Speichermodus",
  wsFs: "Datei-Speicherkanal",
  wsFsOn: "verbunden",
  wsFsOff: "nicht verbunden",
  indexTitle: "Wissensbasis (Index)",
  docs: "Dokumente",
  pages: "Seiten",
  chunks: "Passagen",
  corpora: "Korpora (Laufwerke)",
  runtime: "Laufzeit & Ressourcen",
  activeSessions: "Aktive Gespräche",
  generatedFiles: "Gespeicherte generierte Dateien",
  features: "Lizenzierte Funktionen",
  watchdog: "Präsenz-Watchdog (s)",
  loading: "Plattformstatus wird von der Box gelesen…",
  loadFailed: "Der Plattformstatus konnte nicht von der Box gelesen werden.",
};

const MAP: Record<string, DataInsightCopy> = { EN, HU, DE };

export function getDataInsightCopy(lang: Lang): DataInsightCopy {
  // Danish (DA) and unknown locales fall back to English.
  return MAP[lang] ?? EN;
}
