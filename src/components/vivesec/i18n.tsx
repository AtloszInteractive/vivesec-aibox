import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { translateExact } from "./i18n-data";

/* =========================================================================
   ViVeSec AI — lightweight i18n architecture
   Supports EN / HU / DA / DE with localStorage persistence + EN fallback.
   ========================================================================= */

export type Lang = "EN" | "HU" | "DA" | "DE";

export const LANGS: { code: Lang; label: string; native: string }[] = [
  { code: "EN", label: "English", native: "English" },
  { code: "HU", label: "Hungarian", native: "Magyar" },
  { code: "DA", label: "Danish", native: "Dansk" },
  { code: "DE", label: "German", native: "Deutsch" },
];

const STORAGE_KEY = "vivesec_lang";

/* ---------- Tour step shape ---------- */
export type TourCopy = { title: string; body: string };

/* ---------- Dictionary type ---------- */
export type Dict = {
  headerTitle: string;
  localSecure: string;
  securitySeal: string;
  browseDrive: string;
  hideDrive: string;
  browseDriveFloating: string;
  activeAgents: string;
  // Stands in for the signed-in user: the VVS session carries an opaque id,
  // never a display name.
  youLabel: string;
  bannerLine1: string; // "This is the beginning of 'ViVeSec AI'."
  bannerLine2: string; // "All messages are protected by strong encryption."
  inputPlaceholder: string;
  encryptedFooter: string;
  // Drive
  searchInDriveRoot: string;
  fileName: string;
  driveRoot: string;
  driveRootFull: string;
  filesLabel: string;
  refresh: string;
  toggleView: string;
  // Slash menu
  quickActionsFor: string;
  reset: string;
  noQuickAction: string;
  tapToFill: string;
  // Greeting card
  welcomeTitle: string;
  welcomeBody: string;
  maybeLater: string;
  startTour: string;
  // Tour controls
  stepXofY: (a: number, b: number) => string;
  skipTour: string;
  back: string;
  next: string;
  finishTour: string;
  tourSkippedToast: string;
  tourDoneToast: string;
  // Slash command descriptions keyed by command
  slashDesc: Record<string, string>;
  // Tour steps keyed by target
  tour: Record<"header" | "composer" | "slash" | "card" | "drive", TourCopy>;
};

