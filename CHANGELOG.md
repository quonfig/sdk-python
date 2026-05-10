# Changelog

## 0.0.13 - 2026-05-10

- **Feat: expose `variant` and `flag_metadata` on `EvaluationDetails` (qfg-9dbl).** OpenFeature-style evaluation details now surface the resolved variant name and any flag-level metadata, matching the sdk-node and sdk-ruby surfaces so OpenFeature provider code can read them without re-resolving the flag.
- **Feat: `IS_PRESENT` and `IS_NOT_PRESENT` targeting operators (qfg-7jnb.7).** Both take only `propertyName` (no `valueToMatch`). `IS_PRESENT` resolves the dotted path against the merged context and returns `True` iff the path resolves AND the value is not `None`. Type-agnostic — empty string `""`, `0`, and `False` all count as **present**; only `None` and missing keys (including missing nested paths) are absent. `IS_NOT_PRESENT` is the negation. Implemented in `Evaluator._criterion_matches` so the operator can see the dotted-path `found` flag, with `is not None` checks deliberately used instead of truthy-falsy `if not value:` (which would mis-classify `""`, `0`, and `False` as absent). Matches sdk-node, sdk-go, sdk-ruby wire behaviour. Closes the integration-test parity gap that left 7 pytest cases red since the operators landed in `integration-test-data`.
- **Chore: add ruff format CI gate.** CI now runs `ruff format --check` on every push so style drift can't sneak into a release. Part of the cross-repo formatter rollout (qfg-i5x5).
