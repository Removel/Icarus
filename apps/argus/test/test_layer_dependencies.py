"""Layer dependencies are a behavior enforced at source integration time."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src'


def test_shared_modules_do_not_depend_on_sources_or_legacy_pipeline():
    forbidden = {'clients', 'config', 'connector', 'dao', 'entity', 'evidence', 'handler', 'service', 'strategy', 'utils'}
    for path in list((ROOT / 'common').rglob('*.py')) + list((ROOT / 'core').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                parts = (node.module or '').split('.')
                assert 'surfaces' not in parts, path
                assert 'decision_graph' not in parts, path
                if (node.module or '').startswith('apps.argus.src.'):
                    assert parts[3] not in forbidden, path
    for path in (ROOT / 'common/api').glob('*.py'):
        assert not any(isinstance(node, ast.ImportFrom) and 'models' in (node.module or '').split('.')
                       for node in ast.walk(ast.parse(path.read_text())))