/* ============================ EN ============================ */
const EN: Dict = {
  headerTitle: "ViVeSec AI",
  localSecure: "Local & Secure · Offline Infrastructure",
  securitySeal: "🔒 Local & Secure | Offline Infrastructure",
  browseDrive: "Browse Drive",
  hideDrive: "Hide Drive",
  browseDriveFloating: "Browse drive",
  activeAgents: "Active AI Agents",
  youLabel: "You",
  bannerLine1: "This is the beginning of 'ViVeSec AI'.",
  bannerLine2: "All messages are protected by strong encryption.",
  inputPlaceholder: "Type message here…  ( / for commands )",
  encryptedFooter: "End-to-end encrypted · processed on-device",
  searchInDriveRoot: "Search in (DriveRoot)",
  fileName: "File name",
  driveRoot: "DriveRoot",
  driveRootFull: "ViVeSecBox · DriveRoot",
  filesLabel: "Files",
  refresh: "Refresh",
  toggleView: "Toggle view",
  quickActionsFor: "Quick actions for",
  reset: "Reset",
  noQuickAction: "No quick action matches — press Enter to run as-is.",
  tapToFill: "Tap to fill",
  welcomeTitle: "Welcome to ViVeSec AI",
  welcomeBody:
    "Take a 30-second guided tour through the local sovereignty shield, slash commands, action cards, and your secure DriveRoot.",
  maybeLater: "Maybe later",
  startTour: "🚀 Start Quick Tour",
  stepXofY: (a, b) => `Step ${a} of ${b}`,
  skipTour: "Skip Tour",
  back: "Back",
  next: "Next",
  finishTour: "Finish Tour 🎉",
  tourSkippedToast: "Tour skipped — you can restart it from the greeting card.",
  tourDoneToast: "You're all set 🎉  ViVeSec is ready for action.",
  slashDesc: {
    "/summary": "Create meeting summary & follow-up",
    "/report": "Generate weekly status report",
    "/tracking": "Project summary & task tracking",
    "/memo": "Draft a decision memo",
    "/search": "Intelligent knowledge retrieval",
    "/presentation": "Draft a slide deck from a document",
    "/workflow": "Corporate data insight & platform status",
    "/data": "Corporate data insight & platform status",
    "/legal": "Legal review, clauses & contract risk",
    "/finance": "Financial analysis, budgets & forecasts",
    "/hr": "Talent, hiring & workforce policies",
    "/strategy": "Grant strategy & program design",
    "/sales": "Pipeline, accounts & sales motions",
    "/compliance": "Controls, audits & regulatory posture",
    "/exec": "Executive decisions & board-level briefs",
    "/tour": "Replay the guided onboarding tour",
  },
  tour: {
    header: {
      title: "🔒 Local Infrastructure Status",
      body: "This indicator confirms that your workspace is running entirely within the closed physical circuit of your company's ViVeSecBox and AI Box. Absolutely zero data or telemetry leaves the building.",
    },
    composer: {
      title: "💬 Context-Aware Text Engine",
      body: "Your primary gateway for standard communication. Type natural language queries, paste rough meeting transcripts, or ask direct questions about historical data. The AI processes your prompt against locally indexed intelligence.",
    },
    slash: {
      title: "⚡ Fast Action Playbooks",
      body: "The core accelerator of the platform. Clicking this or typing '/' bypasses manual prompt engineering. It instantly reveals ready-to-run enterprise modules like meeting summaries, weekly status reports, presentation builders, and legal workflow optimization matrices.",
    },
    card: {
      title: "📊 Action-Oriented AI Outputs",
      body: "ViVeSec does not return flat paragraphs. It outputs operational UI widgets. Inside these cards, you can actively check off tasks, swipe through 16:9 presentation slides, edit text directly, or trigger a local one-click automation.",
    },
    drive: {
      title: "📁 DriveRoot & Split-View Verification",
      body: "Your local secure file repository. Whenever the AI references a contract or report in the chat, clicking its citation source opens this panel in a side-by-side Split-View, instantly scrolling and highlighting the exact matching sentence for absolute verification.",
    },
  },
};

