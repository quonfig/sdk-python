"""Regression tests: tolerate explicit `null` in list-typed fields from the wire.

The api-delivery `/api/v2/configs` response sometimes serializes empty lists as
JSON `null` rather than omitting the key (e.g. a config with no env-overrides
arrives as `{"default":{"rules":null}}`). `dict.get("rules", [])` returns `None`
when the key is present-but-null, so iterating with a list-comprehension raises
`TypeError: 'NoneType' object is not iterable` and aborts parsing of the entire
batch. The fix: treat null and missing as equivalent — `data.get("rules") or []`.
"""

from quonfig.types import ConfigEnvelope, Environment, Rule, RuleSet


class TestNullListTolerance:
    def test_ruleset_from_dict_tolerates_null_rules(self):
        rs = RuleSet.from_dict({"rules": None})
        assert rs.rules == []

    def test_environment_from_dict_tolerates_null_rules(self):
        env = Environment.from_dict({"id": "e1", "rules": None})
        assert env.rules == []
        assert env.id == "e1"

    def test_rule_from_dict_tolerates_null_criteria(self):
        rule = Rule.from_dict({"criteria": None, "value": None})
        assert rule.criteria == []

    def test_envelope_with_null_rules_in_one_config_does_not_abort_batch(self):
        # Mimics the staging api-delivery shape: one config has null rules,
        # the rest are well-formed. The whole batch must still parse.
        payload = {
            "configs": [
                {"id": "", "key": "", "type": "object", "valueType": "",
                 "sendToClientSdk": False, "default": {"rules": None}},
                {"id": "1", "key": "real.config", "type": "config", "valueType": "string",
                 "sendToClientSdk": False,
                 "default": {"rules": [
                     {"criteria": [{"operator": "ALWAYS_TRUE"}],
                      "value": {"type": "string", "value": "v"}}]},
                 "environments": None},
            ],
            "meta": {"version": "1", "environment": "development"},
        }
        envelope = ConfigEnvelope.from_dict(payload)
        assert len(envelope.configs) == 2
        assert envelope.configs[1].key == "real.config"
        assert envelope.configs[1].environments == []
