# PDF page evidence

Install `powercontext[pdf]` to extract text with pypdf. The parser accepts original
PDF bytes and a nonblank document ID. It returns every physical page in order,
including blank pages, with 1-based page numbers and the SHA-256 of the complete
original file. It supports empty-password permissions and rejects documents that
require a password. Invalid or incompletely readable documents fail with
`ValueError` before any pages are returned. The parser does not perform OCR on
scanned pages.

```python
from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document

pages = parse_pdf_document(PDFDocumentInput(document_id="annual-report", content=pdf_bytes))
```

Register `PDF_PAGE_SOURCE_DEFINITION` in a `SourceDefinitionRegistry`, resolve each
page, and use `project_source_for_transport` to create its observation. Register
the definition manifest with Runtime ingestion, submit all observations to the
chosen scope, and flush its Memory with a pipeline preserving each observation's
complete text and Source reference. The standard `powercontext.text-evidence@1`
projection contains `document_id + "\n" + page.text`. Its metadata contains exactly
`document_id`, `document_sha256`, and `page_number`.

Source identities hash an unambiguous encoding of the document ID, original file
digest, and physical page number. They are bounded to 68 characters and do not
depend on extracted text. A changed PDF produces new page identities; old Sources
and Memory citations retain their original revision. PDF physical pages must not
be confused with printed page labels.

After reopening Runtime, call:

```python
from powercontext.sources.pdf_pages import search_pdf_pages
from powercontext.builtin.runtime import GetMemoryEntryRequest

memory = runtime.memory.for_scope(scope_id)
citations = await search_pdf_pages(memory, "What was operating income in 2025?", limit=20)
for citation in citations:
    record = await memory.get(GetMemoryEntryRequest(citation=citation))
```

Retrieval reads active entries through the public scoped `Memory.list()` API. It
uses the exact snapshot's citations and ranks complete page text with BM25,
financial vocabulary expansion, statement phrases, and document/year matching.
It returns at most `limit` distinct page Sources, ignoring unrelated memories and
entries combining multiple Sources. Empty questions and nonpositive limits return
an empty tuple. It uses no models, network, file reads, or persistent search cache.

Each query reads and tokenizes the scoped corpus. Time and transient memory grow
linearly with corpus text size; this is intended for small report collections
such as the challenge's 1,151 pages. Larger deployments should use a bounded index
behind the same citation contract. The public 503-page selftest verifies coverage,
persistence, and citation integrity. Hidden evidence recall and token-quality
thresholds require the evaluator's withheld annotations and are not established
by that selftest.

A local scale check under the official 1-CPU, 2-GB container used 1,151 page
observations made from replicas of the public report. Parsing the original 503
pages took 7.6 seconds; persisting and flushing the synthetic corpus took 51.3
seconds. After reopening Runtime, queries with the final ranking took about
0.54–1.01 seconds each. These measurements characterize this synthetic workload;
they do not measure hidden-set recall or establish a general latency guarantee.

Validation with the competition's fixed offline dependencies:

```sh
make -C competition test-pdf
```

Additional behavior tests live in `tests/test_pdf_pages.py` and
`tests/e2e/test_pdf_page_retrieval.py`.

## Public quality validation

The implementation at commit `63e069d46672c9e6e4d1d17f544ad54d4b0c3d37` was
evaluated on 2026-10-27 against the complete public PDF and all five published
questions. The reference was restricted to those questions' eight evidence
annotations, covering six distinct physical pages, from
[FinanceBench's fixed public dataset](https://github.com/patronus-ai/financebench/blob/cc39aeb4afdf33909ee1412188bf89035950c2eb/data/financebench_open_source.jsonl).
Answers and explanations were excluded. Dataset licensing remains CC BY-NC 4.0;
the original report retains its respective rights.

Inference used the official offline 1-CPU, 2-GB container with read-only candidate
sources. Annotation files were not mounted in that container. All 503 pages were
ingested into real SQLite Runtime; retrieval ran after closing and reopening
Runtime, and every returned citation was read back through `memory.get`.
An external scorer compared the frozen candidate's outputs with the references.

Both texts were lowercased and tokenized with `[a-z]+|\d+`. Token overlap used
multiset counts. Reference page numbers were converted from zero-based numbering
to 1-based physical pages. Recall@20 summed each question's distinct matching
document-page pairs and divided by the sum of distinct reference pages.

| Metric | Required | Observed | Status |
| --- | --- | --- | --- |
| Per-page token counts against platform frozen counts | 90%–110% | Platform reference unavailable | Not verified |
| Per-page token counts against independent Poppler extraction | 90%–110% | 503/503 pages within range | Cross-check passed; not the platform reference |
| Token counts against annotated full-page text | 90%–110% | 6/6 pages within range; ratios 1.0000–1.0139 | Annotated pages passed |
| Evidence-token recall | ≥0.95 per annotation | Minimum 0.9962121212; all 8 annotations passed | Public split passed |
| Annotated full-page token F1 | ≥0.95 per page | Minimum 0.9793459552; all 8 annotation comparisons passed | Public split passed |
| Evidence-page Recall@20 | ≥0.70 for the split | 7/8 = 0.875 | Public split passed |

The independent full-document reference used Poppler `pdftotext 25.02.0` with
`-layout -enc UTF-8`. It cannot establish agreement with the platform's undistributed
frozen counts. Question `financebench_id_01009` retrieved one of its two reference
pages; the other four questions retrieved all their reference pages. The aggregate
7/8 meets the split-level threshold but does not mean every question has complete
recall. The withheld split has not been evaluated.