/* ============================ HU ============================ */
const HU: Dict = {
  headerTitle: "ViVeSec AI",
  localSecure: "Helyi és biztonságos · Offline infrastruktúra",
  securitySeal: "🔒 Helyi és biztonságos | Offline infrastruktúra",
  browseDrive: "Meghajtó tallózása",
  hideDrive: "Meghajtó elrejtése",
  browseDriveFloating: "Meghajtó tallózása",
  activeAgents: "Aktív AI ügynökök",
  youLabel: "Te",
  bannerLine1: "Ez a 'ViVeSec AI' kezdete.",
  bannerLine2: "Minden üzenetet erős titkosítás véd.",
  inputPlaceholder: "Írd ide az üzenetet…  ( / a parancsokhoz )",
  encryptedFooter: "Végpontok közötti titkosítás · helyben feldolgozva",
  searchInDriveRoot: "Keresés a (DriveRoot)-ban",
  fileName: "Fájlnév",
  driveRoot: "DriveRoot",
  driveRootFull: "ViVeSecBox · DriveRoot",
  filesLabel: "Fájlok",
  refresh: "Frissítés",
  toggleView: "Nézet váltása",
  quickActionsFor: "Gyors műveletek ehhez:",
  reset: "Visszaállítás",
  noQuickAction: "Nincs illeszkedő gyors művelet — nyomd meg az Entert a futtatáshoz.",
  tapToFill: "Koppints a kitöltéshez",
  welcomeTitle: "Üdvözöl a ViVeSec AI",
  welcomeBody:
    "Indíts egy 30 másodperces vezetett bemutatót a helyi szuverenitási pajzson, a perjeles parancsokon, a műveleti kártyákon és a biztonságos DriveRoot-on keresztül.",
  maybeLater: "Talán később",
  startTour: "🚀 Gyors bemutató indítása",
  stepXofY: (a, b) => `${a}. lépés / ${b}`,
  skipTour: "Bemutató kihagyása",
  back: "Vissza",
  next: "Tovább",
  finishTour: "Bemutató befejezése 🎉",
  tourSkippedToast: "Bemutató kihagyva — bármikor újraindíthatod az üdvözlőkártyáról.",
  tourDoneToast: "Minden készen áll 🎉  A ViVeSec indulásra kész.",
  slashDesc: {
    "/summary": "Megbeszélés összefoglalása és teendők",
    "/report": "Heti állapotjelentés készítése",
    "/tracking": "Projekt-összefoglaló és feladatkövetés",
    "/memo": "Döntési feljegyzés megfogalmazása",
    "/search": "Intelligens tudásvisszakeresés",
    "/presentation": "Diabemutató készítése dokumentumból",
    "/workflow": "Vállalati adat-áttekintés és platform-állapot",
    "/data": "Vállalati adat-áttekintés és platform-állapot",
    "/legal": "Jogi felülvizsgálat, záradékok és szerződési kockázat",
    "/finance": "Pénzügyi elemzés, költségvetés és előrejelzés",
    "/hr": "Tehetség, toborzás és munkaerő-szabályzatok",
    "/strategy": "Pályázati stratégia és programtervezés",
    "/sales": "Pipeline, ügyfelek és értékesítési lépések",
    "/compliance": "Kontrollok, auditok és szabályozási megfelelés",
    "/exec": "Vezetői döntések és igazgatósági tájékoztatók",
    "/tour": "A vezetett bemutató újrajátszása",
  },
  tour: {
    header: {
      title: "🔒 Helyi infrastruktúra állapota",
      body: "Ez a jelző megerősíti, hogy a munkaterületed teljes egészében a céged ViVeSecBox és AI Box zárt fizikai áramkörén belül fut. Egyetlen adat vagy telemetria sem hagyja el az épületet.",
    },
    composer: {
      title: "💬 Kontextusérzékeny szövegmotor",
      body: "Az általános kommunikáció elsődleges kapuja. Írj természetes nyelvű kérdéseket, illessz be nyers jegyzőkönyveket, vagy tegyél fel közvetlen kérdéseket a korábbi adatokról. Az AI a helyben indexelt tudás alapján dolgozza fel a kérésedet.",
    },
    slash: {
      title: "⚡ Gyors művelet forgatókönyvek",
      body: "A platform fő gyorsítója. Erre kattintva vagy a '/' beírásával átugorhatod a kézi prompt-tervezést. Azonnal előhívja a kész vállalati modulokat: megbeszélés-összefoglalók, heti jelentések, prezentációkészítők és jogi munkafolyamat-optimalizáló mátrixok.",
    },
    card: {
      title: "📊 Cselekvésorientált AI kimenetek",
      body: "A ViVeSec nem lapos bekezdéseket ad vissza. Működő UI widgeteket állít elő. E kártyákon belül kipipálhatsz feladatokat, lapozhatsz 16:9-es diákon, közvetlenül szerkeszthetsz szöveget, vagy elindíthatsz egy helyi, egykattintásos automatizálást.",
    },
    drive: {
      title: "📁 DriveRoot és osztott nézetű ellenőrzés",
      body: "A helyi, biztonságos fájltárolód. Amikor az AI szerződésre vagy jelentésre hivatkozik a csevegésben, a forrásra kattintva ez a panel osztott nézetben nyílik meg, azonnal odagörgetve és kiemelve a pontos mondatot a teljes ellenőrzéshez.",
    },
  },
};

