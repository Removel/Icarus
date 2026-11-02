"""Standalone worker. Invoked by absolute path with Python -I, never imported by host."""
import json
import resource
import sys

resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))


def emit(value):
    data = json.dumps(value)
    if len(data.encode()) > 131000:
        raise ValueError('worker output limit')
    sys.stdout.write(data + '\n')
    sys.stdout.flush()


def capability(name, args):
    if not isinstance(name, str) or not isinstance(args, dict):
        raise ValueError('capability requires name and dict')
    emit({'type': 'call', 'name': name, 'args': args})
    line = sys.stdin.readline(131072)
    return json.loads(line)['value']


try:
    initial = json.loads(sys.stdin.buffer.readline(2 * 1024 * 1024 + 1))
    names = ('len', 'str', 'int', 'float', 'bool', 'any', 'all', 'min', 'max', 'sum', 'sorted', 'range', 'abs')
    import builtins
    safe = {name: getattr(builtins, name) for name in names}
    namespace = {'__builtins__': safe, 'capability': capability}
    exec(compile(initial['source'], '<argus-rule>', 'exec'), namespace)
    result = namespace['decide'](initial['event'], initial['channel'])
    emit({'type': 'result', 'value': result})
except BaseException as exc:
    emit({'type': 'error', 'message': str(exc)[:512]})
