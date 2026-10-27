# 知新 / Recall Coach — Qoder Sites 站点文案

> 把下面每个 `##` 小节作为站点的一个区块发布。发布前替换 `{{...}}` 占位符。
> 站点可见范围务必设为「任何人都可访问」。

## 首屏

# 知新 / Recall Coach

**一个会用「记忆」复习你的学习教练。**
资料检索交给 seekdb，学习者画像与复习上下文交给 PowerContext——每一轮复习都带着上一轮的记忆开始。

[立即体验]({{DEMO_URL}}) · [代码仓库]({{REPO_URL}}) · [3 分钟演示视频]({{VIDEO_URL}})

## 解决的问题

复习的痛点不在「没有资料」，而在资料与学习者之间缺少一条连续的线：

- 笔记、文献、错题散落在不同文件夹，复习时找不到相关原文；
- 每次打开学习工具都像第一次见你：不知道你哪一章薄弱、上次为什么错；
- 复习完就结束，没有东西沉淀到下一轮，同一个知识点反复「重新认识」。

知新把「导入资料 → 带记忆开始 → 出题作答 → 写回记忆 → 下一轮更懂你」做成一个闭环。

## 核心功能

1. **资料导入与切段**：粘贴或上传 `.txt/.md/.json`，自动按课程切分，写入 seekdb（`VECTOR(1024)` + 全文列 + HNSW 索引）。
2. **混合检索**：向量通道 `ORDER BY l2_distance(...) APPROXIMATE` 与全文通道 `MATCH(...) AGAINST(...)` 各取候选，用 RRF 融合；界面逐条展示 SQL、参数、耗时。
3. **带记忆开始**：开始复习前先向 PowerContext `context/prepare` 取上下文——薄弱点、偏好、进度——再用它影响检索与提示。
4. **出题与评分**：从召回原文生成选择题，作答后给出正确答案、相关原文、掌握度变化；连续答错的卡片在之后几轮优先出现。
5. **记忆沉淀**：答错/「有点难」写入 weakness/misconception，偏好写入 preference，里程碑写入 progress；支持修正与失效。
6. **可核对的执行记录**：右栏「执行记录」分 seekdb（SQL/行数/耗时）与 PowerContext（HTTP/请求/响应）两栏，评审可直接对照。

## 技术分工

| 技术 | 分工 | 可核对证据 |
| --- | --- | --- |
| seekdb | 唯一资料存储 + 向量/全文混合检索；掌握度与复习次数回写 | `/api/evidence` 的 `[seekdb]` 记录、界面 SQL 面板 |
| PowerContext | 学习者记忆：`memory/remember`、`memory/search`、`context/prepare`、`entries/list`、`revise`/`retire` | `/api/evidence` 的 `[powercontext]` 记录、记忆面板 |

两者不互相替代：检索结果只有在作答之后才可能变成记忆，而记忆只影响下一轮检索与提示。

## 演示

- **截图**：`{{SCREENSHOT_HOME}}`（首页与资料库）、`{{SCREENSHOT_SESSION}}`（复习卡与上下文）、`{{SCREENSHOT_EVIDENCE}}`（执行记录）。
- **录屏**：`{{VIDEO_URL}}`（3 分钟：导入 → 开始复习 → 答错 → 记忆出现 → 第二轮上下文复用）。
- **本地体验**：见仓库 `README.md` 第 5 节；一条命令启动后在浏览器打开 `http://127.0.0.1:8848`。

## 使用效果

> 填写真实使用数据，附来源（访问统计截图、使用日志、问卷导出）。没有数据时可删除本节。

- 使用人数：`{{USERS}}`；累计复习轮次：`{{ROUNDS}}`；持续使用：`{{DAYS}}`
- 提效对比：整理一轮课程复习从 `{{BEFORE}}` 缩短到 `{{AFTER}}`
- 用户反馈：`{{FEEDBACK}}`（已隐去个人信息）

## 开源与复用

- 代码仓库：{{REPO_URL}}，主目录 `qoder/`
- 嵌入式 PowerContext Server 启动方式参考该仓库 `examples/jupyter/_tutorial.py`
- seekdb 混合检索写法参考 `src/powercontext/builtin/persistence/seekdb/`
- 示例资料为本项目撰写，仅用于演示
