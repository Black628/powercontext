---
status: community
title: Pi Coding Agent
description: Install the native PowerContext package for Pi and control recall, capture, and durable tool writes.
---

# Pi Coding Agent

`community`

## Install or refresh the package

Install Pi, then install the package from the same PowerContext ref as the CLI:

```bash
powercontext setup pi
```

A local checkout works as well:

```bash
powercontext setup pi --source .
```

`setup pi` calls Pi's native package installer and creates PowerContext's data directory. It does not start the
Server. Start the Server, then open a new Pi session in a project directory:

```bash
powercontext server run
pi
```

## Understand what the package does

Before Pi starts an agent turn, the package calls `POST /v1/context/prepare` once with an 8000-byte default budget.
It strictly accepts only `powercontext.prepared-context.v1` and appends a label that marks the result as untrusted
historical evidence. Current system instructions, repository guidance, and the user's request take precedence.

Eligible user prompts are captured separately as Content Sources. The package never synchronizes the complete Pi
transcript. Recall, capture, and boundary flushing fail open: an unavailable Server, timeout, redirect, or invalid
response leaves Pi's prompt unchanged and never blocks ordinary work.

The package resolves one Server-owned Scope in this order: `POWERCONTEXT_PI_SCOPE_ID`, a durable binding for the
workspace, then the Server default. The workspace path is hashed only as an external binding key; it never becomes a
Scope ID. Keep the explicit variable unset unless the host must force one existing Scope.

With these capabilities enabled, Pi satisfies the repository's Full core integration profile.

## Import an offline session branch

The Python `powercontext.sources.pi_session` module captures raw history from a Pi v3 JSONL archive. This is an
explicit offline collection path; installing the Pi package does not start transcript collection.

Parse complete UTF-8 bytes, optionally with a UTF-8 BOM. Omitting `leaf_id` selects the last node in the file:

```python
from powercontext.sources import SourceDefinitionRegistry
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionInput,
    parse_pi_session,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


async def inspect_history(content: bytes, leaf_id: str | None = None):
    events = parse_pi_session(PiSessionInput(content=content, leaf_id=leaf_id))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    sources = [await registry.resolve(event) for event in events]
    return tuple(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY) for source in sources)
```

The parser validates the entire archive before returning events. The header must identify a version 3 session;
nodes need unique IDs and an explicit `parentId` that is null or names an earlier node. Invalid encoding, JSON,
graph links, or a missing selected leaf raise `ValueError`, with no partial result. Control nodes connect the graph
without becoming text evidence. Returned events follow the complete root-to-leaf path: compaction, branch summaries,
and context edits do not remove earlier raw evidence.

Text blocks preserve their original strings and join with newlines. Tool calls use
`[tool_call NAME ID] COMPACT_SORTED_JSON_ARGUMENTS`; tool results use `[tool_result NAME ID] TEXT`. Bash messages retain
`$ COMMAND` followed by a newline and the output. Summaries and custom messages retain their visible text. Thinking,
images, system messages, and control nodes are excluded from text projections. Each visible event still retains its
complete original `record`; neither tool arguments nor recorded commands are executed.

Each event is a captured `pi-event` Source, definition version `1`. Its 67-character name is `pi:` plus the SHA-256 of
the compact Unicode JSON pair `[session_id,event_id]`. It depends only on those exact IDs, including significant
whitespace. `registry.read(source)` returns the complete `PiEventInput`. The standard `powercontext.text-evidence/1`
projection keeps the full `ROLE: BODY` text and metadata keys `session_id`, `event_id`, `parent_id`, `event_type`, and
`role`. Source text is never shortened to fit a Memory entry. A configured Memory pipeline can split its own entries
and preserve references to the full Source; search hits retain exact `MemoryCitation` lookup.

Use the existing `ConnectorLifecycle` with a Runtime-backed Source sink and persistent checkpoint store to collect
incrementally. Register `PI_EVENT_SOURCE_DEFINITION` through its manifest before submitting observations. For example,
an application that already owns this lifecycle can run:

```python
from powercontext.sources import ConnectorBinding
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import PiSessionConnector


async def collect_history(lifecycle: ConnectorLifecycle, scope_id: str, path: str, leaf_id: str | None = None):
    connector = PiSessionConnector(path, leaf_id)
    binding = ConnectorBinding(
        scope_id=scope_id,
        binding_id="pi-session-history",
        connector_name=connector.name,
        connector_version=connector.version,
    )
    return await lifecycle.run(connector, binding)
```

