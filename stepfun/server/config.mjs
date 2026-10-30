// 运行期配置。全部可用环境变量覆盖，便于评测时固定条件。
const explicitPort = process.env.SEEKDB_PORT;

export const config = {
  httpPort: Number(process.env.PORT || 7860),

  // 集合（seekdb 表）名。切换知识库 = 切换集合名。
  collection: process.env.SEEKDB_COLLECTION || 'edge_kb',

  // all-MiniLM-L6-v2 输出 384 维；换模型时同步改这里和前端 MODEL。
  dimension: Number(process.env.EMBED_DIM || 384),

  // seekdb 服务端连接（Windows 下不支持嵌入式，必须用 server 模式）
  // 端口不写死：部署方式不同，seekdb 可能听 2882 也可能听 2881
  //（实测：Docker/原生实例多用 2882；WSL 里的 observer 听 2881，经 wslrelay 转发到 Windows 的 127.0.0.1:2881）。
  // 未显式设置 SEEKDB_PORT 时，连接阶段按 probePorts 顺序逐个握手 select 1，采用第一个可用的。
  seekdb: {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(explicitPort || 2882),
    probePorts: explicitPort ? [Number(explicitPort)] : [Number(process.env.SEEKDB_PORT || 2882), 2881],
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
