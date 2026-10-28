import { describe, expect, it } from "vitest";
import { mapQueryResult } from "./search";

describe("mapQueryResult", () => {
  it("maps the first seekdb query batch by rank", () => {
    expect(
      mapQueryResult({
        ids: [["a", "b"]],
        documents: [["alpha", "beta"]],
        metadatas: [[{ source: "a.md" }, null]],
        distances: [[0.1, null]],
      }),
    ).toEqual([
      {
        id: "a",
        document: "alpha",
        metadata: { source: "a.md" },
        distance: 0.1,
      },
      {
        id: "b",
        document: "beta",
        metadata: null,
        distance: null,
      },
    ]);
  });
});
