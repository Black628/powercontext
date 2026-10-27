# PDF 页级证据

安装 `powercontext[pdf]` 后，可通过 pypdf 提取正文。解析器接收原始 PDF 字节和非空白文档 ID，
按顺序返回所有物理页，包括空白页。页码从 1 开始，每页携带完整原文件的 SHA-256 摘要。
支持空密码权限标记；需要非空密码的文件会被拒绝。损坏或无法完整读取的文档在返回页面之前
整体以 `ValueError` 失败。扫描图像不进行 OCR。

```python
from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document

pages = parse_pdf_document(PDFDocumentInput(document_id="年度报告", content=pdf_bytes))
```

将 `PDF_PAGE_SOURCE_DEFINITION` 注册到 `SourceDefinitionRegistry`，逐页 resolve，
再通过 `project_source_for_transport` 创建 observation。向 Runtime ingestion 注册定义清单，
把全部 observation 提交到指定 Scope，并使用保留完整投影正文及 Source 引用的 pipeline
刷新 Memory。标准 `powercontext.text-evidence@1` 投影正文为 `document_id + "\n" + page.text`，
metadata 恰好包含 `document_id`、`document_sha256`、`page_number`。

Source 身份对文档 ID、完整文件摘要和物理页码的无歧义编码计算摘要，固定为 68 个字符，
不依赖提取正文。PDF 修订会产生新的页身份，旧 Source 和 Memory citation 继续对应原修订。
物理页码不能当作报告印刷页码使用。

重新打开 Runtime 后即可检索：

```python
from powercontext.sources.pdf_pages import search_pdf_pages
from powercontext.builtin.runtime import GetMemoryEntryRequest

memory = runtime.memory.for_scope(scope_id)
citations = await search_pdf_pages(memory, "What was operating income in 2025?", limit=20)
for citation in citations:
    record = await memory.get(GetMemoryEntryRequest(citation=citation))
```

检索通过公开的 scoped `Memory.list()` 读取有效条目，使用该精确快照的真实 citation。
排序结合 BM25、财务词汇扩展、报表短语以及文档和年份匹配，最多返回 `limit` 个不同页 Source。
不返回无关记忆或合并多个 Source 的条目。空白问题和非正数上限返回空元组。
检索不调用模型、网络，不读取文件，也不依赖进程缓存。

每次查询读取并分词该 Scope 的全部正文，时间和临时内存随正文总量线性增长，适合题目中
1,151 页规模的小型报告集。更大的部署应在同样的精确引用契约下使用有界索引。
公开的 503 页自测验证全页覆盖、持久化及引用完整性；隐藏证据的召回率和词项质量门槛需要
评测方未公开的标注，不能由公开自测结果推定。

本机在官方 1 CPU、2 GB 容器中，以公开报告页面的副本组成 1,151 页 observation 进行规模检查。
原始 503 页解析约 7.6 秒，合成语料持久化及刷新约 51.3 秒。重新打开 Runtime 后，最终排序的
单次查询约 0.54–1.01 秒。这些数据只描述该合成负载，不代表隐藏集召回率，也不是通用延迟保证。

在比赛固定依赖、离线环境中运行：

```sh
make -C competition test-pdf
```

补充行为测试位于 `tests/test_pdf_pages.py` 和 `tests/e2e/test_pdf_page_retrieval.py`。
