import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Cpu,
  Database,
  Download,
  FileText,
  FileUp,
  Gauge,
  Library,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import {
  EMBEDDING_DIMENSION,
  EMBEDDING_PROVIDER,
  COLLECTION_NAME_PATTERN,
  MAX_BATCH_SIZE,
  MODEL_ID,
  type CollectionSummary,
  type DocumentRecord,
  type HealthResponse,
  type SearchResultItem,
} from "../shared/contracts";
import { api } from "./lib/api";
import { chunkText, documentFingerprint } from "./lib/chunk";
import {
  createMetric,
  summarizeMetrics,
  type PerformanceMetric,
} from "./lib/metrics";
import "./styles.css";

const EMBEDDING_BATCH_SIZE = 16;

interface ImportProgress {
  completed: number;
  total: number;
  label: string;
}

async function clearLegacyEmbeddingCaches(): Promise<void> {
  if ("caches" in window) {
    const cacheNames = await window.caches.keys();
    await Promise.all(
      cacheNames
        .filter((name) => /transformers|huggingface|onnx/i.test(name))
        .map((name) => window.caches.delete(name)),
    );
  }

  if ("databases" in indexedDB) {
    const databases = await indexedDB.databases();
    await Promise.all(
      databases
        .filter(
          (database): database is IDBDatabaseInfo & { name: string } =>
            typeof database.name === "string" &&
            /transformers|huggingface|onnx/i.test(database.name),
        )
        .map((database) => database.name)
        .map(
          (name) =>
            new Promise<void>((resolve) => {
              const request = indexedDB.deleteDatabase(name);
              request.onsuccess = request.onerror = request.onblocked = () => resolve();
            }),
        ),
    );
  }
}

