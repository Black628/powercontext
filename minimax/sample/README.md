# 验证样例

本目录是「可机检」的验证样例，配合 `scripts/check_expected.py` 使用。

| 文件 | 作用 |
| --- | --- |
| `task.txt` | 本次任务描述（一句话）。`context`/`apply` 不传 `--task` 时读它 |
| `specs/discount.v1.md` | 规格 v1（历史版本，明确写着「不提供金额门槛筛选」） |
| `specs/discount.v2.md` | 规格 v2（新增 SPEC-02 min_total 门槛筛选） |
| `offline_patch.diff` | 离线回放用的参照 patch（= `demo_project/patch/fix.diff`，由 `scripts/make_patch.py` 从快照生成） |
| `expected.json` | 预期结果（机检口径：必须出现的规格/代码/测试来源、checks、前后验证退出码） |
| `steps.md` | 从零跑通的步骤与每步预期 |
| `reset.md` | 重置方法（回到干净状态再跑一遍） |
| `initial-data.md` | 初始数据说明（seekdb 表结构、初始切片、PowerContext scope） |

## 初始数据

- **seekdb**：库 `sdd_minimax`，表 `sdd_docs`（切片）、`sdd_runs`（索引 run）、`sdd_state_refs`（状态索引），
  FULLTEXT 索引 `ft_sdd_docs(content, path, symbol)`，parser 优先 `ik`。
  初始数据**不预置**，全部由 `init-db` 从 `demo_project/` 的真实文件切片写入。
- **PowerContext**：scope `sdd-context:minimax`，`kind=sdd_task_state` 的显式记忆。
  初始为空；`run-demo` 会写入 2~3 条状态记录。
- **demo_project**：初始为「修改前」基线（`inventory/discount.py` 没有 `min_total` 参数），
  由 `demo_project/snapshots/before/` 决定，`sddcontext reset` 可随时还原。
