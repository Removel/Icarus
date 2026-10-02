import ast
from pathlib import Path


def test_layer_imports_obey_application_boundaries():
    source = Path(__file__).resolve().parents[2] / 'src'
    forbidden = {
        'decision_graph': {'clients','config','connector','dao','entity','evidence','handler','service','strategy','utils'},
        'entity': {'dao','service','evidence','strategy','connector','handler'},
        'dao': {'service','evidence','strategy','connector','handler'},
        'evidence': {'strategy','service','connector','handler'},
        'strategy': {'evidence','service','dao','connector','handler'},
        'service': {'connector','handler'},
        'connector': {'service','dao','evidence','strategy','queue','worker'},
    }
    violations = []
    for path in source.rglob('*.py'):
        relative = path.relative_to(source)
        layer = relative.parts[0]
        package = ['apps','argus','src',*relative.parts[:-1]]
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node,ast.ImportFrom):
                continue
            if node.level:
                base = package[:len(package)-node.level+1]
                target = base+(node.module or '').split('.')
            else:
                target = (node.module or '').split('.')
            if target[:3] != ['apps','argus','src'] or len(target)<4:
                continue
            dependency = target[3]
            if dependency in forbidden.get(layer,set()):
                violations.append(f'{relative}:{node.lineno} -> {dependency}')
    assert violations == []