function formatDuration(value: number): string {
  if (value < 1) {
    return "<1 ms";
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

function fileTypeAllowed(file: File): boolean {
  return /\.(txt|md)$/i.test(file.name);
}

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [selectedCollection, setSelectedCollection] = useState("");
  const [newCollection, setNewCollection] = useState("edge_notes");
  const [files, setFiles] = useState<File[]>([]);
  const [query, setQuery] = useState("");
  const [nResults, setNResults] = useState(8);
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [metrics, setMetrics] = useState<PerformanceMetric[]>([]);
  const [importProgress, setImportProgress] = useState<ImportProgress | null>(null);
  const [busy, setBusy] = useState<"create" | "delete" | "import" | "search" | "refresh" | null>(
    null,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const selectedSummary = collections.find(
    (collection) => collection.name === selectedCollection,
  );
  const metricSummary = useMemo(() => summarizeMetrics(metrics), [metrics]);

  async function loadCollections(preferred?: string) {
    const response = await api.collections();
    setCollections(response.collections);
    setSelectedCollection((current) => {
      if (preferred && response.collections.some((item) => item.name === preferred)) {
        return preferred;
      }
      if (response.collections.some((item) => item.name === current)) {
        return current;
      }
      return response.collections[0]?.name ?? "";
    });
  }

  async function checkDatabase() {
    try {
      const nextHealth = await api.health();
      setHealth(nextHealth);
      if (nextHealth.ok) {
        await loadCollections();
      }
    } catch (nextError) {
      setHealth({
        ok: false,
        mode: "server",
        target: "127.0.0.1:2881",
        database: "test",
        embedding: {
          ok: false,
          provider: EMBEDDING_PROVIDER,
          model: MODEL_ID,
          dimension: EMBEDDING_DIMENSION,
        },
        message: nextError instanceof Error ? nextError.message : String(nextError),
      });
    }
  }

  useEffect(() => {
    void clearLegacyEmbeddingCaches();
    void checkDatabase();
  }, []);

  function reportError(nextError: unknown) {
    setNotice("");
    setError(nextError instanceof Error ? nextError.message : String(nextError));
  }

  async function createCollection() {
    const name = newCollection.trim();
    if (!COLLECTION_NAME_PATTERN.test(name)) {
      reportError("知识库名称需为 3-48 位小写字母、数字或下划线，并以字母开头");
      return;
    }

    setBusy("create");
    setError("");
    try {
      await api.createCollection(name);
      await loadCollections(name);
      setNotice(`知识库 ${name} 已就绪`);
    } catch (nextError) {
      reportError(nextError);
    } finally {
      setBusy(null);
    }
  }

  async function deleteCollection() {
    if (!selectedCollection) {
      return;
    }
    if (!window.confirm(`删除知识库 ${selectedCollection} 及其中全部文档？`)) {
      return;
    }

    setBusy("delete");
    setError("");
    try {
      const deleted = selectedCollection;
      await api.deleteCollection(deleted);
      setResults([]);
      await loadCollections();
      setNotice(`知识库 ${deleted} 已删除`);
    } catch (nextError) {
      reportError(nextError);
    } finally {
      setBusy(null);
    }
  }

  function addFiles(selected: FileList | File[]) {
    const next = Array.from(selected).filter(fileTypeAllowed);
    if (next.length === 0) {
      reportError("请选择 .txt 或 .md 文件");
      return;
    }
    setFiles((current) => {
      const byKey = new Map(
        [...current, ...next].map((file) => [
          `${file.name}:${file.size}:${file.lastModified}`,
          file,
        ]),
      );
      return Array.from(byKey.values());
    });
    setError("");
  }

  async function importFiles() {
    if (!selectedCollection || files.length === 0 || !health?.embedding.ok) {
      return;
    }

    setBusy("import");
    setError("");
    setNotice("");

    try {
      const prepared: Array<{
        file: File;
        fingerprint: string;
        chunks: string[];
      }> = [];

      for (const file of files) {
        const readStartedAt = performance.now();
        const content = await file.text();
        setMetrics((current) => [
          ...current,
          createMetric("file-read", `读取 ${file.name}`, performance.now() - readStartedAt, {
            bytes: file.size,
          }),
        ]);

        const chunkStartedAt = performance.now();
        const chunks = chunkText(content);
        const fingerprint = await documentFingerprint(file.name, content);
        prepared.push({ file, fingerprint, chunks });
        setMetrics((current) => [
          ...current,
          createMetric("chunk", `分块 ${file.name}`, performance.now() - chunkStartedAt, {
            chunks: chunks.length,
          }),
        ]);
      }

      const totalChunks = prepared.reduce((sum, item) => sum + item.chunks.length, 0);
      if (totalChunks === 0) {
        throw new Error("所选文件没有可导入的文本");
      }

      let completed = 0;
      setImportProgress({ completed, total: totalChunks, label: "准备向量化" });

      for (const item of prepared) {
        for (let offset = 0; offset < item.chunks.length; offset += EMBEDDING_BATCH_SIZE) {
          const texts = item.chunks.slice(offset, offset + EMBEDDING_BATCH_SIZE);
          setImportProgress({
            completed,
            total: totalChunks,
            label: `向量化 ${item.file.name}`,
          });

          const embedded = await api.embed(texts);
          setMetrics((current) => [
            ...current,
            createMetric(
              "document-embedding",
              `文档向量化 ${item.file.name}`,
              embedded.inferenceMs,
              {
                backend: embedded.backend,
                batchSize: texts.length,
              },
            ),
          ]);

          const records: DocumentRecord[] = texts.map((document, batchIndex) => {
            const chunkIndex = offset + batchIndex;
            return {
              id: `${item.fingerprint}:${chunkIndex}`,
              document,
              embedding: embedded.vectors[batchIndex],
              metadata: {
                source: item.file.name,
                chunkIndex,
                sourceBytes: item.file.size,
                model: MODEL_ID,
                dimension: embedded.dimension,
                backend: embedded.backend,
              },
            };
          });

          const imported = await api.importDocuments(selectedCollection, records);
          setMetrics((current) => [
            ...current,
            createMetric("database-write", `写入 ${item.file.name}`, imported.writeMs, {
              records: imported.upserted,
            }),
            createMetric("index-refresh", "刷新 seekdb 索引", imported.refreshMs, {
              collection: selectedCollection,
            }),
          ]);

          completed += texts.length;
          setImportProgress({
            completed,
            total: totalChunks,
            label: `已写入 ${completed}/${totalChunks}`,
          });
        }
      }

      await loadCollections(selectedCollection);
      setFiles([]);
      setNotice(`已增量写入 ${totalChunks} 个文本块`);
    } catch (nextError) {
      reportError(nextError);
    } finally {
      setBusy(null);
      setImportProgress(null);
    }
  }

  async function refreshIndex() {
    if (!selectedCollection) {
      return;
    }
    setBusy("refresh");
    setError("");
    try {
      const response = await api.refreshIndex(selectedCollection);
      setMetrics((current) => [
        ...current,
        createMetric("index-refresh", "手动刷新 seekdb 索引", response.refreshMs, {
          collection: selectedCollection,
        }),
      ]);
      setNotice(`索引已刷新，用时 ${formatDuration(response.refreshMs)}`);
    } catch (nextError) {
      reportError(nextError);
    } finally {
      setBusy(null);
    }
  }

  async function runSearch() {
    const normalizedQuery = query.trim();
    if (!selectedCollection || !normalizedQuery || !health?.embedding.ok) {
      return;
    }

    setBusy("search");
    setError("");
    setNotice("");
    try {
      const embedded = await api.embed([normalizedQuery]);
      setMetrics((current) => [
        ...current,
        createMetric("query-embedding", "查询向量化", embedded.inferenceMs, {
          backend: embedded.backend,
        }),
      ]);

      const response = await api.search(
        selectedCollection,
        normalizedQuery,
        embedded.vectors[0],
        nResults,
      );
      setResults(response.results);
      setMetrics((current) => [
        ...current,
        createMetric("hybrid-search", "seekdb 原生混合检索", response.databaseMs, {
          results: response.results.length,
          collection: selectedCollection,
        }),
      ]);
    } catch (nextError) {
      reportError(nextError);
    } finally {
      setBusy(null);
    }
  }

  function exportMetrics() {
    downloadJson(`seekdb-performance-${new Date().toISOString()}.json`, {
      exportedAt: new Date().toISOString(),
      model: {
        id: MODEL_ID,
        provider: EMBEDDING_PROVIDER,
        dimension: EMBEDDING_DIMENSION,
      },
      database: health,
      collection: selectedCollection,
      summary: metricSummary,
      metrics,
    });
  }

  const importPercent = importProgress
    ? (importProgress.completed / importProgress.total) * 100
    : 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <Library size={20} strokeWidth={2.2} />
          </div>
          <div>
            <h1>Local Knowledge Lab</h1>
            <p>浏览器向量化 · seekdb 原生混合检索</p>
          </div>
        </div>
        <div className="status-cluster" aria-label="运行状态">
          <div className={`status-item ${health?.embedding.ok ? "is-ok" : "is-error"}`}>
            <Cpu size={15} />
            <span>
              {health?.embedding.ok
                ? `QWEN API · ${EMBEDDING_DIMENSION}维`
                : "Qwen 未配置"}
            </span>
          </div>
          <div className={`status-item ${health?.ok ? "is-ok" : "is-error"}`}>
            <Database size={15} />
            <span>{health?.ok ? `seekdb · ${health.database}` : "seekdb 未连接"}</span>
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <div>
              <span className="eyebrow">KNOWLEDGE BASES</span>
              <h2>知识库</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              title="重新检查 seekdb"
              onClick={() => void checkDatabase()}
              disabled={busy !== null}
            >
              <RefreshCw size={16} />
            </button>
          </div>

          <div className="collection-list">
            {collections.map((collection) => (
              <button
                className={`collection-row ${
                  selectedCollection === collection.name ? "is-selected" : ""
                }`}
                type="button"
                key={collection.name}
                onClick={() => {
                  setSelectedCollection(collection.name);
                  setResults([]);
                }}
              >
                <Database size={16} />
                <span className="collection-copy">
                  <strong>{collection.name}</strong>
                  <small>{collection.count} 个文本块</small>
                </span>
              </button>
            ))}
            {collections.length === 0 && (
              <div className="empty-compact">
                <Database size={22} />
                <span>{health?.ok ? "还没有知识库" : "连接后显示知识库"}</span>
              </div>
            )}
          </div>

          <form
            className="create-form"
            onSubmit={(event) => {
              event.preventDefault();
              void createCollection();
            }}
          >
            <label htmlFor="collection-name">新建知识库</label>
            <div className="input-action">
              <input
                id="collection-name"
                value={newCollection}
                onChange={(event) => setNewCollection(event.target.value.toLowerCase())}
                placeholder="edge_notes"
                spellCheck={false}
              />
              <button
                className="icon-button is-primary"
                type="submit"
                title="新建知识库"
                disabled={!health?.ok || busy !== null}
              >
                <Plus size={17} />
              </button>
            </div>
            <span className="field-hint">小写字母、数字、下划线</span>
          </form>

          <div className="sidebar-footer">
            <button
              className="text-button danger"
              type="button"
              onClick={() => void deleteCollection()}
              disabled={!selectedCollection || busy !== null}
            >
              <Trash2 size={15} />
              删除当前知识库
            </button>
          </div>
        </aside>

        <main className="main-canvas">
          {(error || notice) && (
            <div className={`message-bar ${error ? "is-error" : "is-notice"}`}>
              {error ? <TriangleAlert size={17} /> : <Activity size={17} />}
              <span>{error || notice}</span>
              <button
                className="message-close"
                type="button"
                title="关闭"
                onClick={() => {
                  setError("");
                  setNotice("");
                }}
              >
                <X size={15} />
              </button>
            </div>
          )}

          <section className="context-bar">
            <div>
              <span className="eyebrow">ACTIVE COLLECTION</span>
              <h2>{selectedCollection || "请选择或新建知识库"}</h2>
            </div>
            <div className="context-facts">
              <span>{selectedSummary?.count ?? 0} 个文本块</span>
              <span>{selectedSummary?.dimension ?? EMBEDDING_DIMENSION} 维</span>
              <span>{selectedSummary?.distance ?? "cosine"}</span>
            </div>
          </section>

          <div className="work-grid">
            <section className="panel import-panel">
              <div className="panel-heading">
                <div>
                  <span className="panel-index">01</span>
                  <h3>增量导入</h3>
                </div>
                <button
                  className="icon-button"
                  type="button"
                  title="刷新当前知识库索引"
                  onClick={() => void refreshIndex()}
                  disabled={!selectedCollection || busy !== null}
                >
                  <RefreshCw size={16} />
                </button>
              </div>

              <button
                className="dropzone"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  addFiles(event.dataTransfer.files);
                }}
                disabled={!selectedCollection || busy !== null}
              >
                <FileUp size={24} />
                <strong>选择或拖入文档</strong>
                <span>.txt / .md · 通过 Qwen API 向量化</span>
              </button>
              <input
                ref={fileInputRef}
                className="visually-hidden"
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                multiple
                onChange={(event) => {
                  if (event.target.files) {
                    addFiles(event.target.files);
                  }
                  event.target.value = "";
                }}
              />

              <div className="file-list">
                {files.map((file) => (
                  <div className="file-row" key={`${file.name}:${file.lastModified}`}>
                    <FileText size={16} />
                    <span>
                      <strong>{file.name}</strong>
                      <small>{Math.max(1, Math.round(file.size / 1024))} KB</small>
                    </span>
                    <button
                      className="icon-button"
                      type="button"
                      title={`移除 ${file.name}`}
                      onClick={() =>
                        setFiles((current) => current.filter((item) => item !== file))
                      }
                      disabled={busy === "import"}
                    >
                      <X size={15} />
                    </button>
                  </div>
                ))}
              </div>

              {importProgress && (
                <div className="progress-block">
                  <div>
                    <span>{importProgress.label}</span>
                    <strong>{Math.round(importPercent)}%</strong>
                  </div>
                  <progress max="100" value={importPercent} />
                </div>
              )}

              <button
                className="primary-button"
                type="button"
                onClick={() => void importFiles()}
                disabled={
                  !selectedCollection ||
                  files.length === 0 ||
                  !health?.embedding.ok ||
                  busy !== null
                }
              >
                <FileUp size={17} />
                {busy === "import" ? "正在导入" : `导入 ${files.length || ""} 个文件`}
              </button>
            </section>

            <section className="panel search-panel">
              <div className="panel-heading">
                <div>
                  <span className="panel-index">02</span>
                  <h3>混合检索</h3>
                </div>
                <span className="native-badge">RRF · NATIVE</span>
              </div>

              <form
                className="search-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void runSearch();
                }}
              >
                <label htmlFor="search-query">查询内容</label>
                <textarea
                  id="search-query"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="输入自然语言问题或关键词"
                  rows={4}
                />
                <div className="search-controls">
                  <label htmlFor="result-count">
                    返回数量
                    <select
                      id="result-count"
                      value={nResults}
                      onChange={(event) => setNResults(Number(event.target.value))}
                    >
                      {[5, 8, 10, 20].map((count) => (
                        <option value={count} key={count}>
                          {count}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="primary-button"
                    type="submit"
                    disabled={
                      !selectedCollection ||
                      !query.trim() ||
                      !health?.embedding.ok ||
                      busy !== null
                    }
                  >
                    <Search size={17} />
                    {busy === "search" ? "检索中" : "开始检索"}
                  </button>
                </div>
              </form>

              <div className="results">
                {results.map((result, index) => (
                  <article className="result-row" key={result.id}>
                    <span className="result-rank">{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <div className="result-meta">
                        <strong>{String(result.metadata?.source ?? "未命名文档")}</strong>
                        <span>片段 {String(result.metadata?.chunkIndex ?? "-")}</span>
                        {result.distance !== null && (
                          <span>距离 {result.distance.toFixed(4)}</span>
                        )}
                      </div>
                      <p>{result.document}</p>
                    </div>
                  </article>
                ))}
                {results.length === 0 && (
                  <div className="empty-results">
                    <Search size={25} />
                    <span>检索结果会按 seekdb RRF 排名显示在这里</span>
                  </div>
                )}
              </div>
            </section>
          </div>

          <section className="performance-band">
            <div className="performance-heading">
              <div>
                <Gauge size={18} />
                <div>
                  <span className="eyebrow">PERFORMANCE</span>
                  <h3>端到端阶段耗时</h3>
                </div>
              </div>
              <div className="performance-actions">
                <strong>{formatDuration(metricSummary.totalMs)}</strong>
                <button
                  className="icon-button"
                  type="button"
                  title="导出性能 JSON"
                  onClick={exportMetrics}
                  disabled={metrics.length === 0}
                >
                  <Download size={16} />
                </button>
              </div>
            </div>
            <div className="metric-strip">
              {metrics.slice(-6).map((metric) => (
                <div className="metric-cell" key={metric.id}>
                  <span>{metric.label}</span>
                  <strong>{formatDuration(metric.durationMs)}</strong>
                </div>
              ))}
              {metrics.length === 0 && (
                <div className="metric-placeholder">操作后自动记录模型、推理、写入和检索耗时</div>
              )}
            </div>
          </section>
        </main>
      </div>

      <footer className="runtime-footer">
        <span>{MODEL_ID} · {EMBEDDING_DIMENSION}维</span>
        <span>batch {EMBEDDING_BATCH_SIZE}/{MAX_BATCH_SIZE}</span>
        <span>服务端代理</span>
      </footer>
    </div>
  );
}
