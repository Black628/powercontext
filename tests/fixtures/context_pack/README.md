# QMSum context transmission fixture

The meeting text and speakers are unchanged excerpts from
[`Yale-LILY/QMSum`, commit `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`](https://github.com/Yale-LILY/QMSum/tree/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e).
The upstream license is MIT; see `LICENSE` in this directory.

Source: `data/ALL/test/ES2004a.json`.
Source SHA-256: `911e56db0d8482f1e730bae0a8fcf0ab0095c94abb000ba0516e975fa6e48b31`.

The fixture takes the first twelve turns of 120–900 UTF-8 bytes from turn index 131 onward, after the warm-up exercise.
It retains the original speaker, turn index, and complete text, in transcript order. Source integrity is checked when
preparing the fixture; tests consume the checked-in offline JSON without network access.

The native Runtime measurement starts with eight turns and performs forty ordinary/incremental prepare comparisons.
It appends one further turn before zero-based rounds 8, 16, 24, and 32. Queries switch every ten rounds through `remote`,
`price`, `market`, and `control`, with an 8192-byte prepare budget. These are explicit measurement controls, not real user
request logs or the organizer's hidden measurement sequence.

Each round serializes the outgoing complete pack or delta and reconstructs it from receiver-owned wire data. The test
compares the exact reconstructed bytes to ordinary prepare, rejects empty measurement rounds, requires at least four
distinct contexts, and measures the first full pack and all subsequent protocol bytes. It reports unencoded full text,
full v1 packets, and independent per-round `gzip.compress(compresslevel=9, mtime=0)` totals. Only savings against raw
full text have a 45% threshold; negative savings against gzip are retained as observed.
