import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { FileBadge, Printer } from "lucide-react";

import { firefoxForm, readCaCertificate, type CaCertificate } from "@/lib/auth/ca-certificate";
import { qrModules } from "@/lib/qr";
import { QrCode } from "../AuthPages";
import { LOGO_ON_LIGHT } from "../brand";
import { useLang } from "../i18n";
import { Button, Card, formatTime } from "./ui";

type Reading =
  | { kind: "loading" }
  | { kind: "unavailable" }
  | { kind: "ready"; ca: CaCertificate; names: string[]; addresses: string[] };

/** The CA and the box addresses, read over the authenticated connection of
 *  this page (HTTPS through the front, or loopback): the CA file from the
 *  front, the configured names from its handover list. */
async function readHandover(): Promise<Reading> {
  if (typeof window === "undefined" || !window.isSecureContext) {
    return { kind: "unavailable" };
  }
  const response = await fetch("/aibox-ca.cer", { cache: "no-store", credentials: "omit" });
  if (!response.ok) return { kind: "unavailable" };
  const ca = await readCaCertificate(await response.arrayBuffer());
  let names = [window.location.hostname];
  let addresses: string[] = [];
  try {
    const list = await fetch("/aibox-handover.json", { cache: "no-store", credentials: "omit" });
    if (list.ok) {
      const body = (await list.json()) as { names?: unknown; addresses?: unknown };
      const strings = (value: unknown) =>
        Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
      if (strings(body.names).length) names = strings(body.names);
      addresses = strings(body.addresses);
    }
  } catch {
    // An older front without the list: the name this page was opened with.
  }
  return { kind: "ready", ca, names, addresses };
}

const installUrl = (host: string) => `http://${host}/cert`;

/** Phones rarely resolve the box name, so the QR code carries an address when there is one. */
const qrTarget = (names: string[], addresses: string[]) => installUrl(addresses[0] ?? names[0]);

function printSheet() {
  const body = document.body;
  body.classList.add("vvs-printing");
  window.addEventListener("afterprint", () => body.classList.remove("vvs-printing"), {
    once: true,
  });
  window.print();
}

function Field({
  label,
  children,
  mono,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-ink/5 py-1.5 text-xs last:border-0">
      <span className="text-ink/55">{label}</span>
      <span className={`text-right text-ink/90 ${mono ? "break-all font-mono" : ""}`}>
        {children}
      </span>
    </div>
  );
}

export function ClientCertificateCard() {
  const { tm, lang } = useLang();
  const [reading, setReading] = useState<Reading>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    readHandover()
      .catch((): Reading => ({ kind: "unavailable" }))
      .then((result) => {
        if (live) setReading(result);
      });
    return () => {
      live = false;
    };
  }, []);

  const qr = useMemo(
    () => (reading.kind === "ready" ? qrModules(qrTarget(reading.names, reading.addresses)) : null),
    [reading],
  );

  return (
    <Card
      title={tm("Certificate for the client devices")}
      icon={<FileBadge className="h-4 w-4" />}
      actions={
        reading.kind === "ready" ? (
          <Button onClick={printSheet}>
            <Printer className="h-3.5 w-3.5" />
            {tm("Print handover sheet")}
          </Button>
        ) : undefined
      }
    >
      {reading.kind === "loading" && <p className="text-xs text-ink/55">…</p>}
      {reading.kind === "unavailable" && (
        <p className="text-xs text-ink/60">
          {tm(
            "The certificate cannot be read here: the page is not open through the HTTPS front of the AI Box.",
          )}
        </p>
      )}
      {reading.kind === "ready" && qr && (
        <>
          <div className="flex flex-wrap items-start gap-4">
            <div className="min-w-0 flex-1">
              <Field label={tm("Installation page")}>
                {[...reading.names, ...reading.addresses].map((host) => (
                  <span key={host} className="block">
                    {installUrl(host)}
                  </span>
                ))}
              </Field>
              <Field label={tm("CA certificate")}>{reading.ca.commonName || "—"}</Field>
              <Field label={tm("Valid until")}>
                {reading.ca.notAfter ? formatTime(reading.ca.notAfter.getTime() / 1000, lang) : "—"}
              </Field>
              <Field label={tm("SHA-256 fingerprint (Windows form)")} mono>
                {reading.ca.sha256}
              </Field>
              <Field label={tm("SHA-256 fingerprint (Firefox form)")} mono>
                {firefoxForm(reading.ca.sha256)}
              </Field>
            </div>
            <div className="rounded-lg bg-white p-1">
              <QrCode rows={qr} label={tm("QR code of the installation page")} size={112} />
            </div>
          </div>
          <p className="mt-2 text-[11px] text-ink/50">
            {tm(
              "The fingerprint is computed in this browser from the certificate received over the encrypted connection. For the very first sheet, compare it with the output of tls-pki.sh show on the AI Box.",
            )}
          </p>
          {createPortal(
            <HandoverSheet
              ca={reading.ca}
              names={reading.names}
              addresses={reading.addresses}
              qr={qr}
            />,
            document.body,
          )}
        </>
      )}
    </Card>
  );
}

