# Evidence and plan reproduction

This stage is official-document research and an unexecuted acquisition plan, not a backtest. No vendor subscription, entitlement, connection or paid market data was validated.

- The provider manifests list public URLs, retrieval times, statuses and private-body hashes; findings contain short attributed quotations and limitations. Full source bodies are private and are not bundled. Some live links and SDK main branches can change; use the recorded body hashes for exact captured-source identity. Hashes alone do not provide public access to those bodies.
- `historical-request-plan.json.gz` preserves all 800 rows from the prior publication; decompress with Python gzip or a standard gzip reader. The prior sanitized contract source is included as `prior-selected-contracts.json.gz`, byte-identical to prior `selected-contracts.json` after decompression.
- Independent audit scripts under `audit/` reconstruct scope and UTC/DST windows, check preserved rows and source evidence, and recompute the peer arithmetic. They may require prior stage files and private captured source bodies. They are audit methods, not vendor download tools.
- `provider-adaptation-plan.json` expressly leaves endpoint mapping, time semantics, fields, cost, rights and transport issues unresolved. Do not convert the generic event-time plan into API requests without resolving them. Do not bypass missing conditions, quote size units or historical identity requirements.
- Public prices are not the user's contractual charges or permission. There is no zero-cost paid-data authorization hidden in the study. User credentials must stay in secure environment settings for the selected official destination, never in this report or another vendor's endpoint.
- `build_peer_reply.py` only reproduces the clearly hypothetical quoted-spread arithmetic. `assemble_stage.py` assembles interpretation and plans from frozen findings; rerunning it changes generated timestamps and does not prove new market data availability.

The environment already supports this standard-library document/audit workflow; no installation, service startup or saved environment configuration change was needed. Existing checkouts and prior artifacts were preserved; no worktree was created. Data-provider integration remains untested and separate from this completed research stage.
