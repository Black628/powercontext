# 规有所依：SDD 工程上下文

**当前交付状态：2026-10-28 已完成真实完整流程验证。** MiniMax 从 before 快照生成了当前业务修改，seekdb 实际保存并查询资料，PowerContext 实际保存并在新进程读取状态；规格更新验证通过。真实记录位于 records/live/，最终模型代码位于 snapshots/after-minimax.mjs。尚未推送或在评测平台提交。

本机已安装 npm 11.6.2、mysql2 3.11.5、PowerContext 1.4.1 和 seekdb 1.3.0；复用 Node.js 24.22.1 与 Python 3.12。运行时与数据位于聊天工作区的 work/。重开 PowerShell 后，在聊天工作区根目录执行 `. ./outputs/enter-env.ps1` 和 `./outputs/start-services.ps1`。详细本机说明见 outputs/INSTALL.md。

## 仓库基线

所有项目资产位于 minimax/。基线为 oceanbase/powercontext 的 `2026-competition-ecosystem`，commit `d647275c308e09b7b623f322a26f5c4e85734fb0`。公开 Git 克隆失败后，通过 GitHub API 读取目录和提交元数据，恢复 Git 对象并核对出完全相同的 tree 和 commit SHA。没有改动其他赛道。origin 指向用户提供的 gao-mutable/powercontext；未推送。该私有仓库的比赛分支查询返回 404，不能假定其 master 是比赛基线。

## 依赖与配置

Node.js >=22，npm，mysql2 3.11.5。PowerContext HTTP 已在 1.4.1 实机验证。seekdb 使用 MySQL 协议，可替换为 OceanBase MySQL 租户。本次使用 Windows 原生 seekdb 1.3.0，无需 Docker。Docker 配置只是可选部署示例，未实测。MiniMax 实际使用 MiniMax-M2.5 和国内地址 https://api.minimaxi.com/v1；国际站密钥应使用 .env.example 的国际地址。

在 minimax 目录执行：

```sh
npm install
cp .env.example .env
```

PowerShell 将 cp 换为 Copy-Item。填写 .env 中连接信息和密钥。不要提交 .env。无认证的本机 PowerContext 应将 POWERCONTEXT_TOKEN 留空。

另一台机器没有数据库时，可安装 Docker，配置 SEEKDB_ROOT_PASSWORD 环境变量（与 DATABASE_URL 密码一致），运行 `docker compose up -d`；等待服务就绪，再运行 `node --env-file=.env init-db.mjs`。当前本机已启动原生 seekdb 并创建 sdd，无需重复安装或初始化密码。

没有 PowerContext 时，安装 Python 3.11+ 和 uv，按官方方式启动独立服务：

```sh
uv tool install "powercontext[cli,server]==1.4.1"
powercontext server run
```

默认本地服务可显式保存 Memory，无需配置服务端模型。运行 `node --env-file=.env create-scope.mjs`，把响应中的真实 scope_id 填到 .env。必须为每次完整验收创建空 Scope，不能填一个未创建的名称。PowerContext 服务拥有独立持久化存储；本模块不以本地 JSON 冒充 PowerContext。

## 调用入口与验收

```sh
node --env-file=.env cli.mjs ingest
node --env-file=.env cli.mjs context
node --env-file=.env demo.mjs
```

ingest 输入 task.json 指定的真实文件，事务替换数据库中的当前资料；context 输入任务 ID（当前单样例从 task.json 读取），输出 JSON，包括规格全文与 REQ 编号、代码和测试路径、SHA256、PowerContext 历次记录和 latest 状态。检索以 task_id 和受控资料清单选择局部任务所需文件，不使用向量模型。资料缺失或本地文件 SHA 与库中不同会报错，必须重新导入。不会回退到固定答案。

