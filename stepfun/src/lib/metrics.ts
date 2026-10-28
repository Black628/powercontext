export type MetricStage =
  | "model-load"
  | "file-read"
  | "chunk"
  | "document-embedding"
  | "database-write"
  | "index-refresh"
  | "query-embedding"
  | "hybrid-search";

export interface PerformanceMetric {
  id: string;
  stage: MetricStage;
  label: string;
  durationMs: number;
  recordedAt: string;
  details?: Record<string, string | number | boolean>;
}

export interface MetricSummary {
  totalMs: number;
  byStage: Partial<Record<MetricStage, number>>;
}

export function createMetric(
  stage: MetricStage,
  label: string,
  durationMs: number,
  details?: PerformanceMetric["details"],
): PerformanceMetric {
  return {
    id: crypto.randomUUID(),
    stage,
    label,
    durationMs: Math.max(0, durationMs),
    recordedAt: new Date().toISOString(),
    details,
  };
}

export function summarizeMetrics(metrics: PerformanceMetric[]): MetricSummary {
  const byStage: MetricSummary["byStage"] = {};
  let totalMs = 0;

  for (const metric of metrics) {
    totalMs += metric.durationMs;
    byStage[metric.stage] = (byStage[metric.stage] ?? 0) + metric.durationMs;
  }

  return { totalMs, byStage };
}
