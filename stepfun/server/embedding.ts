import process from "node:process";
import {
  EMBEDDING_DIMENSION,
  EMBEDDING_PROVIDER,
  MODEL_ID,
  type EmbeddingResponse,
} from "../shared/contracts";

if (process.env.NODE_ENV !== "test") {
  try {
    process.loadEnvFile(".env");
  } catch {
    // Environment variables are optional; health checks report missing credentials.
  }
}

const baseUrl = (process.env.QWEN_BASE_URL ?? "https://maas.qianwenaiapi.com/compatible-mode/v1")
  .replace(/\/+$/, "");
const apiKey = process.env.QWEN_API_KEY ?? "";
const model = process.env.QWEN_EMBEDDING_MODEL ?? MODEL_ID;

interface QwenEmbeddingItem {
  index?: number;
  embedding?: unknown;
}

interface QwenEmbeddingPayload {
  data?: QwenEmbeddingItem[];
  error?: {
    message?: string;
  };
  message?: string;
}

function configured(): boolean {
  return apiKey.length > 0;
}

export function embeddingHealth() {
  return {
    ok: configured(),
    provider: EMBEDDING_PROVIDER,
    model,
    dimension: EMBEDDING_DIMENSION,
    ...(configured() ? {} : { message: "QWEN_API_KEY is not configured" }),
  };
}

function errorMessage(payload: QwenEmbeddingPayload): string {
  return payload.error?.message ?? payload.message ?? "Qwen embedding request failed";
}

function parseVectors(payload: QwenEmbeddingPayload): number[][] {
  if (!Array.isArray(payload.data) || payload.data.length === 0) {
    throw new Error(errorMessage(payload));
  }

  const ordered = [...payload.data].sort((left, right) => (left.index ?? 0) - (right.index ?? 0));
  return ordered.map((item, index) => {
    if (
      !Array.isArray(item.embedding) ||
      item.embedding.length !== EMBEDDING_DIMENSION ||
      item.embedding.some((value) => typeof value !== "number" || !Number.isFinite(value))
    ) {
      throw new Error(
        `Qwen embedding ${index} must contain ${EMBEDDING_DIMENSION} finite numbers`,
      );
    }
    return item.embedding;
  });
}

export async function embedTexts(texts: string[]): Promise<EmbeddingResponse> {
  if (!configured()) {
    throw new Error("QWEN_API_KEY is not configured");
  }

  const startedAt = performance.now();
  const response = await fetch(`${baseUrl}/embeddings`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      input: texts,
      dimensions: EMBEDDING_DIMENSION,
      encoding_format: "float",
    }),
    signal: AbortSignal.timeout(120_000),
  });
  const payload = (await response.json()) as QwenEmbeddingPayload;

  if (!response.ok) {
    throw new Error(`Qwen embedding request failed (${response.status}): ${errorMessage(payload)}`);
  }

  return {
    model,
    dimension: EMBEDDING_DIMENSION,
    vectors: parseVectors(payload),
    inferenceMs: performance.now() - startedAt,
    backend: EMBEDDING_PROVIDER,
  };
}
