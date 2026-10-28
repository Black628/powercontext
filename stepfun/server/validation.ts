import {
  COLLECTION_NAME_PATTERN,
  EMBEDDING_DIMENSION,
  MAX_BATCH_SIZE,
  type DocumentRecord,
} from "../shared/contracts";

function validateMetadata(
  value: Record<string, unknown>,
  recordIndex: number,
): DocumentRecord["metadata"] {
  const metadata: DocumentRecord["metadata"] = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      typeof item !== "string" &&
      typeof item !== "number" &&
      typeof item !== "boolean"
    ) {
      throw new Error(`records[${recordIndex}].metadata.${key} must be a primitive value`);
    }
    metadata[key] = item;
  }
  return metadata;
}

export function validateCollectionName(value: unknown): string {
  if (typeof value !== "string" || !COLLECTION_NAME_PATTERN.test(value)) {
    throw new Error(
      "Collection name must be 3-48 characters: lowercase letters, numbers, underscores, starting with a letter",
    );
  }
  return value;
}

export function validateEmbedding(value: unknown): number[] {
  if (
    !Array.isArray(value) ||
    value.length !== EMBEDDING_DIMENSION ||
    value.some((item) => typeof item !== "number" || !Number.isFinite(item))
  ) {
    throw new Error(`Embedding must contain ${EMBEDDING_DIMENSION} finite numbers`);
  }
  return value;
}

export function validateRecords(value: unknown): DocumentRecord[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_BATCH_SIZE) {
    throw new Error(`records must contain 1-${MAX_BATCH_SIZE} items`);
  }

  return value.map((item, index) => {
    if (!item || typeof item !== "object") {
      throw new Error(`records[${index}] must be an object`);
    }
    const candidate = item as Partial<DocumentRecord>;
    if (
      typeof candidate.id !== "string" ||
      candidate.id.length === 0 ||
      candidate.id.length > 255
    ) {
      throw new Error(`records[${index}].id must contain 1-255 characters`);
    }
    if (
      typeof candidate.document !== "string" ||
      candidate.document.trim().length === 0 ||
      candidate.document.length > 20_000
    ) {
      throw new Error(`records[${index}].document must contain 1-20000 characters`);
    }
    if (
      !candidate.metadata ||
      typeof candidate.metadata !== "object" ||
      Array.isArray(candidate.metadata)
    ) {
      throw new Error(`records[${index}].metadata must be an object`);
    }

    return {
      id: candidate.id,
      document: candidate.document,
      embedding: validateEmbedding(candidate.embedding),
      metadata: validateMetadata(candidate.metadata, index),
    };
  });
}

export function validateEmbeddingTexts(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_BATCH_SIZE) {
    throw new Error(`texts must contain 1-${MAX_BATCH_SIZE} items`);
  }
  return value.map((item, index) => {
    if (typeof item !== "string" || item.trim().length === 0 || item.length > 20_000) {
      throw new Error(`texts[${index}] must contain 1-20000 characters`);
    }
    return item;
  });
}

export function validateSearchBody(value: unknown): {
  query: string;
  embedding: number[];
  nResults: number;
} {
  if (!value || typeof value !== "object") {
    throw new Error("Search body must be an object");
  }
  const candidate = value as Record<string, unknown>;
  const query = typeof candidate.query === "string" ? candidate.query.trim() : "";
  if (!query || query.length > 1000) {
    throw new Error("query must contain 1-1000 characters");
  }
  const requested = Number(candidate.nResults ?? 10);
  if (!Number.isInteger(requested) || requested < 1 || requested > 50) {
    throw new Error("nResults must be an integer between 1 and 50");
  }
  return {
    query,
    embedding: validateEmbedding(candidate.embedding),
    nResults: requested,
  };
}
