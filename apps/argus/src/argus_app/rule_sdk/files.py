"""Immutable, content-addressed channel rule source files."""
from hashlib import sha256
from pathlib import Path
import os
import re
import tempfile

from argus_app.rule_sdk.engine import RuleError, lint


class RuleFiles:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def check(self, source: str) -> None:
        lint(source)

    def publish(self, source: str, channel_id: str) -> dict:
        self.check(source)
        if not re.fullmatch(r'[A-Za-z0-9-]{1,100}', channel_id):
            raise RuleError('rule_path', 'invalid channel identity')
        digest = sha256(source.encode()).hexdigest()
        relative = f'{channel_id}/{digest}.py'
        target = self._path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.rule-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            # link creates immutable destination without replacing an existing file
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_text(encoding='utf-8') != source:
                    raise RuleError('rule_hash', 'existing rule content mismatch')
        finally:
            os.unlink(temporary)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {'rule_path': relative, 'rule_hash': digest}

    def _path(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise RuleError('rule_path', 'path outside rule root')
        return candidate

    def read(self, path: str, hash: str) -> str:
        try:
            target = self._path(path)
            if target.stat().st_size > 32768:
                raise RuleError('rule_invalid', 'rule exceeds size limit')
            source = target.read_text(encoding='utf-8')
        except OSError as exc:
            raise RuleError('rule_unavailable', str(exc)) from None
        if sha256(source.encode()).hexdigest() != hash:
            raise RuleError('rule_hash', 'rule hash mismatch')
        self.check(source)
        return source
