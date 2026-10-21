"""CLI composition tests without opening sockets or touching real settings."""
from apps.argus.src.core.config import ArgusConfig


def test_lifecycle_host_port_arguments_reach_uvicorn(monkeypatch):
    from apps.argus.src import main
    config = ArgusConfig()
    calls = []
    monkeypatch.setattr(main, 'load_config', lambda: config)
    monkeypatch.setattr(main, 'create_app', lambda value: value)
    monkeypatch.setattr(main.uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    main.main(['--host', '127.0.0.1', '--port', '9876'])
    assert calls[0][0].port == 9876
    assert calls[0][1] == {'host': '127.0.0.1', 'port': 9876}
