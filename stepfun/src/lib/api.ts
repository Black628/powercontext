import type {
  CollectionSummary,
  DocumentRecord,
  EmbeddingResponse,
  HealthResponse,
  ImportResponse,
  SearchResponse,
} from "../../shared/contracts";

interface ApiErrorPayload {
  error?: string;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload;
    throw new Error(payload.error ?? `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  embed: (texts: string[]) =>
    request<EmbeddingResponse>("/api/embeddings", {
      method: "POST",
      body: JSON.stringify({ texts }),
    }),
  collections: () => request<{ collections: CollectionSummary[] }>("/api/collections"),
  createCollection: (name: string) =>
    request<{ collection: CollectionSummary }>("/api/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteCollection: (name: string) =>
    request<{ deleted: string }>(`/api/collections/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  importDocuments: (name: string, records: DocumentRecord[]) =>
    request<ImportResponse>(`/api/collections/${encodeURIComponent(name)}/documents`, {
      method: "POST",
      body: JSON.stringify({ records }),
    }),
  refreshIndex: (name: string) =>
    request<{ collection: string; refreshMs: number }>(
      `/api/collections/${encodeURIComponent(name)}/refresh`,
      { method: "POST", body: "{}" },
    ),
  search: (name: string, query: string, embedding: number[], nResults: number) =>
    request<SearchResponse>(`/api/collections/${encodeURIComponent(name)}/search`, {
      method: "POST",
      body: JSON.stringify({ query, embedding, nResults }),
    }),
};
