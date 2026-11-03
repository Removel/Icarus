import pytest
from agent_app.argus_protocol import WakeRequest


def wake(**changes):
    return dict(channel_id='channel', wake_id='wake', binding_id='binding', channel_version=1,
                session_id='session', session_key='slot', items=[dict(id='obs', source_key='qq:1', message_id='m',
                    kind='message.observed', revision=1, observed_at='2026-11-02T00:00:00Z', content={'text': 'fact'}, detail={})],
                argus_url='http://argus', channel_snapshot=dict(id='channel', title='task', goal='track', progress='',
                    lifecycle='active', version=1, source_keys=['qq:1'], rule_source=''),
                rule_sdk={'version': 2}, **changes)


def test_wake_requires_matching_snapshot_identity_and_version():
    valid = wake()
    assert WakeRequest.model_validate(valid).channel_snapshot.id == 'channel'
    for fields in ({'id': 'other'}, {'version': 2}, {'runtime': {'binding': 'secret'}}):
        with pytest.raises(ValueError):
            WakeRequest.model_validate(valid | {'channel_snapshot': valid['channel_snapshot'] | fields})


def test_snapshot_is_required_and_bounded():
    valid = wake()
    del valid['channel_snapshot']
    with pytest.raises(ValueError):
        WakeRequest.model_validate(valid)
    valid = wake()
    valid['channel_snapshot']['rule_source'] = '界' * 11000
    with pytest.raises(ValueError):
        WakeRequest.model_validate(valid)
