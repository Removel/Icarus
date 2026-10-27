from apps.tui.src.gateway_client.ports import ClientFactory, RuntimeClient
from apps.tui.src.services.types import PreparedSession, SessionBusyError


def test_runtime_ports_import_without_app_module() -> None:
    assert RuntimeClient is not None
    assert ClientFactory is not None
    assert PreparedSession is not None
    assert issubclass(SessionBusyError, RuntimeError)
