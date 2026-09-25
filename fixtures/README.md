# Fixtures

Owner: M2 (Claude.md sections 7-8).

Three golden example responses go here, written during the Day 1 team call,
before anyone else's real logic exists:

1. `normal.json` — a normal, fully-matched answer (real catalog deeplinks).
2. `dummy_positive.json` — an answer using the `bixby://dummy_positive` deeplink.
3. `critical.json` — a `critical` action with null deeplinks.

M2 writes these because M2 owns the validator, so the fixtures should
exercise the rules the validator actually checks. Everyone else codes
against these files until the real pipeline is wired (Claude.md Day 2).
