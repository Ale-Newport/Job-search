// Preload for `node --test`: lets the tests import the TypeScript sources directly (Node strips the types; the hook
// below resolves the extension-less relative imports that esbuild's bundler resolution allows) and defines the build-time
// globals that esbuild would substitute. Nothing here ships.
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

globalThis.__E2E__ = false;
globalThis.__SAFARI__ = false;
globalThis.__SHADOW__ = "closed";

registerHooks({
  /** `./x` and `../x` -> x.ts or x/index.ts, the way a bundler resolves them. */
  resolve(specifier, context, nextResolve) {
    const relative = specifier.startsWith("./") || specifier.startsWith("../");
    if (relative && context.parentURL && !/\.[cm]?[jt]s$|\.json$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL);
      for (const suffix of [".ts", "/index.ts"]) {
        const candidate = new URL(base.href + suffix);
        if (existsSync(fileURLToPath(candidate))) return nextResolve(candidate.href, context);
      }
    }
    return nextResolve(specifier, context);
  },
});