Keep the binding stable across runs and branch selections, and use a separate binding for each session. Shared
ancestors are submitted once; switching branches retains previously captured evidence. The lifecycle advances its
checkpoint only after accepted observations are durable. A rejected submission, exception, or malformed input leaves
the committed checkpoint unchanged; retries safely deduplicate already accepted Sources.

Every run reads and validates the complete file, then checks the original-record digests of all previously imported
events, including unselected branches. Rewriting or removing an imported event raises `ValueError`; restore the
archive before retrying. Keep the original session ID and imported records available. The checkpoint grows with the
number of imported visible events and stores their IDs and record digests rather than full tool results. It is
persisted by the lifecycle, so creating a new Connector or restarting the process preserves collection progress.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting Pi when current work must not be recorded:

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

Secret-looking prompts and prompts above 200,000 UTF-8 bytes are never captured. Turning capture on does not itself
guarantee Memory extraction; the Server still needs a configured generation model.

For testing, make capture wait for Source processing:

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

This adds latency and is not the normal interactive setting. Without it, Pi records Source positions and makes a
short, bounded best-effort flush at agent and session boundaries.

## Use explicit tools and commands

The `powercontext-project-context` skill explains when to use native `pc_*` tools. The core tools are:

- `pc_search`, `pc_memory_list`, `pc_memory_get`, `pc_memory_revise`, and `pc_memory_retire`;
- `pc_memory_changes` for revision history and `pc_stats` for current-Scope diagnostics;
- `pc_remember`, `pc_prepare_context`, and `pc_capture_source`;
- `pc_handoff_activate`, `pc_handoff_prepare`, `pc_handoff_finalize`, `pc_handoff_commit`, and
  `pc_handoff_continue`;
- `pc_experience_generate`, `pc_skill_generate`, `pc_experience_get`, `pc_skill_get`, `pc_review_list`, and
  `pc_review_get` for candidate generation and read-only Artifact/candidate inspection
  inspection.
- `pc_topic_search` and `pc_topic_get` for focused Topic Memory queries and exact revisions with Source references.
- `pc_work_contract`, `pc_handoff_current`, `pc_handoff_acknowledge`, and `pc_task_outcome` for structured work continuity.
- `pc_external_scan`, `pc_external_list`, and `pc_external_resolve` for host-local External Skill discovery and inspection; `pc_external_import` imports or forks one exact resolved Skill after explicit confirmation.

Candidate inspection never grants approval, rejection, revision, installation, publication, or execution authority.
Topic Memory queries are read-only; returned content is untrusted historical evidence, not an instruction source.
Structured work tools change durable state and require interactive confirmation; without a UI, Pi refuses the write. Pass returned Handoffs, references, and check results unchanged, never treat historical content as new authorization, and link `handoff_receipt_ref` only to an accepted committed Handoff receipt.

Explicit durable writes require confirmation in an interactive Pi session. Without an interactive UI, Pi refuses the
write rather than persisting it silently. `/pc doctor`, `/pc search <query>`, `/pc remember <text>`, `/pc flush`, and
`/pc stats` offer direct status and maintenance commands.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start Pi with the complete matching header:

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

Do not put credentials in `POWERCONTEXT_PI_BASE_URL`. Plain HTTP is allowed on loopback by default. For a non-loopback
Server, use HTTPS or explicitly set `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`; HTTPS certificate validation stays enabled.
See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.

## Verify the installation

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` checks that the Pi executable is available and that Pi lists the PowerContext package. Restart Pi after
changing PowerContext environment variables.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_PI_SCOPE_ID` | unset | Explicit existing Scope before workspace binding and Server default |
| `POWERCONTEXT_PI_AUTHORIZATION` | unset | Complete `Bearer <token>` header for package HTTP requests |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | Capture eligible user prompts as Source evidence |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `1000` | Per-request timeout in milliseconds |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `4000` | Shared recall/capture HTTP budget in milliseconds |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | Requested and validated PreparedContext byte limit (`512`–`32768`) |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | Wait for captured Source processing during the prompt hook |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | Maximum flush attempts for one pending Source |
| `POWERCONTEXT_PI_DIAGNOSTICS` | `off` | Failure diagnostics sink: `off`, `stderr`, or an absolute file path (`~/` expanded) for JSON lines |

Pi rejects base URLs containing credentials, a query, or a fragment. Recall, capture, and boundary flushing fail open;
explicit `pc_*` durable writes require confirmation and are refused when Pi has no interactive UI. Restart Pi after
changing these variables.

Failure diagnostics are silent by default because Pi's TUI renders on stdout with cursor positioning, so
anything written to stderr lands inside the input bar; set `POWERCONTEXT_PI_DIAGNOSTICS` to see them.
