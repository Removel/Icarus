from apps.tui.src.client.gateway.ports import ClientFactory, RuntimeClient
from apps.tui.src.services.chat.operations import PreparedSession
from apps.tui.src.services.chat.session import SessionBusyError


def test_runtime_ports_import_without_app_module() -> None:
    assert RuntimeClient is not None
    assert ClientFactory is not None
    assert PreparedSession is not None
    assert issubclass(SessionBusyError, RuntimeError)
