"""A test that always fails, standing in for a pre-existing failure that
is unrelated to whatever change is currently under verification. Used by
Layer 1's test to prove such a failure is flagged, not papered over or
silently skipped."""


def test_known_preexisting_bug():
    assert False, "known pre-existing failure, unrelated to the change under verification"
