"""Trusted bootstrap, run only inside Linux user/mount/pid/network namespaces."""
import builtins
import ctypes
import errno
import importlib
import importlib.util
import types
import json
import os
import resource
import socket
import sys

# Load only the trusted export policy before removing access to host files.
policy_spec = importlib.util.spec_from_file_location('argus_safe_exports', os.path.join(os.path.dirname(__file__), 'safe_modules.py'))
policy = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy)
exports = policy.EXPORTS
facades = {}
for module_name, names in exports.items():
    module = importlib.import_module(module_name)
    facades[module_name] = types.SimpleNamespace(**{name: getattr(module, name) for name in names if hasattr(module, name)})


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in facades:
        raise ImportError('Only approved computation imports are available')
    if any(item not in exports[name] for item in (fromlist or ())):
        raise ImportError('Undocumented module export')
    return facades[name]

libc = ctypes.CDLL(None, use_errno=True)
seccomp = ctypes.CDLL('libseccomp.so.2', use_errno=True)
seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
seccomp.seccomp_init.restype = ctypes.c_void_p
seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]


def protect(root):
    # Private mount tree; empty root is a read-only bind, with no host paths/dev/proc.
    if libc.mount(None, b'/', None, 16384 | (1 << 18), None) != 0:
        raise OSError(ctypes.get_errno(), 'private mount')
    encoded = os.fsencode(root)
    if libc.mount(encoded, encoded, None, 4096, None) != 0:
        raise OSError(ctypes.get_errno(), 'root bind')
    if libc.mount(None, encoded, None, 4096 | 32 | 1 | 2 | 4 | 8, None) != 0:
        raise OSError(ctypes.get_errno(), 'read-only root')
    os.chdir(root)
    os.chroot('.')
    os.chdir('/')
    os.environ.clear()
    os.closerange(3, 65536)
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'no_new_privs')
    context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW; deny authority-granting syscalls
    if not context:
        raise RuntimeError('seccomp unavailable')
    denied = (
        'open', 'openat', 'openat2', 'creat', 'socket', 'socketpair', 'connect', 'bind',
        'listen', 'accept', 'accept4', 'sendto', 'sendmsg', 'recvfrom', 'recvmsg',
        'clone', 'clone3', 'fork', 'vfork', 'execve', 'execveat', 'ptrace',
        'mount', 'umount2', 'pivot_root', 'chroot', 'setns', 'unshare', 'capset',
        'open_by_handle_at', 'name_to_handle_at', 'bpf', 'perf_event_open',
        'process_vm_readv', 'process_vm_writev', 'pidfd_getfd', 'io_uring_setup',
        'init_module', 'finit_module', 'delete_module', 'reboot', 'kexec_load',
        'keyctl', 'add_key', 'request_key', 'userfaultfd', 'mknod', 'mknodat',
    )
    try:
        for name in denied:
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('seccomp rule failed')
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError('seccomp load failed')
    finally:
        seccomp.seccomp_release(context)
    # No late loading from Python paths; only already loaded standard-library modules exist.
    sys.path[:] = []
    sys.meta_path[:] = []


protocol_out = sys.stdout
protocol_in = sys.stdin


def emit(value):
    data = json.dumps(value, allow_nan=False)
    if len(data.encode()) > 131000:
        raise ValueError('worker output limit')
    protocol_out.write(data + '\n')
    protocol_out.flush()


def capability(name, args):
    if not isinstance(name, str) or not isinstance(args, dict):
        raise ValueError('capability requires name and dict')
    emit({'type': 'call', 'name': name, 'args': args})
    response = json.loads(protocol_in.readline(131072))
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response['value']


class QuietOutput:
    def write(self, value):
        return len(value)
    def flush(self):
        pass


def run():
    try:
        root = sys.argv[1]
        probe = len(sys.argv) > 2 and sys.argv[2] == '--probe'
        initial = None if probe else json.loads(protocol_in.buffer.readline(2 * 1024 * 1024 + 1))
        protect(root)
        if probe:
            protocol_out.write('ready\n')
            protocol_out.flush()
        else:
            sys.stdout = sys.stderr = QuietOutput()
            names = ('len', 'str', 'int', 'float', 'bool', 'any', 'all', 'min', 'max', 'sum', 'sorted',
                     'range', 'abs', 'isinstance', 'type', 'enumerate', 'zip', 'list', 'dict', 'set',
                     'tuple', 'round', 'reversed', 'map', 'filter', 'print', 'Exception', 'ValueError',
                     'TypeError', 'KeyError', 'IndexError', 'RuntimeError', 'OSError', 'ImportError', 'AttributeError')
            safe = {name: getattr(builtins, name) for name in names}
            safe['__import__'] = safe_import
            namespace = {'__builtins__': safe, 'capability': capability, '__name__': 'argus_rule'}
            exec(compile(initial['source'], '<argus-rule>', 'exec'), namespace)
            result = namespace['main'](initial['event'], initial['channel'])
            emit({'type': 'result', 'value': result})
    except BaseException as exc:
        emit({'type': 'error', 'message': str(exc)[:512]})
        sys.exit(1)


if __name__ == '__main__':
    run()