/* ============================ DA ============================ */
const DA: Dict = {
  headerTitle: "ViVeSec AI",
  localSecure: "Lokal & sikker · Offline infrastruktur",
  securitySeal: "🔒 Lokal & sikker | Offline infrastruktur",
  browseDrive: "Gennemse drev",
  hideDrive: "Skjul drev",
  browseDriveFloating: "Gennemse drev",
  activeAgents: "Aktive AI-agenter",
  youLabel: "Dig",
  bannerLine1: "Dette er begyndelsen på 'ViVeSec AI'.",
  bannerLine2: "Alle beskeder er beskyttet af stærk kryptering.",
  inputPlaceholder: "Skriv besked her…  ( / for kommandoer )",
  encryptedFooter: "End-to-end-krypteret · behandlet på enheden",
  searchInDriveRoot: "Søg i (DriveRoot)",
  fileName: "Filnavn",
  driveRoot: "DriveRoot",
  driveRootFull: "ViVeSecBox · DriveRoot",
  filesLabel: "Filer",
  refresh: "Opdater",
  toggleView: "Skift visning",
  quickActionsFor: "Hurtige handlinger for",
  reset: "Nulstil",
  noQuickAction: "Ingen hurtig handling matcher — tryk Enter for at køre som det er.",
  tapToFill: "Tryk for at udfylde",
  welcomeTitle: "Velkommen til ViVeSec AI",
  welcomeBody:
    "Tag en 30-sekunders guidet rundvisning gennem det lokale suverænitetsskjold, skråstreg-kommandoer, handlingskort og din sikre DriveRoot.",
  maybeLater: "Måske senere",
  startTour: "🚀 Start hurtig rundvisning",
  stepXofY: (a, b) => `Trin ${a} af ${b}`,
  skipTour: "Spring rundvisning over",
  back: "Tilbage",
  next: "Næste",
  finishTour: "Afslut rundvisning 🎉",
  tourSkippedToast: "Rundvisning sprunget over — du kan genstarte den fra velkomstkortet.",
  tourDoneToast: "Du er klar 🎉  ViVeSec er klar til handling.",
  slashDesc: {
    "/summary": "Opret mødereferat & opfølgning",
    "/report": "Generer ugentlig statusrapport",
    "/tracking": "Projektoversigt og opgavesporing",
    "/memo": "Udarbejd et beslutningsnotat",
    "/search": "Intelligent vidensøgning",
    "/presentation": "Udarbejd en præsentation fra et dokument",
    "/workflow": "Virksomhedsdataoverblik og platformstatus",
    "/data": "Virksomhedsdataoverblik og platformstatus",
    "/legal": "Juridisk gennemgang, klausuler & kontraktrisiko",
    "/finance": "Finansiel analyse, budgetter & prognoser",
    "/hr": "Talent, ansættelse & personalepolitik",
    "/strategy": "Bevillingsstrategi & programdesign",
    "/sales": "Pipeline, kunder & salgsbevægelser",
    "/compliance": "Kontroller, revisioner & regulatorisk position",
    "/exec": "Ledelsesbeslutninger & bestyrelsesoplæg",
    "/tour": "Afspil den guidede rundvisning igen",
  },
  tour: {
    header: {
      title: "🔒 Lokal infrastrukturstatus",
      body: "Denne indikator bekræfter, at dit arbejdsområde kører helt inden for din virksomheds lukkede fysiske kredsløb af ViVeSecBox og AI Box. Absolut ingen data eller telemetri forlader bygningen.",
    },
    composer: {
      title: "💬 Kontekstbevidst tekstmotor",
      body: "Din primære indgang til standardkommunikation. Skriv naturlige forespørgsler, indsæt rå mødereferater, eller stil direkte spørgsmål om historiske data. AI'en behandler din prompt mod lokalt indekseret viden.",
    },
    slash: {
      title: "⚡ Hurtige handlingsdrejebøger",
      body: "Platformens centrale accelerator. Klik her eller skriv '/' for at springe manuel prompt-engineering over. Det viser straks klar-til-brug virksomhedsmoduler som mødereferater, ugentlige statusrapporter, præsentationsbyggere og juridiske workflow-optimeringsmatricer.",
    },
    card: {
      title: "📊 Handlingsorienterede AI-output",
      body: "ViVeSec returnerer ikke flade afsnit. Den udsender operationelle UI-widgets. Inde i disse kort kan du aktivt afkrydse opgaver, bladre gennem 16:9-præsentationsdias, redigere tekst direkte eller udløse en lokal et-kliks-automatisering.",
    },
    drive: {
      title: "📁 DriveRoot & verifikation i delt visning",
      body: "Dit lokale, sikre filarkiv. Når AI'en henviser til en kontrakt eller rapport i chatten, åbner et klik på kilden dette panel i en delt visning side om side, der straks ruller til og fremhæver den nøjagtige matchende sætning til fuld verifikation.",
    },
  },
};

