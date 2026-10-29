# MiniMax 调用记录摘要

- 日志文件：`records/minimax.jsonl`（共 11 条）
- 真实调用成功：0 条；真实调用失败：9 条；离线回放：2 条

## 真实调用（mode=live）
### 2026-10-29T20:17:16+0800  MiniMax-M3  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 277 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201716-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c4ba3546cac0147219455ad8dc0"}
`

### 2026-10-29T20:17:16+0800  MiniMax-M3.1-Flash  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 257 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201716-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c4c29117492c8871979606892db"}
`

### 2026-10-29T20:17:16+0800  MiniMax-M2.7  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 238 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201716-prompt.md`
- 错误：`HTTP 404 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"not_found_error","message":"The model \"MiniMax-M2.7\" is no longer available. (2069)","http_code":"404"},"request_id":"07326c4c5a6007e40f937127771fa7cb"}`

### 2026-10-29T20:17:31+0800  MiniMax-M3  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 602 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201731-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c5b47bd1936c2367a475ebf5faa"}
`

### 2026-10-29T20:17:32+0800  MiniMax-M3.1-Flash  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 1013 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201732-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c5c8d3b59696bdf5af2f2612ece"}
`

### 2026-10-29T20:17:33+0800  MiniMax-M2.7  status=error
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 424 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(8529字,sha=f8e043007c7b175e)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201733-prompt.md`
- 错误：`HTTP 404 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"not_found_error","message":"The model \"MiniMax-M2.7\" is no longer available. (2069)","http_code":"404"},"request_id":"07326c5c3a04a19ba2209af8f1aaaf76"}`

### 2026-10-29T20:18:28+0800  MiniMax-M3  status=error  http=402
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 375 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(7316字,sha=b6dba4b9de7940a4)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201828-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c9382eb7eb71ced6b7165a6a84a"}
`

### 2026-10-29T20:18:28+0800  MiniMax-M3.1-Flash  status=error  http=402
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 409 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(7316字,sha=b6dba4b9de7940a4)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201828-prompt.md`
- 错误：`HTTP 402 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"insufficient_balance_error","message":"insufficient balance (1008)","http_code":"402"},"request_id":"07326c9438b7c5b0a06897fef3e37eed"}
`

### 2026-10-29T20:18:29+0800  MiniMax-M2.7  status=error  http=404
- endpoint: `https://api.minimaxi.com/v1/chat/completions`
- 耗时: 198 ms；usage: prompt=None completion=None total=None
- messages: system(128字,sha=99661c9e12d3ba48), user(7316字,sha=b6dba4b9de7940a4)
- prompt 全文: `D:\Deep seek文件\deepseek\powercontext\minimax\records\prompts\20261029-201829-prompt.md`
- 错误：`HTTP 404 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"not_found_error","message":"The model \"MiniMax-M2.7\" is no longer available. (2069)","http_code":"404"},"request_id":"07326c94ca55ce5eaf8a32ea4d0f421a"}`

## 离线回放（mode=replay，不是模型响应）
- 2026-10-29T20:17:33+0800  原因：实时调用失败（所有模型调用都失败，最后一次错误：HTTP 404 <- https://api.minimaxi.com/v1/chat/completions: {"type":"error","error":{"type":"not_found_error","message":"The model \"MiniMax-M2.7\" is no longer available. (2069)","http_code":"404"},"request_id":"07326c5c3a04a19ba2209af8f1aaaf76"}），回退 sample/offline_patch.diff
  - patch 来源：`D:\Deep seek文件\deepseek\powercontext\minimax\sample\offline_patch.diff`；note：离线回放模式：本条记录不是 MiniMax 的真实响应，仅用于证明流程可复现。设置 MINIMAX_API_KEY 后执行 `apply --patch-source minimax` 得到真实调用记录。
- 2026-10-29T20:18:01+0800  原因：显式指定 --patch-source replay
  - patch 来源：`D:\Deep seek文件\deepseek\powercontext\minimax\sample\offline_patch.diff`；note：离线回放模式：本条记录不是 MiniMax 的真实响应，仅用于证明流程可复现。设置 MINIMAX_API_KEY 后执行 `apply --patch-source minimax` 得到真实调用记录。
