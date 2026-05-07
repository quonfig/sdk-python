# Changelog

## 0.0.13 - 2026-05-07

- **Feat: `IS_PRESENT` and `IS_NOT_PRESENT` targeting operators (qfg-7jnb.7).** Both take only `propertyName` (no `valueToMatch`). `IS_PRESENT` resolves the dotted path against the merged context and returns `True` iff the path resolves AND the value is not `None`. Type-agnostic — empty string `""`, `0`, and `False` all count as **present**; only `None` and missing keys (including missing nested paths) are absent. `IS_NOT_PRESENT` is the negation. Implemented in `Evaluator._criterion_matches` so the operator can see the dotted-path `found` flag, with `is not None` checks deliberately used instead of truthy-falsy `if not value:` (which would mis-classify `""`, `0`, and `False` as absent). Matches sdk-node, sdk-go, sdk-ruby wire behaviour. Closes the integration-test parity gap that left 7 pytest cases red since the operators landed in `integration-test-data`.
</content>
</invoke>