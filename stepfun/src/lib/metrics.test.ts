import { describe, expect, it } from "vitest";
import type { PerformanceMetric } from "./metrics";
import { summarizeMetrics } from "./metrics";

describe("summarizeMetrics", () => {
  it("totals repeated stages", () => {
    const metrics = [
      { stage: "document-embedding", durationMs: 10 },
      { stage: "document-embedding", durationMs: 15 },
      { stage: "database-write", durationMs: 5 },
    ] as PerformanceMetric[];

    expect(summarizeMetrics(metrics)).toEqual({
      totalMs: 30,
      byStage: {
        "document-embedding": 25,
        "database-write": 5,
      },
    });
  });
});
