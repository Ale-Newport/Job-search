import test from "node:test";
import assert from "node:assert/strict";
import { trace } from "../../src/shared/trace.ts";

test("trace prints only booleans, numbers and short plain words, never anything credential-like or URL-like", () => {
  const lines = [];
  const original = console.info;
  console.info = (...args) => lines.push(args);
  try {
    trace("probe", {
      ok: true, frames: 2, host: "jobs.example.com", reason: "permission",
      token: "mrd1.abc.def", url: "https://x.test/path?secret=1", long: "x".repeat(200), bearer: "Bearer abc", nothing: undefined,
    });
  } finally {
    console.info = original;
  }
  assert.equal(lines.length, 1);
  assert.equal(lines[0][0], "[meridian] probe ok=true frames=2 host=jobs.example.com reason=permission");
});
