// The ONE place the worker may write to a console (tests/unit/parity.test.mjs forbids console.* everywhere else).
// It exists so a stuck toolbar click can be diagnosed from the worker's console (Safari: Develop > Web Extension Background
// Content): only booleans, numbers and short plain words are printed; anything that could be a credential, a URL or page text is
// dropped, so a breadcrumb can never leak what the rest of the code works so hard to protect.

const SAFE_WORD = /^[A-Za-z0-9 ._:-]{0,60}$/;
const SECRETISH = /mrd1|bearer|token|secret|password|proof/i;

export function trace(label: string, data: Record<string, boolean | number | string | null | undefined> = {}): void {
  try {
    const clean: Record<string, boolean | number | string> = {};
    for (const [key, value] of Object.entries(data)) {
      if (typeof value === "boolean" || typeof value === "number") clean[key] = value;
      else if (typeof value === "string" && SAFE_WORD.test(value) && !SECRETISH.test(value)) clean[key] = value;
    }
    console.info(`[meridian] ${label} ${Object.entries(clean).map(([k, v]) => `${k}=${v}`).join(" ")}`);
  } catch {
    /* tracing never fails the caller */
  }
}
