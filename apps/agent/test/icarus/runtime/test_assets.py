"""Session assets are reusable infrastructure, not a persistence plugin capability."""

from importlib import import_module


def test_asset_store_operates_without_plugin_backends(tmp_path):
    assets = import_module("icarus.runtime.assets")
    identity_module = import_module("icarus.core.identity")
    runtime = assets.PersistenceRuntime(tmp_path / "data", tmp_path / "workspace")
    identity = identity_module.SessionIdentity.create(tmp_path / "workspace", "assets")
    session = assets.PersistenceSession(runtime, identity)
    image = session.import_image_bytes(b"\x89PNG\r\n\x1a\nexample")
    assert session.resolve_image(image).read_bytes() == b"\x89PNG\r\n\x1a\nexample"
    assert not hasattr(runtime, "state_store")
    assert not hasattr(runtime, "trace_writer")
    assert not hasattr(runtime, "redactor")
