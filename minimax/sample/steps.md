# 验证步骤（从零跑通）

所有命令在 `minimax/` 目录下执行。Windows 用 `scripts\sdd.ps1`，Linux/WSL 用 `scripts/sdd.sh`。

## 0. 依赖

```powershell
.\scripts\bootstrap.ps1        # Windows：装 pymysql + 端口探测 + doctor
```
```bash
./scripts/bootstrap.sh         # Linux/WSL
```

需要：Python 3.9+、`pymysql`、可达的 seekdb（MySQL 协议）、可选的 PowerContext Server。

## 1. 环境自检

```powershell
.\scripts\sdd.ps1 doctor
```
预期：`seekdb.ok=true`（端口自动探测）；`powercontext.ok=true`（地址 8001 或 8000）；
`minimax.api_key_present=false` 时 `mode=replay(离线回放)`。

## 2. 建库 + 入库（真实文件切片）

```powershell
.\scripts\sdd.ps1 init-db --recreate
```
预期：创建库表与 FULLTEXT 索引（parser=ik），写入 **24 条**切片
（spec=4 / code=5 / test=15），输出 `write.inserted`、`db_counts_by_kind`。

## 3. 生成上下文（带来源）

```powershell
.\scripts\sdd.ps1 context --format both
```
预期：
- `specs` 命中 `specs/discount.md:12-26 [SPEC-02]`、`SPEC-01`、`SPEC-03`；
- `code` 命中 `inventory/discount.py:21-28 [apply_discount]`；
- `tests` 命中 `tests/test_discount_target.py` 的 6 个目标测试 + `tests/test_discount_regression.py` 的 7 个回归测试；
- `checks` 四项全为 true；
- 输出文件：`.state/context/context.json` 与 `context.txt`。

## 4. 修改前验证（必须失败）

```powershell
.\scripts\sdd.ps1 verify --phase before
```
预期：`target: exit=1`（`TypeError: ... unexpected keyword argument 'min_total'`），
`regression: exit=0 OK`，命令整体退出码 1。日志在 `records/test-logs/before-*.log`。

## 5. 改代码（MiniMax 或离线回放）

```powershell
.\scripts\sdd.ps1 apply --patch-source auto
```
预期：`mode=replay`（无 key）或 `mode=live`（有 key）；`applied_files={"inventory/discount.py": "modified"}`；
patch 落地到 `records/patches/<ts>-<mode>.diff`；调用记录追加到 `records/minimax.jsonl`。

## 6. 修改后验证（必须通过）

```powershell
.\scripts\sdd.ps1 verify --phase after
```
预期：target / regression / full 三个 suite 全部 `exit=0 OK`，命令退出码 0。

## 7. 状态复用（跨进程）

```powershell
.\scripts\sdd.ps1 state save --completed "已实现 min_total" --todo "补文档"
.\scripts\sdd.ps1 state list
.\scripts\sdd.ps1 state load          # 独立进程读回
```
预期：`load` 能读回 `completed/todo/verification`；`records/powercontext.md` 里有 save/load 的
HTTP 往返摘要与 artifact_id。

## 8. 规格更新演示（防写死）

```powershell
.\scripts\demo-spec-update.ps1
```
预期：v1 上下文的 SPEC-02 段落写的是「v1 不支持 min_total」，v2 写的是「v2 新增」；
两次正文不同，且差异来自「改规格文件 -> 重新 index -> 重新查库」。

## 9. 一键复现全部

```powershell
.\scripts\run-demo.ps1
```
11 个步骤全部 `OK`，报告写入 `records/demo-run-<ts>.md`。

## 10. 机检预期结果

```powershell
python .\scripts\check_expected.py
```
逐条校验 `sample/expected.json`，全部 PASS 时退出码 0。
