"""Validates the checked-in automation-rule.json artifact's structure
and internal consistency -- e.g. that the payload it sends carries
exactly the fields `webhook_relay.AutomationEventPayload` requires, so
the two can never quietly diverge (CLAUDE.md's "never let two briefs
[here: two artifacts] quietly diverge from each other" rule, applied to
this rule/relay pair).
"""

import json
from pathlib import Path

from issue_tracker.webhook_relay import AutomationEventPayload

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "automation-rule.json"


def _load():
    return json.loads(ARTIFACT_PATH.read_text())


def test_artifact_is_valid_json_with_expected_top_level_shape():
    doc = _load()
    assert "rule" in doc
    assert "_meta" in doc


def test_trigger_is_issue_transitioned():
    rule = _load()["rule"]
    assert rule["trigger"]["component"] == "TRIGGER"
    assert "transitioned" in rule["trigger"]["type"]


def test_condition_checks_both_status_and_opt_in_label():
    rule = _load()["rule"]
    condition = next(c for c in rule["components"] if c["component"] == "CONDITION")
    jql = condition["value"]["jql"]
    assert "status" in jql
    assert "labels" in jql
    assert "AND" in jql  # both conditions required -- neither alone is sufficient (Sec. 4.4)


def test_action_sends_web_request_carrying_the_fields_the_relay_requires():
    rule = _load()["rule"]
    action = next(c for c in rule["components"] if c["component"] == "ACTION")
    assert action["type"] == "jira.issue.outgoing.webhook"
    assert action["value"]["method"] == "POST"

    custom_data_fields = set(action["value"]["customData"])
    required_relay_fields = {f.name for f in AutomationEventPayload.__dataclass_fields__.values()}
    assert required_relay_fields.issubset(custom_data_fields), (
        "automation-rule.json's action payload must carry every field "
        "AutomationEventPayload.from_json requires, or the two have drifted apart"
    )


def test_placeholders_are_documented_in_meta():
    doc = _load()
    placeholders = doc["_meta"]["placeholders_a_human_must_fill_in"]
    rule_text = json.dumps(doc["rule"])
    for placeholder in placeholders:
        assert placeholder in rule_text, f"{placeholder} is documented as a placeholder but not used in the rule body"
