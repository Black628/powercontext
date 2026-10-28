# Local Knowledge Lab

一个可直接运行的 AI 知识库示例：

- 文档和查询通过本机 Node API 调用 Qwen `qwen3.7-text-embedding` 生成 embedding，密钥不进入浏览器。
- Qwen 返回 1024 维向量；Node API 通过 `seekdb@1.4.0` 保存向量、原文和元数据。
- 检索直接调用 seekdb 原生 `collection.hybridSearch()`，由数据库执行全文检索、向量检索和 RRF 排名。
- 支持稳定文档 ID、增量 `upsert`、`refresh_index()`、多知识库切换和性能 JSON 导出。

## 环境要求

- Node.js 20.12 或更高版本
- npm 10 或更高版本
- 已运行的 seekdb Server，默认连接 `127.0.0.1:2881`
- 可访问 Qwen embedding API 的网络环境

## 启动 seekdb

按 [seekdb 官方文档](https://www.oceanbase.ai/docs) 启动 seekdb Server，并准备一个可写数据库。默认配置是：

```text
host: 127.0.0.1
port: 2881
user: root
password:
database: test
```

复制环境变量模板并按实际连接修改：

```bash
cp .env.example .env
```

OceanBase Server 模式可额外设置 `SEEKDB_TENANT`。

## 本地运行

```bash
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。页面通过本机 API 服务调用 Qwen，浏览器不会下载 embedding 模型，也不会接触 API 密钥。

`@seekdb/default-embed` 不需要安装。该项目明确把 embedding 放在 Node API 中执行，并通过 `embeddingFunction: null` 向 seekdb 传入已生成向量。

## 使用步骤

1. 确认页头显示 `seekdb · test` 和 `QWEN API · 1024 维`。
2. 新建知识库，例如 `edge_notes`。
3. 导入 `samples/` 中的 `.md` 或 `.txt` 文件，也可以拖入自己的文档。
4. 输入查询并执行检索。结果来自 seekdb 原生混合检索和 RRF 排名。
5. 在底部性能区域导出 JSON，获取模型加载、文档推理、数据库写入、索引刷新、查询推理和混合检索耗时。

重复导入同名且内容相同的文件会使用相同文档指纹和分块 ID，通过 `upsert` 更新而不是重复追加。内容变化时会生成新的指纹。

## 验证

```bash
npm run check
```

测试覆盖文本规范化、重叠分块、seekdb 结果映射和性能汇总。`npm run build` 同时执行 TypeScript 类型检查和 Vite 生产构建。

真实数据库链路可通过以下方式确认：

```bash
curl http://127.0.0.1:8787/api/health
```

导入后，服务端会对每批数据依次调用 `collection.upsert()` 和 `collection.refresh_index()`；查询端点只调用 `collection.hybridSearch()`，不在浏览器或 Node 层拼接全文与向量结果。

## 项目结构

```text
server/                seekdb 连接、Qwen embedding、参数校验和 HTTP API
shared/                前后端共享的数据契约
src/lib/               分块、API、性能统计和结果映射
src/App.tsx            知识库工作台
samples/               示例文档和固定查询集
TECHNICAL_NOTE.md      一页技术说明
```

## 已知边界

- 当前导入格式为 UTF-8 `.txt` 和 `.md`。
- 单批 API 最多接收 64 个文本块，前端默认每批推理 16 个。
- 知识库名称限制为 3-48 位小写字母、数字或下划线，并以字母开头。
- 模型固定为 1024 维；切换模型时必须同步调整 seekdb collection schema。
