// 运行期配置。全部可用环境变量覆盖，便于评测时固定条件。
export const config = {
  httpPort: Number(process.env.PORT || 7860),

  // 集合（seekdb 表）名。切换知识库 = 切换集合名。
  collection: process.env.SEEKDB_COLLECTION || 'edge_kb',

  // all-MiniLM-L6-v2 输出 384 维；换模型时同步改这里和前端 MODEL。
  dimension: Number(process.env.EMBED_DIM || 384),

  // seekdb 服务端连接（Windows 下不支持嵌入式，必须用 server 模式）
  seekdb: {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: process.env.SEEKDB_DATABASE || 'test',
  },

  // 混合检索候选集大小（两路各取多少，再由 seekdb 用 RRF 融合）
  recallK: Number(process.env.RECALL_K || 20),
  defaultTopK: Number(process.env.TOP_K || 5),

  // 前端模型来源。国内网络可设为 https://hf-mirror.com
  modelHost: process.env.MODEL_HOST || 'https://huggingface.co',
  modelId: process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2',
};
