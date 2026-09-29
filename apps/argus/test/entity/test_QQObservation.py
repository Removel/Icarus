from dataclasses import FrozenInstanceError

import pytest

from apps.argus.src.entity.QQObservation import QQObservation
from apps.argus.src.entity.observation import Observation
from apps.argus.src.handler.QQHandler import QQHandler


def test_qq_observation_inherits_immutable_common_facts():
    observation = QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 300,
        "message_type": "private", "user_id": 7, "message": "你好",
    })
    assert isinstance(observation, Observation)
    assert isinstance(observation, QQObservation)
    with pytest.raises(FrozenInstanceError):
        observation.mentions_bot = True
