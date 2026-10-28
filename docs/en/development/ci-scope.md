# CI scope selection

Continuous integration uses `.github/ci-paths.toml` to select task groups before allocating their runners. Each participating workflow calls `ci-changes.yml` with its own filename. A documentation change selects website and documentation-contract checks; an isolated host plugin selects its existing Python regressions and host checks. Unrelated upgrade, worker observability, installation-role, Desktop and diagnostic matrices are skipped.

The lightweight scope jobs, Main quality checks, license check and upgrade result gate can still appear on an unrelated change. A skipped upgrade matrix is an explicit not-required result, not evidence of upgrade acceptance. A selected matrix must succeed; failure, cancellation or unexpected skipping does not pass its gate. Release publication continues to call the installed-release acceptance workflow directly and does not use path selection to bypass acceptance.

## Comparison baseline

- Pull requests compare the merge base with the complete PR head, including all commits. Unrelated changes on the base branch are excluded.
- Pushes compare the current commit with a successful ancestor from the same workflow and branch. The selector examines up to 100 successful push runs through the read-only Actions API. An ancestor check rejects rewritten histories. Cancelled or failed runs do not become baselines, so their unverified changes remain in the next comparison.
- Missing history, unavailable API access, malformed metadata or a failed diff selects every task group. A new workflow with no successful baseline therefore starts with full validation.
- Scheduled and manual runs select every task group available in that workflow. Workflow-specific manual inputs, such as Desktop installer diagnostics or the recall-only harness, retain their own restrictions.

The selector reads Git history rather than GitHub's truncated path-filter list. Rename detection is disabled so both the removed and added paths participate. Branch names are encoded as API query parameters; commit identities must be full hexadecimal SHAs. The scope summary records the comparison range, selected groups and fallback reason.

## Maintaining ownership rules

`[suites]` assigns files to individual task groups. `[shared.<name>]` assigns shared inputs to multiple consumers. A workflow change selects its own consumers, and a composite action change selects workflows that use that action. The selection configuration, selector, shared test fixtures and shared environment setup retain full coverage. Unknown paths also select every group until their ownership is declared.

Shared Python runtime, API and root dependency changes conservatively exercise runtime consumers, backend and upgrade acceptance, worker observability and packaging. Historical schema capture and the SeekDB lock diagnostic have separate inputs; a routine runtime change does not launch those development tools. Python-version and backend matrices are preserved when their groups are selected.

When adding an input, include its consumers outside the directory that owns it. For example, Pi changes also select ZCode cross-host acceptance; a migration-related test must select the corresponding real-backend acceptance. Add a selection regression in `tests/test_ci_changes.py`. Verify moves, deletions, unavailable diffs and accumulated push changes when altering comparison behavior.

Run focused validation with:

```sh
uv run pytest -q tests/test_ci_changes.py
uv run python scripts/check_workflow_actions.py .github/workflows .github/actions
```

Local scope tests exercise real Git histories and simulated Actions run metadata. They do not execute the GitHub scheduler or establish native-host/backend acceptance. Measure queue wait, runner minutes and launched job counts after deployment before claiming a throughput improvement.
