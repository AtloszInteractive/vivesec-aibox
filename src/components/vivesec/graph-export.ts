// The relationship picture as a file (PNG or SVG), drawn in the browser after
// the box logged the export and gave its watermark (adapter /ui/graph/image/log).

const STYLE_PROPS = [
  "fill",
  "fill-opacity",
  "stroke",
  "stroke-width",
  "stroke-dasharray",
  "stroke-opacity",
  "opacity",
  "font-family",
  "font-size",
  "font-weight",
  "paint-order",
  "color",
];

/** Copy what the stylesheet and CSS variables decide into the clone, so the
 *  file looks the same outside the page. */
function inlineStyles(source: Element, target: Element) {
  const computed = window.getComputedStyle(source);
  const style = (target as SVGElement).style;
  if (style)
    for (const prop of STYLE_PROPS) style.setProperty(prop, computed.getPropertyValue(prop));
  const from = source.children;
  const to = target.children;
  for (let i = 0; i < from.length && i < to.length; i++) inlineStyles(from[i], to[i]);
}

export type Frame = { minX: number; minY: number; w: number; h: number };

/** The picture with its background and the watermark lines along the bottom. */
export function pictureSvg(
  svg: SVGSVGElement,
  frame: Frame,
  unit: number,
  background: string,
  lines: string[],
): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  // The page's own placement of the picture stays on the page: a WebGL
  // snapshot is drawn hidden there, and a file that kept `visibility: hidden`
  // would hold the whole picture and show nothing.
  clone.removeAttribute("style");
  inlineStyles(svg, clone);
  const ns = "http://www.w3.org/2000/svg";
  const band = (lines.length * 14 + 12) * unit;
  const height = frame.h + band;
  clone.setAttribute("xmlns", ns);
  clone.setAttribute("viewBox", `${frame.minX} ${frame.minY} ${frame.w} ${height}`);
  clone.setAttribute("width", String(Math.round(frame.w / unit)));
  clone.setAttribute("height", String(Math.round(height / unit)));
  clone.removeAttribute("class");
  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("x", String(frame.minX));
  bg.setAttribute("y", String(frame.minY));
  bg.setAttribute("width", String(frame.w));
  bg.setAttribute("height", String(height));
  bg.setAttribute("fill", background);
  clone.insertBefore(bg, clone.firstChild);
  lines.forEach((line, i) => {
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", String(frame.minX + 10 * unit));
    text.setAttribute("y", String(frame.minY + frame.h + (i * 14 + 16) * unit));
    text.setAttribute("font-size", String(11 * unit));
    text.setAttribute("font-family", "system-ui, sans-serif");
    text.setAttribute("fill", window.getComputedStyle(svg).getPropertyValue("color") || "#888");
    text.textContent = line;
    clone.appendChild(text);
  });
  return new XMLSerializer().serializeToString(clone);
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadPicture(markup: string, format: "png" | "svg", name: string) {
  const blob = new Blob([markup], { type: "image/svg+xml;charset=utf-8" });
  if (format === "svg") {
    save(blob, `${name}.svg`);
    return;
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("picture"));
      image.src = url;
    });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = image.width * scale;
    canvas.height = image.height * scale;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("canvas");
    context.scale(scale, scale);
    context.drawImage(image, 0, 0);
    const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!png) throw new Error("png");
    save(png, `${name}.png`);
  } finally {
    URL.revokeObjectURL(url);
  }
}
