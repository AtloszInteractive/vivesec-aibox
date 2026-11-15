/* AI Box certificate page.
 *
 * Served over plain HTTP by the HTTPS front (https-front.sh) because a browser
 * that does not trust the box CA never shows anything of the box over HTTPS.
 * It probes https://<this host>/aibox-probe: when that loads, the CA is trusted
 * and the page moves on to the AI Box; otherwise it shows the installation
 * steps for this device and keeps probing. Nothing is sent anywhere else.
 */
(function () {
  "use strict";

  var PROBE_TIMEOUT_MS = 4000;
  var POLL_MS = 4000;
  var LANG_KEY = "vivesec_lang";

  var CMD = {
    winHash: 'Get-FileHash "$env:USERPROFILE\\Downloads\\aibox-ca.cer"',
    winImport:
      'Import-Certificate -FilePath "$env:USERPROFILE\\Downloads\\aibox-ca.cer" -CertStoreLocation Cert:\\LocalMachine\\Root',
    macHash: "shasum -a 256 ~/Downloads/aibox-ca.cer",
    macImport:
      "sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/Downloads/aibox-ca.cer",
    linuxHash: "sha256sum ~/Downloads/aibox-ca.cer",
    debian:
      "sudo openssl x509 -inform DER -in ~/Downloads/aibox-ca.cer -out /usr/local/share/ca-certificates/vivesec-aibox-ca.crt && sudo update-ca-certificates",
    fedora:
      "sudo openssl x509 -inform DER -in ~/Downloads/aibox-ca.cer -out /etc/pki/ca-trust/source/anchors/vivesec-aibox-ca.pem && sudo update-ca-trust",
    nss: 'certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "ViVeSec AI Box CA" -i ~/Downloads/aibox-ca.cer',
  };

  // Text: **bold** marks a menu item or a label on screen. Commands stay in CMD.
  var TEXT = {
    en: {
      langName: "English",
      checking: "Checking the connection to the AI Box…",
      untrusted: "This device does not trust the AI Box certificate yet.",
      untrustedHint: "Install it as shown below; this page keeps checking.",
      trustedNow: "Done: the certificate works.",
      trustedNowHint: "Opening the AI Box…",
      trustedAlready: "This device already trusts the AI Box certificate.",
      onHttps: "The encrypted connection works: the certificate is installed on this device.",
      open: "Open the AI Box",
      title: "Install the AI Box certificate on this device",
      lead: "The AI Box uses its own certificate. Install it once on this device, so the browser opens the AI Box over an encrypted connection without a warning.",
      warningTitle: "Check before installing",
      warningText: "This page and the file arrive over an unencrypted connection. Install the certificate only if its SHA-256 fingerprint matches the handover sheet character by character. If it does not, delete the file and tell the administrator.",
      dlTitle: "Download the certificate",
      dlNote: "The file holds only the public part of the certificate. It lets this device recognise the AI Box; it gives no access to it.",
      chooseTitle: "Choose this device",
      fpTitle: "Check the fingerprint",
      installTitle: "Install",
      doneTitle: "Done",
      doneText: "This page checks every few seconds and opens the AI Box by itself once the certificate works. If it does not notice, restart the browser.",
      admin: "Administrators: in a Windows domain the certificate can be deployed to every computer with Group Policy, so nobody has to install it by hand.",
      copy: "Copy",
      copied: "Copied",
      caseNote: "Upper or lower case does not matter.",
      p: {
        windows: {
          name: "Windows (Edge, Chrome)",
          fp: ["Open **PowerShell** and run:", { cmd: "winHash" }, "The **Hash** value must match the **SHA-256 (Windows)** line of the handover sheet."],
          steps: [
            "Double-click the downloaded **aibox-ca.cer**, then **Install Certificate…**.",
            "Choose **Local Machine** (administrator rights needed), then **Next**.",
            "Choose **Place all certificates in the following store** → **Browse…** → **Trusted Root Certification Authorities** → **OK** → **Next** → **Finish**.",
            ["Or in PowerShell started as administrator:", { cmd: "winImport" }],
            "Edge and Chrome use it at once. For Firefox see the **Firefox** tab.",
          ],
        },
        macos: {
          name: "macOS (Safari, Chrome)",
          fp: ["In **Terminal**:", { cmd: "macHash" }, "The result must match the **SHA-256 (Windows)** line of the handover sheet.", { note: "caseNote" }],
          steps: [
            "Double-click **aibox-ca.cer**: **Keychain Access** opens. Add it to the **System** keychain and confirm with your password.",
            "In Keychain Access open **ViVeSec AI Box CA …** → **Trust** → **When using this certificate: Always Trust**, close the window and confirm.",
            ["Or in Terminal:", { cmd: "macImport" }],
            "Safari and Chrome use it. For Firefox see the **Firefox** tab.",
          ],
        },
        linux: {
          name: "Linux",
          fp: [{ cmd: "linuxHash" }, "The result must match the **SHA-256 (Windows)** line of the handover sheet.", { note: "caseNote" }],
          steps: [
            ["Debian, Ubuntu:", { cmd: "debian" }],
            ["Fedora, RHEL:", { cmd: "fedora" }],
            ["Chrome and Chromium keep their own store (package **libnss3-tools** or **nss-tools**):", { cmd: "nss" }],
            "For Firefox see the **Firefox** tab. Restart the browser afterwards.",
          ],
        },
        android: {
          name: "Android",
          fp: ["After installing: **Settings** → search for **Trusted credentials** → **User** → **ViVeSec AI Box CA**. Compare the **SHA-256** fingerprint with the handover sheet; if it differs, remove the certificate there."],
          steps: [
            "The device needs a screen lock (PIN, pattern or password).",
            "Tap **aibox-ca.cer** above; it is saved to **Downloads**.",
            "**Settings** → **Security** → **Encryption & credentials** → **Install a certificate** → **CA certificate** → **Install anyway** → choose **aibox-ca.cer**. Menu names differ by manufacturer: search Settings for **CA certificate**.",
            "Chrome uses it. Firefox: **Settings** → **About Firefox**, tap the logo five times, then **Settings** → **Secret settings** → **Use third party CA certificates**.",
          ],
        },
        ios: {
          name: "iPhone, iPad",
          fp: ["Before tapping **Install**: open the profile → **More Details** → the certificate, and compare **SHA-256** with the handover sheet."],
          steps: [
            "Open this page in **Safari**, tap **aibox-ca.cer** above, then **Allow**.",
            "**Settings** → **Profile Downloaded** (or **General** → **VPN & Device Management**) → **ViVeSec AI Box CA** → check the fingerprint → **Install**, then enter the passcode.",
            "**Settings** → **General** → **About** → **Certificate Trust Settings** → switch on **ViVeSec AI Box CA …**.",
          ],
        },
        firefox: {
          name: "Firefox",
          fp: ["In the import window choose **View**: the **SHA-256** fingerprint must match the **SHA-256 (Firefox)** line of the handover sheet (with colons)."],
          steps: [
            "**Settings** → **Privacy & Security** → **Certificates** → **View Certificates…** → **Authorities** → **Import…** → choose **aibox-ca.cer**.",
            "Tick **Trust this CA to identify websites** → **OK**. No administrator rights are needed.",
            "On Windows and macOS, Firefox can use the system store instead (after the Windows or macOS tab): open **about:config** and set **security.enterprise_roots.enabled** to **true**.",
            "Restart Firefox.",
          ],
        },
      },
    },

    hu: {
      langName: "Magyar",
      checking: "Kapcsolat ellenőrzése az AI Boxszal…",
      untrusted: "Ez az eszköz még nem bízik az AI Box tanúsítványában.",
      untrustedHint: "Telepítse az alábbiak szerint; az oldal közben folyamatosan ellenőriz.",
      trustedNow: "Kész: a tanúsítvány működik.",
      trustedNowHint: "Az AI Box megnyitása…",
      trustedAlready: "Ez az eszköz már megbízik az AI Box tanúsítványában.",
      onHttps: "A titkosított kapcsolat működik: a tanúsítvány telepítve van ezen az eszközön.",
      open: "Az AI Box megnyitása",
      title: "Az AI Box tanúsítványának telepítése erre az eszközre",
      lead: "Az AI Box saját tanúsítványt használ. Telepítse egyszer erre az eszközre, és a böngésző figyelmeztetés nélkül, titkosított kapcsolaton nyitja meg az AI Boxot.",
      warningTitle: "Telepítés előtt ellenőrizze",
      warningText: "Ez az oldal és a fájl titkosítatlan kapcsolaton érkezik. Csak akkor telepítse a tanúsítványt, ha az SHA-256 ujjlenyomata karakterre pontosan egyezik az átadás-átvételi lapon szereplővel. Ha nem egyezik, törölje a fájlt, és szóljon a rendszergazdának.",
      dlTitle: "A tanúsítvány letöltése",
      dlNote: "A fájl csak a tanúsítvány nyilvános részét tartalmazza. Ezzel az eszköz felismeri az AI Boxot; hozzáférést nem ad.",
      chooseTitle: "Válassza ki ezt az eszközt",
      fpTitle: "Az ujjlenyomat ellenőrzése",
      installTitle: "Telepítés",
      doneTitle: "Kész",
      doneText: "Az oldal néhány másodpercenként ellenőriz, és magától megnyitja az AI Boxot, amint a tanúsítvány működik. Ha nem veszi észre, indítsa újra a böngészőt.",
      admin: "Rendszergazdáknak: Windows-tartományban a tanúsítvány csoportházirenddel minden gépre kiosztható, így senkinek nem kell kézzel telepítenie.",
      copy: "Másolás",
      copied: "Másolva",
      caseNote: "A kis- és nagybetű nem számít.",
      p: {
        windows: {
          name: "Windows (Edge, Chrome)",
          fp: ["Nyissa meg a **PowerShellt**, és futtassa:", { cmd: "winHash" }, "A **Hash** értéknek egyeznie kell az átadás-átvételi lap **SHA-256 (Windows)** sorával."],
          steps: [
            "Kattintson duplán a letöltött **aibox-ca.cer** fájlra, majd: **Tanúsítvány telepítése…**.",
            "Válassza a **Helyi számítógép** lehetőséget (rendszergazdai jog kell), majd **Tovább**.",
            "**Minden tanúsítvány tárolása a következő tárolóban** → **Tallózás…** → **Megbízható legfelső szintű hitelesítésszolgáltatók** → **OK** → **Tovább** → **Befejezés**.",
            ["Vagy rendszergazdaként indított PowerShellben:", { cmd: "winImport" }],
            "Az Edge és a Chrome azonnal használja. Firefoxhoz lásd a **Firefox** fület.",
          ],
        },
        macos: {
          name: "macOS (Safari, Chrome)",
          fp: ["A **Terminálban**:", { cmd: "macHash" }, "Az eredménynek egyeznie kell az átadás-átvételi lap **SHA-256 (Windows)** sorával.", { note: "caseNote" }],
          steps: [
            "Kattintson duplán az **aibox-ca.cer** fájlra: megnyílik a **Kulcskarika-hozzáférés**. Adja a **Rendszer** kulcskarikához, és erősítse meg a jelszavával.",
            "A Kulcskarika-hozzáférésben nyissa meg: **ViVeSec AI Box CA …** → **Megbízhatóság** → **Tanúsítvány használatakor: Mindig megbízható**, zárja be az ablakot, és erősítse meg.",
            ["Vagy a Terminálban:", { cmd: "macImport" }],
            "A Safari és a Chrome ezt használja. Firefoxhoz lásd a **Firefox** fület.",
          ],
        },
        linux: {
          name: "Linux",
          fp: [{ cmd: "linuxHash" }, "Az eredménynek egyeznie kell az átadás-átvételi lap **SHA-256 (Windows)** sorával.", { note: "caseNote" }],
          steps: [
            ["Debian, Ubuntu:", { cmd: "debian" }],
            ["Fedora, RHEL:", { cmd: "fedora" }],
            ["A Chrome és a Chromium saját tárat használ (**libnss3-tools** vagy **nss-tools** csomag):", { cmd: "nss" }],
            "Firefoxhoz lásd a **Firefox** fület. Utána indítsa újra a böngészőt.",
          ],
        },
        android: {
          name: "Android",
          fp: ["Telepítés után: **Beállítások** → keressen rá: **Megbízható hitelesítési adatok** → **Felhasználó** → **ViVeSec AI Box CA**. Vesse össze az **SHA-256** ujjlenyomatot az átadás-átvételi lappal; ha eltér, ott távolítsa el a tanúsítványt."],
          steps: [
            "Az eszközön legyen képernyőzár (PIN-kód, minta vagy jelszó).",
            "Koppintson a fenti **aibox-ca.cer** gombra; a fájl a **Letöltések** mappába kerül.",
            "**Beállítások** → **Biztonság** → **Titkosítás és hitelesítési adatok** → **Tanúsítvány telepítése** → **CA-tanúsítvány** → **Telepítés mindenképp** → válassza az **aibox-ca.cer** fájlt. A menük neve gyártónként eltér: keressen rá a Beállításokban: **CA-tanúsítvány**.",
            "A Chrome ezt használja. Firefox: **Beállítások** → **A Firefox névjegye**, koppintson ötször a logóra, majd **Beállítások** → **Titkos beállítások** → **Harmadik féltől származó CA-tanúsítványok használata**.",
          ],
        },
        ios: {
          name: "iPhone, iPad",
          fp: ["A **Telepítés** előtt: nyissa meg a profilt → **További részletek** → a tanúsítvány, és vesse össze az **SHA-256** értéket az átadás-átvételi lappal."],
          steps: [
            "Nyissa meg ezt az oldalt a **Safariban**, koppintson a fenti **aibox-ca.cer** gombra, majd: **Engedélyezés**.",
            "**Beállítások** → **Profil letöltve** (vagy **Általános** → **VPN és eszközkezelés**) → **ViVeSec AI Box CA** → ellenőrizze az ujjlenyomatot → **Telepítés**, majd adja meg a jelkódot.",
            "**Beállítások** → **Általános** → **Névjegy** → **Tanúsítvány-megbízhatósági beállítások** → kapcsolja be: **ViVeSec AI Box CA …**.",
          ],
        },
        firefox: {
          name: "Firefox",
          fp: ["Az importálási ablakban válassza a **Megtekintés** gombot: az **SHA-256** ujjlenyomatnak egyeznie kell az átadás-átvételi lap **SHA-256 (Firefox)** sorával (kettőspontokkal)."],
          steps: [
            "**Beállítások** → **Adatvédelem és biztonság** → **Tanúsítványok** → **Tanúsítványok megtekintése…** → **Hitelesítésszolgáltatók** → **Importálás…** → válassza az **aibox-ca.cer** fájlt.",
            "Jelölje be: **A CA megbízható webhelyek azonosítására** → **OK**. Rendszergazdai jog nem kell.",
            "Windowson és macOS-en a Firefox a rendszer tárát is használhatja (a Windows vagy a macOS fül után): nyissa meg az **about:config** oldalt, és állítsa a **security.enterprise_roots.enabled** értékét **true**-ra.",
            "Indítsa újra a Firefoxot.",
          ],
        },
      },
    },

    de: {
      langName: "Deutsch",
      checking: "Verbindung zur AI Box wird geprüft…",
      untrusted: "Dieses Gerät vertraut dem Zertifikat der AI Box noch nicht.",
      untrustedHint: "Installieren Sie es wie unten beschrieben; diese Seite prüft dabei laufend weiter.",
      trustedNow: "Fertig: Das Zertifikat funktioniert.",
      trustedNowHint: "Die AI Box wird geöffnet…",
      trustedAlready: "Dieses Gerät vertraut dem Zertifikat der AI Box bereits.",
      onHttps: "Die verschlüsselte Verbindung funktioniert: Das Zertifikat ist auf diesem Gerät installiert.",
      open: "AI Box öffnen",
      title: "Zertifikat der AI Box auf diesem Gerät installieren",
      lead: "Die AI Box verwendet ein eigenes Zertifikat. Installieren Sie es einmal auf diesem Gerät, dann öffnet der Browser die AI Box ohne Warnung über eine verschlüsselte Verbindung.",
      warningTitle: "Vor der Installation prüfen",
      warningText: "Diese Seite und die Datei kommen über eine unverschlüsselte Verbindung. Installieren Sie das Zertifikat nur, wenn sein SHA-256-Fingerabdruck Zeichen für Zeichen mit dem Übergabeprotokoll übereinstimmt. Andernfalls löschen Sie die Datei und informieren Sie den Administrator.",
      dlTitle: "Zertifikat herunterladen",
      dlNote: "Die Datei enthält nur den öffentlichen Teil des Zertifikats. Damit erkennt dieses Gerät die AI Box; Zugriff gewährt sie nicht.",
      chooseTitle: "Dieses Gerät wählen",
      fpTitle: "Fingerabdruck prüfen",
      installTitle: "Installieren",
      doneTitle: "Fertig",
      doneText: "Diese Seite prüft alle paar Sekunden und öffnet die AI Box von selbst, sobald das Zertifikat funktioniert. Falls nicht, starten Sie den Browser neu.",
      admin: "Für Administratoren: In einer Windows-Domäne lässt sich das Zertifikat per Gruppenrichtlinie auf alle Computer verteilen, sodass niemand es von Hand installieren muss.",
      copy: "Kopieren",
      copied: "Kopiert",
      caseNote: "Groß- und Kleinschreibung spielt keine Rolle.",
      p: {
        windows: {
          name: "Windows (Edge, Chrome)",
          fp: ["Öffnen Sie **PowerShell** und führen Sie aus:", { cmd: "winHash" }, "Der Wert **Hash** muss mit der Zeile **SHA-256 (Windows)** des Übergabeprotokolls übereinstimmen."],
          steps: [
            "Doppelklicken Sie auf die heruntergeladene **aibox-ca.cer**, dann **Zertifikat installieren…**.",
            "Wählen Sie **Lokaler Computer** (Administratorrechte nötig), dann **Weiter**.",
            "**Alle Zertifikate in folgendem Speicher speichern** → **Durchsuchen…** → **Vertrauenswürdige Stammzertifizierungsstellen** → **OK** → **Weiter** → **Fertig stellen**.",
            ["Oder in einer als Administrator gestarteten PowerShell:", { cmd: "winImport" }],
            "Edge und Chrome verwenden es sofort. Für Firefox siehe den Reiter **Firefox**.",
          ],
        },
        macos: {
          name: "macOS (Safari, Chrome)",
          fp: ["Im **Terminal**:", { cmd: "macHash" }, "Das Ergebnis muss mit der Zeile **SHA-256 (Windows)** des Übergabeprotokolls übereinstimmen.", { note: "caseNote" }],
          steps: [
            "Doppelklicken Sie auf **aibox-ca.cer**: Die **Schlüsselbundverwaltung** öffnet sich. Fügen Sie es dem Schlüsselbund **System** hinzu und bestätigen Sie mit Ihrem Passwort.",
            "Öffnen Sie in der Schlüsselbundverwaltung **ViVeSec AI Box CA …** → **Vertrauen** → **Bei Verwendung dieses Zertifikats: Immer vertrauen**, schließen Sie das Fenster und bestätigen Sie.",
            ["Oder im Terminal:", { cmd: "macImport" }],
            "Safari und Chrome verwenden es. Für Firefox siehe den Reiter **Firefox**.",
          ],
        },
        linux: {
          name: "Linux",
          fp: [{ cmd: "linuxHash" }, "Das Ergebnis muss mit der Zeile **SHA-256 (Windows)** des Übergabeprotokolls übereinstimmen.", { note: "caseNote" }],
          steps: [
            ["Debian, Ubuntu:", { cmd: "debian" }],
            ["Fedora, RHEL:", { cmd: "fedora" }],
            ["Chrome und Chromium haben einen eigenen Speicher (Paket **libnss3-tools** bzw. **nss-tools**):", { cmd: "nss" }],
            "Für Firefox siehe den Reiter **Firefox**. Starten Sie den Browser danach neu.",
          ],
        },
        android: {
          name: "Android",
          fp: ["Nach der Installation: **Einstellungen** → nach **Vertrauenswürdige Anmeldedaten** suchen → **Nutzer** → **ViVeSec AI Box CA**. Vergleichen Sie den **SHA-256**-Fingerabdruck mit dem Übergabeprotokoll; weicht er ab, entfernen Sie das Zertifikat dort."],
          steps: [
            "Das Gerät braucht eine Displaysperre (PIN, Muster oder Passwort).",
            "Tippen Sie oben auf **aibox-ca.cer**; die Datei landet in **Downloads**.",
            "**Einstellungen** → **Sicherheit** → **Verschlüsselung & Anmeldedaten** → **Zertifikat installieren** → **CA-Zertifikat** → **Trotzdem installieren** → **aibox-ca.cer** wählen. Die Menünamen unterscheiden sich je nach Hersteller: Suchen Sie in den Einstellungen nach **CA-Zertifikat**.",
            "Chrome verwendet es. Firefox: **Einstellungen** → **Über Firefox**, fünfmal auf das Logo tippen, dann **Einstellungen** → **Geheime Einstellungen** → **Zertifikate von Drittanbietern verwenden**.",
          ],
        },
        ios: {
          name: "iPhone, iPad",
          fp: ["Vor **Installieren**: Profil öffnen → **Weitere Details** → das Zertifikat, und **SHA-256** mit dem Übergabeprotokoll vergleichen."],
          steps: [
            "Öffnen Sie diese Seite in **Safari**, tippen Sie oben auf **aibox-ca.cer**, dann **Erlauben**.",
            "**Einstellungen** → **Profil geladen** (oder **Allgemein** → **VPN und Geräteverwaltung**) → **ViVeSec AI Box CA** → Fingerabdruck prüfen → **Installieren**, dann den Code eingeben.",
            "**Einstellungen** → **Allgemein** → **Info** → **Zertifikatsvertrauenseinstellungen** → **ViVeSec AI Box CA …** einschalten.",
          ],
        },
        firefox: {
          name: "Firefox",
          fp: ["Wählen Sie im Importfenster **Ansehen**: Der **SHA-256**-Fingerabdruck muss mit der Zeile **SHA-256 (Firefox)** des Übergabeprotokolls übereinstimmen (mit Doppelpunkten)."],
          steps: [
            "**Einstellungen** → **Datenschutz & Sicherheit** → **Zertifikate** → **Zertifikate anzeigen…** → **Zertifizierungsstellen** → **Importieren…** → **aibox-ca.cer** wählen.",
            "**Dieser CA vertrauen, um Websites zu identifizieren** ankreuzen → **OK**. Administratorrechte sind nicht nötig.",
            "Unter Windows und macOS kann Firefox stattdessen den Systemspeicher verwenden (nach dem Reiter Windows bzw. macOS): **about:config** öffnen und **security.enterprise_roots.enabled** auf **true** setzen.",
            "Starten Sie Firefox neu.",
          ],
        },
      },
    },

    da: {
      langName: "Dansk",
      checking: "Kontrollerer forbindelsen til AI Boxen…",
      untrusted: "Denne enhed har endnu ikke tillid til AI Boxens certifikat.",
      untrustedHint: "Installer det som vist nedenfor; siden bliver ved med at kontrollere.",
      trustedNow: "Færdig: certifikatet virker.",
      trustedNowHint: "AI Boxen åbnes…",
      trustedAlready: "Denne enhed har allerede tillid til AI Boxens certifikat.",
      onHttps: "Den krypterede forbindelse virker: certifikatet er installeret på denne enhed.",
      open: "Åbn AI Boxen",
      title: "Installer AI Boxens certifikat på denne enhed",
      lead: "AI Boxen bruger sit eget certifikat. Installer det én gang på denne enhed, så åbner browseren AI Boxen over en krypteret forbindelse uden advarsel.",
      warningTitle: "Kontrollér før installation",
      warningText: "Denne side og filen kommer over en ukrypteret forbindelse. Installer kun certifikatet, hvis dets SHA-256-fingeraftryk stemmer tegn for tegn med overdragelsesarket. Hvis ikke, så slet filen og giv administratoren besked.",
      dlTitle: "Hent certifikatet",
      dlNote: "Filen indeholder kun den offentlige del af certifikatet. Den lader enheden genkende AI Boxen; den giver ingen adgang.",
      chooseTitle: "Vælg denne enhed",
      fpTitle: "Kontrollér fingeraftrykket",
      installTitle: "Installer",
      doneTitle: "Færdig",
      doneText: "Siden kontrollerer med få sekunders mellemrum og åbner selv AI Boxen, når certifikatet virker. Hvis den ikke opdager det, så genstart browseren.",
      admin: "Til administratorer: i et Windows-domæne kan certifikatet fordeles til alle computere med gruppepolitik, så ingen behøver at installere det manuelt.",
      copy: "Kopiér",
      copied: "Kopieret",
      caseNote: "Store og små bogstaver er ligegyldige.",
      p: {
        windows: {
          name: "Windows (Edge, Chrome)",
          fp: ["Åbn **PowerShell** og kør:", { cmd: "winHash" }, "Værdien **Hash** skal stemme med linjen **SHA-256 (Windows)** på overdragelsesarket."],
          steps: [
            "Dobbeltklik på den hentede **aibox-ca.cer**, og vælg **Installer certifikat…**.",
            "Vælg **Lokal computer** (kræver administratorrettigheder), og derefter **Næste**.",
            "**Placer alle certifikater i nedenstående lager** → **Gennemse…** → **Rodnøglecentre, der er tillid til** → **OK** → **Næste** → **Udfør**.",
            ["Eller i en PowerShell startet som administrator:", { cmd: "winImport" }],
            "Edge og Chrome bruger det med det samme. For Firefox se fanen **Firefox**.",
          ],
        },
        macos: {
          name: "macOS (Safari, Chrome)",
          fp: ["I **Terminal**:", { cmd: "macHash" }, "Resultatet skal stemme med linjen **SHA-256 (Windows)** på overdragelsesarket.", { note: "caseNote" }],
          steps: [
            "Dobbeltklik på **aibox-ca.cer**: **Nøgleringsadgang** åbnes. Føj det til nøgleringen **System**, og bekræft med din adgangskode.",
            "Åbn **ViVeSec AI Box CA …** i Nøgleringsadgang → **Tillid** → **Når dette certifikat bruges: Hav altid tillid**, luk vinduet og bekræft.",
            ["Eller i Terminal:", { cmd: "macImport" }],
            "Safari og Chrome bruger det. For Firefox se fanen **Firefox**.",
          ],
        },
        linux: {
          name: "Linux",
          fp: [{ cmd: "linuxHash" }, "Resultatet skal stemme med linjen **SHA-256 (Windows)** på overdragelsesarket.", { note: "caseNote" }],
          steps: [
            ["Debian, Ubuntu:", { cmd: "debian" }],
            ["Fedora, RHEL:", { cmd: "fedora" }],
            ["Chrome og Chromium har deres eget lager (pakken **libnss3-tools** eller **nss-tools**):", { cmd: "nss" }],
            "For Firefox se fanen **Firefox**. Genstart browseren bagefter.",
          ],
        },
        android: {
          name: "Android",
          fp: ["Efter installationen: **Indstillinger** → søg efter **Pålidelige loginoplysninger** → **Bruger** → **ViVeSec AI Box CA**. Sammenlign **SHA-256**-fingeraftrykket med overdragelsesarket; fjern certifikatet dér, hvis det afviger."],
          steps: [
            "Enheden skal have skærmlås (pinkode, mønster eller adgangskode).",
            "Tryk på **aibox-ca.cer** ovenfor; filen gemmes i **Downloads**.",
            "**Indstillinger** → **Sikkerhed** → **Kryptering og loginoplysninger** → **Installer et certifikat** → **CA-certifikat** → **Installer alligevel** → vælg **aibox-ca.cer**. Menunavnene varierer efter producent: søg efter **CA-certifikat** i Indstillinger.",
            "Chrome bruger det. Firefox: **Indstillinger** → **Om Firefox**, tryk fem gange på logoet, derefter **Indstillinger** → **Hemmelige indstillinger** → **Brug tredjeparts CA-certifikater**.",
          ],
        },
        ios: {
          name: "iPhone, iPad",
          fp: ["Før du trykker på **Installer**: åbn profilen → **Flere detaljer** → certifikatet, og sammenlign **SHA-256** med overdragelsesarket."],
          steps: [
            "Åbn denne side i **Safari**, tryk på **aibox-ca.cer** ovenfor og derefter **Tillad**.",
            "**Indstillinger** → **Profil hentet** (eller **Generelt** → **VPN og enhedsadministration**) → **ViVeSec AI Box CA** → kontrollér fingeraftrykket → **Installer**, og indtast adgangskoden.",
            "**Indstillinger** → **Generelt** → **Om** → **Indstillinger for certifikattillid** → slå **ViVeSec AI Box CA …** til.",
          ],
        },
        firefox: {
          name: "Firefox",
          fp: ["Vælg **Vis** i importvinduet: **SHA-256**-fingeraftrykket skal stemme med linjen **SHA-256 (Firefox)** på overdragelsesarket (med koloner)."],
          steps: [
            "**Indstillinger** → **Privatliv & sikkerhed** → **Certifikater** → **Vis certifikater…** → **Autoriteter** → **Importer…** → vælg **aibox-ca.cer**.",
            "Sæt flueben ved **Stol på denne CA til at identificere websteder** → **OK**. Der kræves ikke administratorrettigheder.",
            "På Windows og macOS kan Firefox i stedet bruge systemets lager (efter fanen Windows eller macOS): åbn **about:config** og sæt **security.enterprise_roots.enabled** til **true**.",
            "Genstart Firefox.",
          ],
        },
      },
    },
  };

  var LANGS = ["en", "hu", "da", "de"];
  var PLATFORMS = ["windows", "macos", "linux", "android", "ios", "firefox"];

  var lang = pickLang();
  var platform = detectPlatform();
  var state = "checking";
  var everFailed = false;
  var pollTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function pickLang() {
    var saved = null;
    try {
      saved = (localStorage.getItem(LANG_KEY) || "").toLowerCase();
    } catch (e) {
      saved = null;
    }
    if (saved && TEXT[saved]) return saved;
    var wanted = navigator.languages || [navigator.language || "en"];
    for (var i = 0; i < wanted.length; i++) {
      var code = String(wanted[i]).slice(0, 2).toLowerCase();
      if (TEXT[code]) return code;
    }
    return "en";
  }

  function detectPlatform() {
    var ua = navigator.userAgent || "";
    var touchMac = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
    if (/iPhone|iPad|iPod/.test(ua) || touchMac) return "ios";
    if (/Android/.test(ua)) return "android";
    if (/Firefox\//.test(ua)) return "firefox";
    if (/Windows/.test(ua)) return "windows";
    if (/Macintosh/.test(ua)) return "macos";
    if (/Linux|CrOS|X11/.test(ua)) return "linux";
    return "windows";
  }

  function t(key) {
    return TEXT[lang][key];
  }

  // "**bold** text" -> text nodes and <strong>, never innerHTML.
  function rich(parent, text) {
    var parts = String(text).split("**");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2) {
        var strong = document.createElement("strong");
        strong.textContent = parts[i];
        parent.appendChild(strong);
      } else {
        parent.appendChild(document.createTextNode(parts[i]));
      }
    }
    return parent;
  }

  function commandBlock(key) {
    var box = document.createElement("div");
    box.className = "cmd";
    var code = document.createElement("code");
    code.textContent = CMD[key];
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = t("copy");
    button.addEventListener("click", function () {
      copyText(code, function () {
        button.textContent = t("copied");
        setTimeout(function () {
          button.textContent = t("copy");
        }, 1500);
      });
    });
    box.appendChild(code);
    box.appendChild(button);
    return box;
  }

  // The page is not a secure context, so navigator.clipboard is missing:
  // select the text and use the older copy command.
  function copyText(node, done) {
    var range = document.createRange();
    range.selectNodeContents(node);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    if (ok) done();
  }

  function renderItem(parent, item) {
    if (typeof item === "string") {
      parent.appendChild(rich(document.createElement("p"), item));
    } else if (item.cmd) {
      parent.appendChild(commandBlock(item.cmd));
    } else if (item.note) {
      var note = rich(document.createElement("p"), t(item.note));
      note.className = "muted";
      parent.appendChild(note);
    } else if (Array.isArray(item)) {
      for (var i = 0; i < item.length; i++) renderItem(parent, item[i]);
    }
  }

  function renderPlatform() {
    var spec = t("p")[platform];
    var fp = $("fp-body");
    fp.textContent = "";
    renderItem(fp, spec.fp);
    var list = $("install-body");
    list.textContent = "";
    for (var i = 0; i < spec.steps.length; i++) {
      var li = document.createElement("li");
      renderItem(li, spec.steps[i]);
      list.appendChild(li);
    }
    var tabs = $("tabs").children;
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].setAttribute("aria-selected", tabs[j].getAttribute("data-platform") === platform ? "true" : "false");
    }
  }

  function renderTabs() {
    var tabs = $("tabs");
    tabs.textContent = "";
    PLATFORMS.forEach(function (key) {
      var button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("data-platform", key);
      button.textContent = t("p")[key].name;
      button.addEventListener("click", function () {
        platform = key;
        renderPlatform();
      });
      tabs.appendChild(button);
    });
  }

  function renderLangs() {
    var nav = $("langs");
    nav.textContent = "";
    LANGS.forEach(function (code) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = code.toUpperCase();
      button.title = TEXT[code].langName;
      button.setAttribute("aria-pressed", code === lang ? "true" : "false");
      button.addEventListener("click", function () {
        lang = code;
        try {
          localStorage.setItem(LANG_KEY, code.toUpperCase());
        } catch (e) {
          // Storage may be blocked; the choice then lasts for this page.
        }
        render();
      });
      nav.appendChild(button);
    });
  }

  var IDS = {
    title: "title",
    lead: "lead",
    "warning-title": "warningTitle",
    "warning-text": "warningText",
    "dl-title": "dlTitle",
    "dl-note": "dlNote",
    "choose-title": "chooseTitle",
    "fp-title": "fpTitle",
    "install-title": "installTitle",
    "done-title": "doneTitle",
    "done-text": "doneText",
    admin: "admin",
    open: "open",
  };

  function renderStatus() {
    var box = $("status");
    var title = $("status-title");
    var text = $("status-text");
    var open = $("open");
    box.className = "status";
    open.classList.add("hidden");
    text.textContent = "";
    if (state === "checking") {
      title.textContent = t("checking");
    } else if (state === "untrusted") {
      box.className = "status bad";
      title.textContent = t("untrusted");
      text.textContent = t("untrustedHint");
    } else {
      box.className = "status ok";
      title.textContent =
        state === "https" ? t("onHttps") : state === "trustedNow" ? t("trustedNow") : t("trustedAlready");
      if (state === "trustedNow") text.textContent = t("trustedNowHint");
      open.classList.remove("hidden");
    }
  }

  function render() {
    document.documentElement.lang = lang;
    Object.keys(IDS).forEach(function (id) {
      $(id).textContent = t(IDS[id]);
    });
    renderLangs();
    renderTabs();
    renderPlatform();
    renderStatus();
  }

  function httpsBase() {
    return "https://" + location.hostname + "/";
  }

  // An https request to this host succeeds only if the browser trusts the box
  // CA; with an unknown CA the fetch fails with a network error. no-cors: the
  // answer itself is never read.
  function probe() {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (controller) controller.abort();
    }, PROBE_TIMEOUT_MS);
    var url = httpsBase() + "aibox-probe?t=" + Date.now();
    return fetch(url, { mode: "no-cors", cache: "no-store", credentials: "omit", signal: controller ? controller.signal : undefined })
      .then(function () {
        return true;
      })
      .catch(function () {
        return false;
      })
      .then(function (ok) {
        clearTimeout(timer);
        return ok;
      });
  }

  function onRoot() {
    return location.pathname === "/" || location.pathname === "";
  }

  function check() {
    pollTimer = null;
    probe().then(function (ok) {
      if (ok) {
        trusted();
      } else {
        everFailed = true;
        state = "untrusted";
        document.body.classList.remove("checking");
        renderStatus();
        schedule();
      }
    });
  }

  function trusted() {
    // The root address continues straight to the AI Box; /cert stays, so it
    // can be used to check a device, unless the certificate was just installed.
    if (onRoot() && !everFailed) {
      location.replace(httpsBase());
      return;
    }
    state = everFailed ? "trustedNow" : "trustedAlready";
    document.body.classList.remove("checking");
    renderStatus();
    if (everFailed) {
      setTimeout(function () {
        location.replace(httpsBase());
      }, 1500);
    }
  }

  function schedule() {
    if (pollTimer === null && state === "untrusted") pollTimer = setTimeout(check, POLL_MS);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && state === "untrusted") {
      if (pollTimer !== null) clearTimeout(pollTimer);
      check();
    }
  });

  $("open").setAttribute("href", location.protocol === "https:" ? "/" : httpsBase());
  document.body.classList.remove("nojs");
  if (location.protocol === "https:") {
    state = "https";
    render();
  } else {
    if (onRoot()) document.body.classList.add("checking");
    render();
    check();
  }
})();