/* ============================ DE ============================ */
const DE: Dict = {
  headerTitle: "ViVeSec AI",
  localSecure: "Lokal & sicher · Offline-Infrastruktur",
  securitySeal: "🔒 Lokal & sicher | Offline-Infrastruktur",
  browseDrive: "Laufwerk durchsuchen",
  hideDrive: "Laufwerk ausblenden",
  browseDriveFloating: "Laufwerk durchsuchen",
  activeAgents: "Aktive KI-Agenten",
  youLabel: "Du",
  bannerLine1: "Dies ist der Anfang von 'ViVeSec AI'.",
  bannerLine2: "Alle Nachrichten sind durch starke Verschlüsselung geschützt.",
  inputPlaceholder: "Nachricht hier eingeben…  ( / für Befehle )",
  encryptedFooter: "Ende-zu-Ende-verschlüsselt · auf dem Gerät verarbeitet",
  searchInDriveRoot: "Suchen in (DriveRoot)",
  fileName: "Dateiname",
  driveRoot: "DriveRoot",
  driveRootFull: "ViVeSecBox · DriveRoot",
  filesLabel: "Dateien",
  refresh: "Aktualisieren",
  toggleView: "Ansicht wechseln",
  quickActionsFor: "Schnellaktionen für",
  reset: "Zurücksetzen",
  noQuickAction: "Keine passende Schnellaktion — Enter drücken, um direkt auszuführen.",
  tapToFill: "Zum Ausfüllen tippen",
  welcomeTitle: "Willkommen bei ViVeSec AI",
  welcomeBody:
    "Machen Sie eine 30-sekündige geführte Tour durch den lokalen Souveränitätsschild, Slash-Befehle, Aktionskarten und Ihr sicheres DriveRoot.",
  maybeLater: "Vielleicht später",
  startTour: "🚀 Schnelltour starten",
  stepXofY: (a, b) => `Schritt ${a} von ${b}`,
  skipTour: "Tour überspringen",
  back: "Zurück",
  next: "Weiter",
  finishTour: "Tour abschließen 🎉",
  tourSkippedToast: "Tour übersprungen — Sie können sie über die Begrüßungskarte neu starten.",
  tourDoneToast: "Alles bereit 🎉  ViVeSec ist einsatzbereit.",
  slashDesc: {
    "/summary": "Besprechungszusammenfassung & Follow-up erstellen",
    "/report": "Wöchentlichen Statusbericht erstellen",
    "/tracking": "Projektübersicht & Aufgabenverfolgung",
    "/memo": "Entscheidungsmemo entwerfen",
    "/search": "Intelligente Wissensabfrage",
    "/presentation": "Foliensatz aus einem Dokument erstellen",
    "/workflow": "Unternehmensdaten-Übersicht & Plattformstatus",
    "/data": "Unternehmensdaten-Übersicht & Plattformstatus",
    "/legal": "Rechtsprüfung, Klauseln & Vertragsrisiko",
    "/finance": "Finanzanalyse, Budgets & Prognosen",
    "/hr": "Talente, Einstellung & Personalrichtlinien",
    "/strategy": "Förderstrategie & Programmgestaltung",
    "/sales": "Pipeline, Kunden & Vertriebsbewegungen",
    "/compliance": "Kontrollen, Audits & regulatorische Lage",
    "/exec": "Führungsentscheidungen & Vorstandsbriefings",
    "/tour": "Geführte Einführungstour erneut abspielen",
  },
  tour: {
    header: {
      title: "🔒 Lokaler Infrastrukturstatus",
      body: "Diese Anzeige bestätigt, dass Ihr Arbeitsbereich vollständig innerhalb des geschlossenen physischen Kreislaufs der ViVeSecBox und AI Box Ihres Unternehmens läuft. Absolut keine Daten oder Telemetrie verlassen das Gebäude.",
    },
    composer: {
      title: "💬 Kontextbewusste Text-Engine",
      body: "Ihr primäres Tor für Standardkommunikation. Geben Sie Anfragen in natürlicher Sprache ein, fügen Sie rohe Besprechungsprotokolle ein oder stellen Sie direkte Fragen zu historischen Daten. Die KI verarbeitet Ihre Eingabe anhand lokal indexierter Intelligenz.",
    },
    slash: {
      title: "⚡ Schnellaktions-Playbooks",
      body: "Der zentrale Beschleuniger der Plattform. Ein Klick hier oder die Eingabe von '/' umgeht das manuelle Prompt-Engineering. Es zeigt sofort einsatzbereite Unternehmensmodule wie Besprechungszusammenfassungen, Wochenberichte, Präsentationsbaukästen und juristische Workflow-Optimierungsmatrizen.",
    },
    card: {
      title: "📊 Handlungsorientierte KI-Ausgaben",
      body: "ViVeSec liefert keine flachen Absätze. Es gibt operative UI-Widgets aus. In diesen Karten können Sie Aufgaben aktiv abhaken, durch 16:9-Präsentationsfolien wischen, Text direkt bearbeiten oder eine lokale Ein-Klick-Automatisierung auslösen.",
    },
    drive: {
      title: "📁 DriveRoot & Split-View-Verifizierung",
      body: "Ihr lokales, sicheres Dateirepository. Wann immer die KI im Chat auf einen Vertrag oder Bericht verweist, öffnet ein Klick auf die Quelle dieses Panel in einer nebeneinander angeordneten Split-View, scrollt sofort und hebt den exakt passenden Satz zur vollständigen Verifizierung hervor.",
    },
  },
};

