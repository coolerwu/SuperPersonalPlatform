"""Default-value contracts for RuntimeSettings.

Read the field defaults instead of instantiating Settings with no env, so a
developer's local ``.env`` cannot change what this test asserts.
"""

from app.config import Settings


def test_inbound_batching_defaults():
    fields = Settings.model_fields
    assert fields["channel_inbound_batch_seconds"].default == 6.0
    assert fields["channel_inbound_batch_max_seconds"].default is None
