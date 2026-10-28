export const EMBEDDING_DIMENSION = 1024;
export const MODEL_ID = "qwen3.7-text-embedding";
export const EMBEDDING_PROVIDER = "qwen-api";
export const MAX_BATCH_SIZE = 64;
export const COLLECTION_NAME_PATTERN = /^[a-z][a-z0-9_]{2,47}$/;

export type EmbeddingBackend = typeof EMBEDDING_PROVIDER;

export interface CollectionSummary {
  name: string;
  count: number;
  dimension: number;
  distance: string;
}

export interface DocumentRecord {
  id: string;
  document: string;
  embedding: number[];
  metadata: Record<string, string | number | boolean>;
}

export interface ImportResponse {
  collection: string;
  upserted: number;
  total: number;
  writeMs: number;
  refreshMs: number;
}

export interface EmbeddingResponse {
  model: string;
  dimension: number;
  vectors: number[][];
  inferenceMs: number;
  backend: EmbeddingBackend;
}

export interface SearchResultItem {
  id: string;
  document: string;
  metadata: Record<string, unknown> | null;
  distance: number | null;
}

export interface SearchResponse {
  collection: string;
  databaseMs: number;
  results: SearchResultItem[];
}

export interface HealthResponse {
  ok: boolean;
  mode: "server";
  target: string;
  database: string;
  embedding: {
    ok: boolean;
    provider: EmbeddingBackend;
    model: string;
    dimension: number;
    message?: string;
  };
  message?: string;
}
