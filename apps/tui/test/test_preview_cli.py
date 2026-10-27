import asyncio
from apps.tui.src import main


def test_preview_cli_runs_native_app_without_creating_gateway(monkeypatch, tmp_path):
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not construct Gateway")

    monkeypatch.setattr(main, "_load_gateway_client", forbidden)

    class Host:
        return_code = 0

        def __init__(self, config, *, client_factory, attachments):
            self.factory = client_factory

        def run(self):
            client = asyncio.run(self.factory(None, True))
            calls.append(type(client).__name__)
            return 0

    monkeypatch.setattr(main, "IcarusTextualApp", Host)
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    assert main.run_app(["--preview"]) == 0
    assert calls == ["ReplayRuntimeService"]
