"""CLI network boundaries keep ordinary APIs loopback while service routes are separate."""

import pytest


def test_gateway_rejects_non_loopback_ordinary_listener_and_partial_service_configuration(monkeypatch):
    from apps.gateway.src.main import main
    monkeypatch.setattr('apps.gateway.src.main.uvicorn.run', lambda *args, **kwargs: None)
    with pytest.raises(SystemExit):
        main(['--host', '0.0.0.0'])
    with pytest.raises(SystemExit):
        main(['--service-host', '100.70.8.2'])
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','fixture')
    monkeypatch.setattr('apps.gateway.src.main.asyncio.run', lambda operation: operation.close())
    with pytest.raises(SystemExit):
        main(['--service-host','0.0.0.0','--service-port','9000'])
    with pytest.raises(SystemExit):
        main(['--service-host','::','--service-port','9000'])
