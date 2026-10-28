import {
  FulltextIndexConfig,
  Schema,
  SeekdbClient,
  VectorIndexConfig,
  type Collection,
  type Metadata,
  type QueryResult,
} from "seekdb";
import {
  EMBEDDING_DIMENSION,
  type CollectionSummary,
  type SearchResultItem,
} from "../shared/contracts";

if (process.env.NODE_ENV !== "test") {
  try {
    process.loadEnvFile(".env");
  } catch {
    // Environment variables are optional; documented defaults are used when absent.
  }
}

const connection = {
  host: process.env.SEEKDB_HOST ?? "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT ?? 2881),
  user: process.env.SEEKDB_USER ?? "root",
  password: process.env.SEEKDB_PASSWORD ?? "",
  database: process.env.SEEKDB_DATABASE ?? "test",
  tenant: process.env.SEEKDB_TENANT || undefined,
};

export const client = new SeekdbClient(connection);

const schema = new Schema({
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: EMBEDDING_DIMENSION },
    embeddingFunction: null,
  }),
});

export function connectionTarget(): string {
  return `${connection.host}:${connection.port}`;
}

export function databaseName(): string {
  return connection.database;
}

export async function getOrCreateCollection(name: string): Promise<Collection> {
  return client.getOrCreateCollection({
    name,
    schema,
    embeddingFunction: null,
  });
}

export async function getCollection(name: string): Promise<Collection> {
  return client.getCollection({ name, embeddingFunction: null });
}

export async function describeCollection(collection: Collection): Promise<CollectionSummary> {
  const [description, count] = await Promise.all([
    collection.describe(),
    collection.count(),
  ]);
  return {
    name: description.name,
    count,
    dimension: description.dimension,
    distance: description.distance,
  };
}

export function mapQueryResult(result: QueryResult<Metadata>): SearchResultItem[] {
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