/** The printed page: black on white whatever the theme, shown only while printing. */
function HandoverSheet({
  ca,
  names,
  addresses,
  qr,
}: {
  ca: CaCertificate;
  names: string[];
  addresses: string[];
  qr: string[];
}) {
  const { tm, lang } = useLang();
  const row = (label: string, value: React.ReactNode, mono = false) => (
    <tr>
      <th className="w-[38%] border-b border-black/20 py-2 pr-3 text-left align-top font-normal text-black/70">
        {label}
      </th>
      <td
        className={`border-b border-black/20 py-2 align-top ${mono ? "break-all font-mono text-[12.5px]" : ""}`}
      >
        {value}
      </td>
    </tr>
  );
  return (
    <div className="vvs-print-sheet bg-white p-8 text-[13px] leading-snug text-black">
      <img src={LOGO_ON_LIGHT} alt="ViVeSec" width={177} height={51} />
      <h1 className="mb-4 mt-4 text-lg font-bold">
        {tm("Handover sheet: AI Box certificate for the client devices")}
      </h1>
      <table className="w-full border-collapse">
        <tbody>
          {row(tm("AI Box names"), names.join(", "))}
          {row(tm("AI Box addresses"), addresses.length ? addresses.join(", ") : "—")}
          {row(
            tm("Installation page"),
            [...names, ...addresses].map((host) => (
              <span key={host} className="block font-semibold">
                {installUrl(host)}
              </span>
            )),
          )}
          {row(tm("CA certificate"), ca.commonName || "—")}
          {row(
            tm("Valid until"),
            ca.notAfter ? formatTime(ca.notAfter.getTime() / 1000, lang) : "—",
          )}
          {row(tm("SHA-256 fingerprint (Windows form)"), ca.sha256, true)}
          {row(tm("SHA-256 fingerprint (Firefox form)"), firefoxForm(ca.sha256), true)}
        </tbody>
      </table>
      <div className="mt-6 flex items-start gap-6">
        <div className="text-center">
          <QrCode rows={qr} label={tm("QR code of the installation page")} size={150} />
          <p className="mt-1 max-w-[160px] text-[11px] text-black/70">
            {tm("Scan with a phone on the AI Box network")}
            <br />
            {qrTarget(names, addresses)}
          </p>
        </div>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            {tm(
              "Open the installation page with http:// (not https://), on the device to be set up.",
            )}
          </li>
          <li>
            {tm(
              "Download the certificate and compare its SHA-256 fingerprint with this sheet character by character. Install it only if they match.",
            )}
          </li>
          <li>
            {tm(
              "Follow the steps shown for the device. In a Windows domain the administrator can deploy the certificate with Group Policy instead.",
            )}
          </li>
        </ol>
      </div>
      <p className="mt-6 border-t border-black/20 pt-2 text-[11px] text-black/70">
        {tm("Keep this sheet: every device checks the downloaded certificate against it.")}{" "}
        {tm("Printed")}: {formatTime(Date.now() / 1000, lang)}
      </p>
    </div>
  );
}
