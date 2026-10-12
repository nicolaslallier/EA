import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// A third party's text (a ticket title, a page name) is only ever interpolated as text.
// `v-html` anywhere in the SPA would turn it into markup (docs/adr/0073).
// The one exception shows a document the server rendered to safe HTML (docs/adr/0062).
const RENDERED_BY_THE_SERVER = join(
  "src",
  "features",
  "view",
  "ViewElement.vue",
);
function vueFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      return vueFiles(path);
    }
    return path.endsWith(".vue") ? [path] : [];
  });
}

describe("the SPA", () => {
  it("never uses v-html", () => {
    const offenders = vueFiles("src")
      .filter((file) => file !== RENDERED_BY_THE_SERVER)
      .filter((file) =>
        /\sv-html[\s=>]|\.innerHTML\s*=/.test(readFileSync(file, "utf8")),
      );
    expect(offenders).toEqual([]);
  });
});
