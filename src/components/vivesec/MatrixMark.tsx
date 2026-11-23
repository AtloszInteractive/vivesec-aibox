import { MATRIX_NAME } from "./brand";

/** The product's name in place of the ViVeSec logo, which the ViVeSec app
 *  around the Matrix build already shows. */
export function MatrixMark() {
  return (
    <span className="select-none whitespace-nowrap text-lg font-semibold tracking-[0.18em] text-ink">
      {MATRIX_NAME}
    </span>
  );
}
