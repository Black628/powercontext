import process from "node:process";
import express, { type NextFunction, type Request, type Response } from "express";
import type { Metadata } from "seekdb";
import { embedTexts, embeddingHealth } from "./embedding";
import {
  client,
  connectionTarget,
  databaseName,
  describeCollection,
  getCollection,
  getOrCreateCollection,
  mapQueryResult,
} from "./seekdb";
import {
  validateEmbeddingTexts,
  validateCollectionName,
  validateRecords,
  validateSearchBody,
} from "./validation";

const app = express();
const port = Number(process.env.API_PORT ?? 8787);

app.use(express.json({ limit: "24mb" }));

app.get("/api/health", async (_request, response) => {
  const embedding = embeddingHealth();
  try {
    await client.execute("SELECT 1 AS ok");
    response.json({
      ok: true,
      mode: "server",
      target: connectionTarget(),
      database: databaseName(),
      embedding,
    });
  } catch (error) {
    response.json({
      ok: false,
      mode: "server",
      target: connectionTarget(),
      database: databaseName(),
      embedding,
      message: error instanceof Error ? error.message : String(error),
    });
  }
});

app.post("/api/embeddings", async (request, response) => {
  const texts = validateEmbeddingTexts(request.body?.texts);
  response.json(await embedTexts(texts));
});

app.get("/api/collections", async (_request, response) => {
  const collections = await client.listCollections({ withEmbeddingFunction: false });
  const summaries = await Promise.all(collections.map(describeCollection));
  response.json({ collections: summaries.sort((left, right) => left.name.localeCompare(right.name)) });
});

app.post("/api/collections", async (request, response) => {
  const name = validateCollectionName(request.body?.name);
  const collection = await getOrCreateCollection(name);
  response.status(201).json({ collection: await describeCollection(collection) });
});

app.delete("/api/collections/:name", async (request, response) => {
  const name = validateCollectionName(request.params.name);
  await client.deleteCollection(name);
  response.json({ deleted: name });
});

app.post("/api/collections/:name/documents", async (request, response) => {
  const name = validateCollectionName(request.params.name);
  const records = validateRecords(request.body?.records);
  const collection = await getCollection(name);

  const writeStartedAt = performance.now();
  await collection.upsert({
    ids: records.map((record) => record.id),
    documents: records.map((record) => record.document),
    embeddings: records.map((record) => record.embedding),
    metadatas: records.map((record) => record.metadata as Metadata),
  });
  const writeMs = performance.now() - writeStartedAt;

  const refreshStartedAt = performance.now();
  await collection.refresh_index();
  const refreshMs = performance.now() - refreshStartedAt;

  response.json({
    collection: name,
    upserted: records.length,
    total: await collection.count(),
    writeMs,
    refreshMs,
  });
});

app.post("/api/collections/:name/refresh", async (request, response) => {
  const name = validateCollectionName(request.params.name);
  const collection = await getCollection(name);
  const startedAt = performance.now();
  await collection.refresh_index();
  response.json({ collection: name, refreshMs: performance.now() - startedAt });
});

app.post("/api/collections/:name/search", async (request, response) => {
  const name = validateCollectionName(request.params.name);
  const { query, embedding, nResults } = validateSearchBody(request.body);
  const collection = await getCollection(name);

  const startedAt = performance.now();
  const result = await collection.hybridSearch({
    query: {
      whereDocument: { $contains: query },
      nResults: Math.max(nResults, 20),
    },
    knn: {
      queryEmbeddings: embedding,
      nResults: Math.max(nResults, 20),
    },
    rank: {
      rrf: {
        rankWindowSize: 50,
        rankConstant: 60,
      },
    },
    nResults,
    include: ["documents", "metadatas", "distances"],
  });
  const databaseMs = performance.now() - startedAt;

  response.json({
    collection: name,
    databaseMs,
    results: mapQueryResult(result),
  });
});

app.use((error: unknown, _request: Request, response: Response, _next: NextFunction) => {
  const message = error instanceof Error ? error.message : String(error);
  const status = /must|invalid|required|contain|characters|integer/i.test(message) ? 400 : 500;
  console.error(error);
  response.status(status).json({ error: message });
});

const server = app.listen(port, "127.0.0.1", () => {
  console.log(`seekdb API listening on http://127.0.0.1:${port}`);
});

async function shutdown() {
  server.close();
  await client.close();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
