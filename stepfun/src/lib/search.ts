import type { SearchResponse, SearchResultItem } from "../../shared/contracts";

interface RawQueryResult {
  ids: readonly (readonly string[])[];
  documents?: readonly (readonly (string | null)[])[];
  metadatas?: readonly (readonly (Record<string, unknown> | null)[])[];
  distances?: readonly (readonly (number | null)[])[];
}

export function mapQueryResult(result: RawQueryResult): SearchResultItem[] {
  const ids = result.ids[0] ?? [];
  const documents = result.documents?.[0] ?? [];
  const metadatas = result.metadatas?.[0] ?? [];
  const distances = result.distances?.[0] ?? [];

  return ids.map((id, index) => ({
    id,
    document: documents[index] ?? "",
    metadata: metadatas[index] ?? null,
    distance: distances[index] ?? null,
  }));
}

export function isSearchResponse(value: unknown): value is SearchResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<SearchResponse>;
  return (
    typeof candidate.collection === "string" &&
    typeof candidate.databaseMs === "number" &&
    Array.isArray(candidate.results)
  );
}
