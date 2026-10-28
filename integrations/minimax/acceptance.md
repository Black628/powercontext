# MiniMax native qualification

## Environment

- Date: 2026-10-28.
- Platform: Linux x86_64.
- Node.js: 22.23.3.
- Host: official npm `@minimax-ai/code@0.6.9`, installed into an isolated temporary prefix.
- PowerContext: working-tree CLI and Server, with SQLite and a synthetic local Bearer credential.
- Targets: isolated temporary data directories, including spaces and Chinese characters. No personal MiniMax account, provider credentials, or user profile is used.

## Run

Install the CLI into an isolated prefix following the official [installation instructions](https://github.com/MiniMax-AI/minimax-code/blob/main/docs/installation.md), then run:

```bash
POWERCONTEXT_MINIMAX_ACCEPTANCE_COMMAND=/absolute/path/to/mcode \
  uv run python -m pytest tests/e2e/test_minimax_host.py -q
```

The tests start a real PowerContext HTTP Server and invoke the public setup/doctor commands with the real MiniMax executable. The loopback listener must be permitted by the execution environment. Without the explicit launcher environment variable, these opt-in tests skip; a skip is not qualification.

## Verified behavior

Nine scenarios pass against the real CLI: explicit destination, `MINIMAX_DATA_DIR`, `MAVIS_DATA_DIR`, invalid-package/authentication diagnostics, actual private and plugin MCP calls, disabled private overrides with and without Authorization, and diagnosis of an uninstalled target. They verify repeated setup, the native loader's enabled local plugin record, required Skill/MCP counts, saved-target reuse, native disablement and its preservation during setup, omission of an invalid native manifest, and rejection of a wrong private Bearer header by the real Server.

Native discovery runs in a disposable profile containing the installed package and its enablement state. The real MiniMax profile is unchanged byte-for-byte across doctor, both before and after disabling the plugin through the real CLI. Diagnosing an uninstalled target does not create that target. Only the selected plugin's enablement is carried into the diagnostic runtime; real sessions and provider configuration are not imported.

The disabled-private scenarios configure the incoming package to use an anonymous loopback Server while retaining a disabled private HTTPS endpoint. First and repeated setup preserve every byte of the private file, including a dormant credential when present. Real native discovery and CLI Server probes succeed against the bundled endpoint, and doctor leaves the file unchanged.

The native tool scenarios first verify that setup synchronized the private and installed plugin MCP URLs and Authorization headers. Each case then isolates one connection before running `mcode exec` with a local OpenAI-compatible inference fixture:

| Case | Private MCP configuration | Native plugin state |
| --- | --- | --- |
| `private-mcp` | Retain the enabled `powercontext` entry | Disable `powercontext@local` through the real CLI |
| `plugin-mcp` | Remove the `powercontext` entry from the temporary profile | Keep `powercontext@local` enabled |

The real plugin list must confirm the expected enablement state. The inference fixture selects the single discovered `list_scopes` tool, either directly or through `tool_search` and `mcp_invoke`. Missing or ambiguous tools fail qualification. Tool aliases are not used to identify the route; both isolated cases exposed `mcp__powercontext__list_scopes` in this run.

Both cases return the seeded Scope ID and title in the result associated with the emitted MCP tool-call ID. The result parser accepts MiniMax's concatenated MCP text blocks. The plugin case also verifies that the installed Skill reaches the model; the private case does not require a Skill from its disabled plugin. This establishes authenticated MCP calls through each isolated native path using controlled inference, without an external model provider.

The package and connection checks use actual files, subprocesses, HTTP requests, and SQLite. Unit tests in `tests/test_cli_minimax.py` separately use simulated CLI output to cover parsing failures and installation recovery; those simulations are not native qualification.

## Limits

The plugin list reports resource counts rather than resource identities or an active MCP connection. CLI Server-info access is a separate HTTP probe; it does not establish a MiniMax MCP session. Desktop discovery, desktop refresh behavior, macOS, Windows, other CLI versions, and external model-provider calls require separate acceptance. Do not infer those capabilities from a successful Linux CLI run.
