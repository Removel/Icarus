import pytest
from pydantic import ValidationError

from apps.argus.src.config.config_loader import load_config
from apps.argus.src.config.config_model import ArgusConfig, QQConfig


def test_default_config():
    config = load_config()

    assert config.host == "127.0.0.1"
    assert config.port == 8766
    assert config.queue_capacity == 1000
    assert config.worker_count == 1


@pytest.mark.parametrize("change", [
    {"port": 0}, {"port": 65536}, {"queue_capacity": 0},
    {"worker_count": 0}, {"worker_count": 2}, {"host": ""},
    {"host": "0.0.0.0"}, {"unexpected": "value"},
])
def test_rejects_invalid_runtime_values(change):
    values = {
        "host": "127.0.0.1", "port": 8766, "log_level": "INFO",
        "queue_capacity": 1000, "worker_count": 1,
    }
    values.update(change)
    with pytest.raises(ValidationError):
        ArgusConfig.model_validate(values)


def test_bot_names_default_empty_and_accept_aliases():
    assert QQConfig().bot_names == ()
    config = QQConfig(enabled=True, bot_id="10000", workspace_path="/tmp/work",
                      gateway_url="ws://127.0.0.1:8765/rpc",
                      bot_names=["icarus", "伊卡洛斯"])
    assert config.bot_names == ("icarus", "伊卡洛斯")


@pytest.mark.parametrize("url", [
    "ws://127.0.0.1:123@remote.example/rpc", "ws://127.0.0.1:8765/rpc?x=1",
    "ws://127.0.0.1:8765/rpc#fragment", "ws://127.0.0.1/rpc",
])
def test_gateway_url_rejects_nonlocal_or_ambiguous_authority(url):
    with pytest.raises(ValidationError):
        QQConfig(enabled=True, bot_id="10000", workspace_path="/tmp/work", gateway_url=url)
