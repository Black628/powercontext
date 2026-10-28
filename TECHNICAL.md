# 一页技术说明

模块入口为 cli.mjs，业务任务为 orders-status；资料清单及目标保存在 task.json。规格、业务文件与两类测试组成完整工程上下文。

数据流：文件 → ingest → seekdb/OceanBase sdd_sources → task_id SQL 查询 → SHA 新鲜度校验 → 合并 PowerContext 状态 → MiniMax HTTP → 业务代码 → 固定测试 → PowerContext Memory → 新进程 context。

数据库负责持久保存真实资料正文和摘要，并提供构造上下文的查询结果。参数化 SQL 防止将任务内容解释为查询语句。事务重导入替换旧版本，规格更新后的下一次查询返回新内容；未导入的变更直接报过期错误。

MiniMax 负责从 before 快照完成 listOrders 状态筛选修改；Agent 仅允许写入该文件，测试不交给模型修改。模型请求、最终返回内容、前后代码 SHA 形成审计链。真实模型为 MiniMax-M2.5，国内 HTTP 接口；最终源码 SHA 已核对与模型记录一致，证据见 records/live/。

PowerContext 保存 done、todo、checks 的显式 atomic-memory，带任务标识与时间戳。每次 context 请求通过 HTTP 读取服务器的活动 Memory，分页后筛选任务并输出 latest。demo 使用新进程验证状态复用，恢复的 todo 随上下文传入 MiniMax；无需恢复完整会话。只保存事实和结果，不保存私有思维链。

verify.mjs 在相同测试文件上运行 before 功能、before 回归、after 全集。日志记录命令、退出码和源码摘要，目标失败须为功能断言失败。真实 MiniMax 修改后的结果为 1 / 0 / 0，详见 records/live/tests.json。seekdb 1.3.0、PowerContext 1.4.1 与真实 MiniMax 的完整流程已执行通过，规格更新与跨进程状态读取均已验证。