demo 在不同 Node 子进程中依次读取空状态、保存待办、重启读取、还原 before 业务代码、重新导入、将数据库实际查询结果和恢复状态交给 MiniMax、写入模型返回代码、执行固定测试、保存结果、再重启读取。随后修改规格并重导入，断言查询包含 UPDATE-1，最后还原规格并重导入。PowerContext 断线不会降级成本地文件。若中途失败，保留 evidence 并以非零码退出，不产生成功结论。

单独测试无需外部依赖：

```sh
node verify.mjs
```

该命令临时切换 business/orders.mjs，使用完全相同的测试运行 before/after，finally 恢复候选代码。不要与其他修改业务文件的命令并发运行。

手动状态接口示例（PowerShell 单引号可直接传 JSON）：

```sh
node --env-file=.env cli.mjs save '{"done":["reviewed specs"],"todo":["implement filter"],"checks":[]}'
```

## 验证样例

任务：给订单查询增加精确状态筛选。初始数据在 tests/feature.test.mjs，包括 paid、pending、空字符串四条记录。

必要上下文来源：specs/orders.md 中 REQ-1～REQ-4；business/orders.mjs 的 listOrders；tests/feature.test.mjs 的新增行为；tests/regression.test.mjs 的兼容约束。上述四个文件均必须由数据库查询返回。所有路径均以 minimax/ 为根。

预期：paid 返回 ID 1、3；未知值和 PAID 返回空数组；空字符串仅返回 ID 4。默认查询返回全部数据及原顺序；冻结输入可查询且不被修改；空输入返回空数组。before 功能 3 项失败、回归 2 项通过；after 全部 5 项通过。

重置：将 snapshots/before.mjs 复制到 business/orders.mjs（会覆盖此演示文件），重新 ingest；为新演示创建新的 PowerContext Scope。恢复本地候选使用 snapshots/candidate.mjs。不要删除或跳过测试。

## 证据与提交

records/local-tests.json 是本次真实本地运行日志，包含命令、退出码、基线 HEAD、业务代码 SHA 和测试摘要；records/candidate.diff 是本地候选差异，不能作为 MiniMax 证据。

真实运行后 evidence/ 将产生：contexts.jsonl（完整实际上下文）；database.jsonl（导入及 SQL、参数、查询结果）；minimax.jsonl（任务、上下文、模型 ID、最终代码和修改前后 SHA，不记录私有思维链或密钥）；powercontext.jsonl（服务端状态写入响应和后续读取内容）；tests.json（测试命令、退出码、代码版本和输出）。验收时须人工核对模型 after_sha 与最终源码一致。证据目录默认忽略，审查无凭据后只将上述指定文件加入提交，禁止强制添加整个目录。

records/live/acceptance.json 已记录本次 Node、数据库、PowerContext、模型版本和最终代码 SHA；records/powercontext-requirements.txt 固定服务依赖；package-lock.json 固定 Node 依赖。真实运行中先遇到国际站 401，切换国内地址后成功；随后增加 reasoning_split 和代码块剥离以适配模型输出，未记录私有思维链。完整成功流程随后从新 Scope 重跑通过。

## 已知限制与来源

单任务、小文件、串行演示；按完整文件构造上下文，不做大型工程的语义排序。PowerContext Memory 单条有大小限制，超大测试日志应另存证据并仅将摘要写入 Memory。Agent 接受裸 JavaScript 或单个代码块，去掉包装后验证再写入。当前 Windows 原生服务组合已实测；Docker 部署示例尚未实测。

业务示例和模块代码为本次新建。外部项目仅通过协议调用：

- [比赛基线](https://github.com/oceanbase/powercontext/tree/2026-competition-ecosystem)
- [PowerContext 官方安装文档](https://github.com/oceanbase/powercontext/blob/master/docs/en/docs/get-started/install-and-run.md)
- [PowerContext OpenAPI](https://github.com/oceanbase/powercontext/blob/master/openapi/powercontext.yaml)
- [seekdb Docker 官方说明](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)
- [MiniMax 官方模型说明](https://www.minimax.io/models/text)
