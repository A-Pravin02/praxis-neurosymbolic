# Parser stability — 20 questions × 5 regenerations

Greedy decoding at temperature 0. `max_tokens` varied over 220–224 so every call misses the server's completion cache and regenerates; the budget is far above the ~40 tokens a reply needs, so nothing is truncated.

**20/20 questions gave the same (goal, request) every time (1.000).**

0 call(s) were dropped by the provider after retries and are excluded: a call the server never answered is not a reading, and counting it as a different one would report an overloaded server as an unstable parser.

No question varied. On this sample the parser is deterministic, so a difference between two runs is a difference in the system and not in the decoding.

Scope of the check: these calls are sequential from one client, so a prompt is unlikely to be batched with the same neighbours a full benchmark run would give it. Batch composition is the most likely source of nondeterminism in a batched 4-bit server, and this sample does not exercise it. A stable result here therefore bounds the problem rather than ruling it out.