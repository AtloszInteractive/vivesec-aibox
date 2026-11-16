/**
 * The box CA certificate as the administration page receives it over HTTPS
 * (/aibox-ca.cer, DER): its SHA-256 fingerprint in the two forms the client
 * guide uses, its common name and its expiry. Only the few DER structures of an
 * X.509 certificate that these need are read.
 */

export type CaCertificate = {
  /** SHA-256 of the DER file, 64 upper-case hex digits (Get-FileHash form). */
  sha256: string;
  /** Common name of the subject, "" when absent. */
  commonName: string;
  /** End of validity, null when it cannot be read. */
  notAfter: Date | null;
};

type Tlv = { tag: number; start: number; end: number; next: number };

function tlv(der: Uint8Array, offset: number): Tlv {
  if (offset + 2 > der.length) throw new Error("truncated DER");
  const tag = der[offset];
  let length = der[offset + 1];
  let start = offset + 2;
  if (length & 0x80) {
    const bytes = length & 0x7f;
    if (bytes < 1 || bytes > 4 || start + bytes > der.length) throw new Error("bad DER length");
    length = 0;
    for (let i = 0; i < bytes; i++) length = length * 256 + der[start + i];
    start += bytes;
  }
  const end = start + length;
  if (end > der.length) throw new Error("truncated DER");
  return { tag, start, end, next: end };
}

function children(der: Uint8Array, parent: Tlv): Tlv[] {
  const out: Tlv[] = [];
  for (let offset = parent.start; offset < parent.end; ) {
    const item = tlv(der, offset);
    out.push(item);
    offset = item.next;
  }
  return out;
}

const text = (der: Uint8Array, item: Tlv) =>
  new TextDecoder().decode(der.subarray(item.start, item.end));

function time(der: Uint8Array, item: Tlv): Date | null {
  const value = text(der, item);
  // UTCTime YYMMDDHHMMSSZ (years 1950-2049) or GeneralizedTime YYYYMMDDHHMMSSZ.
  const match =
    item.tag === 0x17
      ? /^(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})Z$/.exec(value)
      : item.tag === 0x18
        ? /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})Z$/.exec(value)
        : null;
  if (!match) return null;
  let year = Number(match[1]);
  if (item.tag === 0x17) year += year < 50 ? 2000 : 1900;
  const [month, day, hour, minute, second] = match.slice(2).map(Number);
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second));
}

const CN_OID = [0x55, 0x04, 0x03];

function commonName(der: Uint8Array, name: Tlv): string {
  for (const rdn of children(der, name)) {
    for (const pair of children(der, rdn)) {
      const [oid, value] = children(der, pair);
      if (
        oid?.tag === 0x06 &&
        value &&
        oid.end - oid.start === CN_OID.length &&
        CN_OID.every((byte, i) => der[oid.start + i] === byte)
      ) {
        return text(der, value);
      }
    }
  }
  return "";
}

/** Common name and expiry; throws on a file that is not a DER certificate. */
export function parseCertificate(der: Uint8Array): Omit<CaCertificate, "sha256"> {
  const certificate = tlv(der, 0);
  if (certificate.tag !== 0x30) throw new Error("not a DER certificate");
  const tbs = children(der, certificate)[0];
  if (!tbs || tbs.tag !== 0x30) throw new Error("not a DER certificate");
  const fields = children(der, tbs);
  // [0] version is optional; then serial, signature, issuer, validity, subject.
  const base = fields[0]?.tag === 0xa0 ? 1 : 0;
  const validity = fields[base + 3];
  const subject = fields[base + 4];
  if (!validity || validity.tag !== 0x30 || !subject || subject.tag !== 0x30) {
    throw new Error("not a DER certificate");
  }
  const times = children(der, validity);
  return {
    commonName: commonName(der, subject),
    notAfter: times[1] ? time(der, times[1]) : null,
  };
}

export async function readCaCertificate(der: ArrayBuffer): Promise<CaCertificate> {
  const bytes = new Uint8Array(der);
  const parsed = parseCertificate(bytes);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
  return { sha256, ...parsed };
}

/** The colon-separated form Firefox and `tls-pki.sh show` print. */
export const firefoxForm = (sha256: string) => sha256.match(/../g)?.join(":") ?? sha256;
