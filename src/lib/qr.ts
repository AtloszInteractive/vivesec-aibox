import qrcode from "qrcode-generator";

/**
 * The QR code of a text as rows of "0"/"1" modules, without the quiet zone.
 * The browser draws it as SVG rectangles, so no image data URL and no markup
 * string ever needs to be injected into the page.
 */
export function qrModules(text: string): string[] {
  // The default encoder keeps only the low byte of each character; the payloads
  // (otpauth:// URI, box addresses) are ASCII, so anything else is a bug.
  if (!/^[\x20-\x7e]+$/.test(text)) throw new Error("QR payload must be printable ASCII");
  const code = qrcode(0, "M");
  code.addData(text, "Byte");
  code.make();
  const size = code.getModuleCount();
  const rows: string[] = [];
  for (let row = 0; row < size; row++) {
    let line = "";
    for (let col = 0; col < size; col++) line += code.isDark(row, col) ? "1" : "0";
    rows.push(line);
  }
  return rows;
}
