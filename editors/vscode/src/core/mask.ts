// The template as an HTML tool should see it: every `{% %}`, `{{ }}` and `{# #}`
// blanked out with spaces of the same length. Offsets and lines stay where they
// were, so a position in one text is the same position in the other.
import type { Token } from "./scan.ts";

export function maskDjango(text: string, tokens: Token[]): string {
  // UTF-16 units, as offsets count them: an emoji in a tag becomes two spaces
  const units = text.split("");
  for (const token of tokens) {
    // What `{% verbatim %}` holds is output as it is: HTML like any other
    if (token.kind === "comment" && token.region === "verbatim") continue;
    for (let i = token.start; i < token.end; i++) {
      if (units[i] !== "\n" && units[i] !== "\r") units[i] = " ";
    }
  }
  return units.join("");
}
