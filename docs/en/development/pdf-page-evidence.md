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