export const DICTS: Record<Lang, Dict> = { EN, HU, DA, DE };

/* ---------- Dynamic mock-data phrase localization ----------
   Replaces known English phrases in mock card content with localized
   equivalents at render time, so visible + new cards update reactively. */
const MOCK_PHRASES: Record<string, Record<Lang, string>> = {
  "Q2 Security Roadmap": {
    EN: "Q2 Security Roadmap",
    HU: "Q2 Biztonsági Útiterv",
    DA: "Q2 Sikkerheds-roadmap",
    DE: "Q2 Sicherheits-Roadmap",
  },
  "Contract Optimization Suggestion": {
    EN: "Contract Optimization Suggestion",
    HU: "Szerződés optimalizálási javaslat",
    DA: "Forslag til kontraktoptimering",
    DE: "Vertragsoptimierungsvorschlag",
  },
  "SLA Obligations": {
    EN: "SLA Obligations",
    HU: "SLA kötelezettségek",
    DA: "SLA-forpligtelser",
    DE: "SLA-Verpflichtungen",
  },
  "Draft Outline": {
    EN: "Draft Outline",
    HU: "Vázlat tervezet",
    DA: "Udkast til disposition",
    DE: "Entwurf der Gliederung",
  },
  "Draft Deck": {
    EN: "Draft Deck",
    HU: "Diabemutató tervezet",
    DA: "Udkast til præsentation",
    DE: "Entwurf des Foliensatzes",
  },
  "Morning Briefing": {
    EN: "Morning Briefing",
    HU: "Reggeli tájékoztató",
    DA: "Morgenbriefing",
    DE: "Morgen-Briefing",
  },
};

export function localizeMock(text: string, lang: Lang): string {
  if (!text || lang === "EN") return text;
  let out = text;
  for (const [en, variants] of Object.entries(MOCK_PHRASES)) {
    if (out.includes(en)) out = out.split(en).join(variants[lang]);
  }
  return out;
}

/* ---------- Unified text translator ----------
   1. exact full-string dictionary (agents, sub-actions, card chrome, outputs)
   2. known mock-data phrase substitution
   Falls back to the original English string. */
export function translateText(text: string, lang: Lang): string {
  if (!text || lang === "EN") return text;
  const exact = translateExact(text, lang);
  if (exact !== null) return exact;
  return localizeMock(text, lang);
}

/* ---------- Context ---------- */
type LanguageContextValue = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: Dict;
  tm: (text: string) => string;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("EN");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem(STORAGE_KEY) as Lang | null;
    if (saved && saved in DICTS) setLangState(saved);
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, l);
  }, []);

  const value = useMemo<LanguageContextValue>(
    () => ({
      lang,
      setLang,
      t: DICTS[lang] ?? EN,
      tm: (text: string) => translateText(text, lang),
    }),
    [lang, setLang],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLang(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    // Safe fallback so components never crash outside the provider.
    return { lang: "EN", setLang: () => {}, t: EN, tm: (s: string) => s };
  }
  return ctx;
}
