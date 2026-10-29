# 重置方法

演示可以反复跑，所有状态都由脚本重建，不依赖任何手工残留。

## 只重置业务工程（回到「修改前」基线）

```powershell
.\scripts\sdd.ps1 reset --spec v2     # 恢复 snapshots/before + 规格 v2
.\scripts\sdd.ps1 reset --spec v1     # 恢复 snapshots/before + 规格 v1
```

等价于把 `demo_project/snapshots/before/**` 覆盖回 `demo_project/`，再把
`sample/specs/discount.<v>.md` 复制成 `demo_project/specs/discount.md`。

## 重置数据库（清空切片，重新入库）

```powershell
.\scripts\sdd.ps1 init-db --recreate
```

`--recreate` 会 `DROP TABLE sdd_docs` 后重建，并按真实文件重新切片入库。
只做增量刷新用 `.\scripts\sdd.ps1 index`。

## 重置本地产物

```powershell
Remove-Item -Recurse -Force .state          # 上下文快照、prompt、最后 patch、scope 缓存
Remove-Item -Recurse -Force records         # 全部证据记录（重新跑会再生成）
```

## 重置 PowerContext 状态

状态写在 scope `sdd-context:minimax` 里，按 `kind=sdd_task_state` 的显式记忆存储。
要清空：

- 用 Dashboard（`http://127.0.0.1:8001/dashboard`）删除该 scope，或
- 换一个 scope：`$env:POWERCONTEXT_SCOPE_ID="scp_xxx"`，或
- 完全离线：`$env:SDD_STATE_BACKEND="file"`，状态落到 `.state/powercontext-fallback.jsonl`
  （删掉该文件即清空）。

> 注意：本模块不会自动删除 PowerContext 里的历史记录；`state load` 按 `task_id` 取
> `saved_at_ms` 最新的一条，因此历史记录不会污染结果。

## 一条命令回到干净状态

```powershell
.\scripts\run-demo.ps1
```

`run-demo` 内部第一步就是 `reset` 基线 + `init-db --recreate`，因此可以直接反复执行。
