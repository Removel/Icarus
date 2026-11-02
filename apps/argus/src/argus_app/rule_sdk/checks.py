"""Static compilation and decision-only capability policy. Never loads candidate code."""
from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from hashlib import sha256
from .safe_modules import EXPORTS

POLICY_VERSION = 'decision-python-v2.1'
SAFE_IMPORTS = frozenset(EXPORTS)
FORBIDDEN_NAMES = frozenset({
    'eval', 'exec', 'compile', '__import__', 'open', 'input', 'breakpoint',
    'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars', 'dir',
    'help', 'exit', 'quit', 'memoryview',
})


@dataclass(frozen=True)
class RuleDiagnostic:
    code: str
    message: str
    line: int
    column: int
    stage: str


@dataclass(frozen=True)
class RuleCheckResult:
    passed: bool
    source_hash: str
    policy_version: str
    diagnostics: tuple[RuleDiagnostic, ...]

    def model_dump(self):
        return asdict(self)


def _literal(node):
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_literal(x) for x in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _literal(k) and _literal(v) for k, v in zip(node.keys, node.values))
    if isinstance(node, (ast.UnaryOp, ast.BinOp)):
        return all(_literal(x) for x in ast.iter_child_nodes(node) if not isinstance(x, (ast.operator, ast.unaryop)))
    return False


def check_source(source: str) -> RuleCheckResult:
    diagnostics = []
    digest = sha256(source.encode()).hexdigest() if isinstance(source, str) else ''
    def reject(code, message, node=None, stage='policy'):
        if len(diagnostics) < 32:
            diagnostics.append(RuleDiagnostic(code, message, getattr(node, 'lineno', 1) or 1,
                                               getattr(node, 'col_offset', 0) or 0, stage))
    def report():
        return RuleCheckResult(not diagnostics, digest, POLICY_VERSION, tuple(diagnostics))
    if not isinstance(source, str) or len(source.encode()) > 32768:
        reject('source_limit', 'Source must be UTF-8 Python of at most 32768 bytes', stage='compile')
        return report()
    try:
        tree = ast.parse(source)
        compile(tree, '<argus-rule>', 'exec')
    except (SyntaxError, ValueError, RecursionError) as exc:
        reject('syntax_error', str(exc)[:512], exc, 'compile')
        return report()
    nodes = list(ast.walk(tree))
    if len(nodes) > 4000:
        reject('ast_limit', 'Source exceeds 4000 AST nodes', stage='compile')
        return report()
    mains = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'main']
    if len(mains) != 1:
        reject('entry_point', 'Declare exactly one synchronous main(event, channel)', stage='compile')
    else:
        fn = mains[0]
        if ([a.arg for a in fn.args.args] != ['event', 'channel'] or fn.args.defaults or fn.args.kw_defaults
                or fn.args.vararg or fn.args.kwarg or fn.args.posonlyargs or fn.args.kwonlyargs or fn.decorator_list):
            reject('entry_point', 'main accepts event, channel only, without defaults/decorators', fn, 'compile')
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Assign) and _literal(node.value) and all(isinstance(t, ast.Name) for t in node.targets):
            continue
        reject('top_level_effect', 'Top level permits imports, constants and function declarations only', node)
    module_aliases = {alias.asname or alias.name: alias.name for node in nodes if isinstance(node, ast.Import)
                      for alias in node.names if alias.name in SAFE_IMPORTS}
    for node in nodes:
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in module_aliases:
                if node.attr not in EXPORTS[module_aliases[node.value.id]]:
                    reject('unsafe_module_export', 'Only documented computation exports may be accessed', node)
            if not isinstance(node.ctx, ast.Load):
                reject('unsafe_attribute_mutation', 'Attribute mutation is not permitted', node)
        if isinstance(node, ast.Import):
            if any(alias.name not in SAFE_IMPORTS for alias in node.names):
                reject('unsafe_import', 'Only approved computation-only standard library roots may be imported', node)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in SAFE_IMPORTS or any(
                    x.name not in EXPORTS.get(node.module, ()) for x in node.names):
                reject('unsafe_import', 'Relative, wildcard and private imports are not permitted', node)
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.ClassDef)):
            reject('unsafe_structure', 'Async, classes and global/nonlocal mutation are not supported by decision scripts', node)
        elif isinstance(node, ast.Name):
            if node.id.startswith('_') or node.id in FORBIDDEN_NAMES:
                reject('unsafe_name', f'Forbidden name: {node.id}', node)
            if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == 'capability':
                reject('reserved_capability', 'The trusted capability broker cannot be replaced', node)
        elif isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            reject('unsafe_attribute', 'Private/dunder reflection is not permitted', node)
        elif isinstance(node, ast.FunctionDef):
            if node.name.startswith('_') or node.name == 'capability' or node.decorator_list or getattr(node, 'type_params', ()):
                reject('unsafe_function', 'Decorators, private/reserved functions and type parameters are not permitted', node)
            if any(not _literal(x) for x in (*node.args.defaults, *(x for x in node.args.kw_defaults if x is not None))):
                reject('hidden_initialization', 'Function defaults must be literal constants', node)
            if node.returns or any(a.annotation for a in (*node.args.args, *node.args.posonlyargs, *node.args.kwonlyargs)):
                reject('hidden_annotation', 'Executable type annotations are not permitted', node)
        elif isinstance(node, ast.arg) and (node.arg.startswith('_') or node.arg == 'capability'):
            reject('reserved_argument', 'Private or capability-shadowing parameters are not permitted', node)
        elif isinstance(node, ast.alias) and node.asname and (node.asname.startswith('_') or node.asname == 'capability'):
            reject('reserved_import', 'Import aliases cannot shadow the broker or use private names', node)
    return report()
