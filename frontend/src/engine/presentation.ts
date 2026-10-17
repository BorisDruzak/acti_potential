/** Human-facing configuration labels; document contents and identifiers stay intact. */
export function displayConfigText(text: string): string {
  if (!/\bACTI[\s_-]*v\s*2\b/iu.test(text)) return text;
  return text.replace(/\bACTI[\s_-]*v\s*2\b/giu, "").replace(/\s+/gu, " ").replace(/^[\s—–:,-]+|[\s—–:,-]+$/gu, "").trim() || "Документы";
}
