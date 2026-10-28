import { test } from "node:test";
import assert from "node:assert/strict";
import { clamp } from "../src/math.js";

test("clamp keeps values inside the range", () => {
  assert.equal(clamp(5, 0, 3), 3);
  assert.equal(clamp(-1, 0, 3), 0);
  assert.equal(clamp(2, 0, 3), 2);
});

test("clamp rejects an inverted range", () => {
  assert.throws(() => clamp(1, 3, 0), RangeError);
});
