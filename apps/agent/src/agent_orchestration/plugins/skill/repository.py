"""Safe directory persistence for explicit Skill produce/evolve Jobs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
import threading
from typing import Any

import yaml

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillScope,
    normalize_skill_name,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner


_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_WRITE_LOCKS_GUARD = threading.Lock()
_WRITE_LOCKS: dict[Path, threading.RLock] = {}
MAX_FILES = 256
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SkillSnapshot:
    """Exact source directory state used for evolve conflict checks."""

    name: str
    description: str
    scope: SkillScope
    path: Path
    directory_hash: str


@dataclass(frozen=True)
class _ParsedSkill:
    name: str
    description: str
    scope: SkillScope
    path: Path
    directory_hash: str


class SkillRepositoryError(Exception):
    """Base class for safe Skill repository failures."""


class SkillValidationError(SkillRepositoryError):
    pass


class SkillSecurityError(SkillRepositoryError):
    pass


class SkillConflictError(SkillRepositoryError):
    pass


class SkillRepository:
    """Prepare Drafts and transactionally publish complete Skill trees."""

    def __init__(
        self,
        global_skills_dir: str | Path,
        workspace_skills_dir: str | Path,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.global_skills_dir = Path(global_skills_dir).expanduser().absolute()
        self.workspace_skills_dir = (
            Path(workspace_skills_dir).expanduser().absolute()
        )
        self.logger = logger or logging.getLogger(__name__)
        if _paths_overlap(self.global_skills_dir, self.workspace_skills_dir):
            raise ValueError(
                "Global and Workspace Skill directories must not overlap"
            )
        self._write_lock = _write_lock_for(self.global_skills_dir)

    def capture(self, name: str) -> SkillSnapshot | None:
        safe_name = _safe_name(name)
        parsed = self._read_visible(safe_name)
        if parsed is None:
            return None
        return SkillSnapshot(
            name=parsed.name,
            description=parsed.description,
            scope=parsed.scope,
            path=parsed.path,
            directory_hash=parsed.directory_hash,
        )

    def prepare_produce(self, name: str, scope: SkillScope) -> Path:
        safe_name = _safe_name(name)
        root = self._root(scope)
        self._ensure_root(root)
        draft_root = self._ensure_draft_root(root)
        return Path(
            tempfile.mkdtemp(
                prefix=f".{safe_name}.",
                suffix=".draft",
                dir=draft_root,
            )
        ).absolute()

    def prepare_evolve(self, snapshot: SkillSnapshot) -> Path:
        safe_name = _safe_name(snapshot.name)
        if snapshot.scope not in ("global", "workspace"):
            raise SkillValidationError(
                f"snapshot {safe_name!r} has an invalid scope"
            )
        target_root = self.workspace_skills_dir
        self._ensure_root(target_root)
        draft_root = self._ensure_draft_root(target_root)
        draft = Path(
            tempfile.mkdtemp(
                prefix=f".{safe_name}.",
                suffix=".draft",
                dir=draft_root,
            )
        ).absolute()
        try:
            current = self._read_scope(snapshot.scope, safe_name)
            if current is None or not self._snapshot_matches(snapshot, current):
                raise SkillConflictError(
                    f"evolve target {safe_name!r} changed after analysis"
                )
            _copy_skill_tree(snapshot.path.parent, draft)
            _, copied_hash = _validate_skill_tree(draft, safe_name)
            if copied_hash != snapshot.directory_hash:
                raise SkillConflictError(
                    f"evolve target {safe_name!r} changed while preparing Draft"
                )
            current = self._read_scope(snapshot.scope, safe_name)
            if current is None or not self._snapshot_matches(snapshot, current):
                raise SkillConflictError(
                    f"evolve target {safe_name!r} changed while preparing Draft"
                )
            return draft
        except BaseException:
            self.cleanup_draft(draft)
            raise

    def publish_produce(
        self,
        name: str,
        scope: SkillScope,
        draft: str | Path,
    ) -> Path:
        safe_name = _safe_name(name)
        root = self._root(scope)
        draft_path = self._validate_draft(draft, root, safe_name)
        with self._write_lock:
            conflicts = self._find_conflict_scopes(safe_name)
            if conflicts:
                raise SkillConflictError(
                    f"Skill {safe_name!r} already exists in: "
                    + ", ".join(conflicts)
                )
            target = root / safe_name
            try:
                os.rename(draft_path, target)
            except FileExistsError as error:
                raise SkillConflictError(
                    f"Skill {safe_name!r} appeared before commit"
                ) from error
            self._fsync_directory(root)
        return (target / "SKILL.md").absolute()

    def publish_evolve(
        self,
        snapshot: SkillSnapshot,
        draft: str | Path,
    ) -> Path:
        safe_name = _safe_name(snapshot.name)
        root = self.workspace_skills_dir
        draft_path = self._validate_draft(draft, root, safe_name)
        with self._write_lock:
            current = self._read_scope(snapshot.scope, safe_name)
            if current is None or not self._snapshot_matches(snapshot, current):
                raise SkillConflictError(
                    f"evolve target {safe_name!r} changed after analysis"
                )
            target = root / safe_name
            if snapshot.scope == "global":
                if self._scope_entry_exists("workspace", safe_name):
                    raise SkillConflictError(
                        f"Workspace override for {safe_name!r} appeared after analysis"
                    )
                try:
                    os.rename(draft_path, target)
                except FileExistsError as error:
                    raise SkillConflictError(
                        f"Workspace override for {safe_name!r} appeared after analysis"
                    ) from error
            else:
                self._replace_directory(target, draft_path)
            self._fsync_directory(root)
        return (target / "SKILL.md").absolute()

    def cleanup_draft(self, draft: str | Path) -> None:
        path = Path(draft).absolute()
        if not path.exists():
            return
        if not any(
            _is_direct_draft(path, root)
            for root in (self.global_skills_dir, self.workspace_skills_dir)
        ):
            raise SkillSecurityError(f"Refusing to clean unsafe Draft: {path}")
        if path.is_symlink():
            raise SkillSecurityError(f"Draft is a symlink: {path}")
        shutil.rmtree(path)

    def find_conflicts(self, name: str) -> tuple[SkillScope, ...]:
        safe_name = _safe_name(name)
        with self._write_lock:
            return self._find_conflict_scopes(safe_name)

    def _root(self, scope: SkillScope) -> Path:
        if scope == "global":
            return self.global_skills_dir
        if scope == "workspace":
            return self.workspace_skills_dir
        raise SkillValidationError(f"unsupported Skill scope: {scope}")

    def _ensure_root(self, root: Path) -> None:
        descriptor, _ = _open_directory_path(
            root, create=True, tighten_final=True
        )
        assert descriptor is not None
        os.close(descriptor)

    @staticmethod
    def _ensure_draft_root(root: Path) -> Path:
        draft_root = root / ".drafts"
        draft_root.mkdir(mode=0o700, exist_ok=True)
        if draft_root.is_symlink() or not draft_root.is_dir():
            raise SkillSecurityError(
                f"Skill Draft root is not a safe directory: {draft_root}"
            )
        draft_root.chmod(0o700)
        return draft_root

    def _validate_draft(
        self, draft: str | Path, root: Path, name: str
    ) -> Path:
        path = Path(draft).expanduser().absolute()
        if not _is_direct_draft(path, root):
            raise SkillSecurityError(
                f"Draft must be a direct temporary child of {root}"
            )
        if path.is_symlink() or not path.is_dir():
            raise SkillSecurityError(f"Draft is not a safe directory: {path}")
        _validate_skill_tree(path, name)
        return path

    @staticmethod
    def _snapshot_matches(
        snapshot: SkillSnapshot, current: _ParsedSkill
    ) -> bool:
        return (
            current.directory_hash == snapshot.directory_hash
            and current.path == snapshot.path.absolute()
        )

    def _replace_directory(self, target: Path, draft: Path) -> None:
        backup = target.parent / ".drafts" / (
            f".{target.name}.{secrets.token_hex(8)}.backup"
        )
        moved_old = False
        moved_new = False
        try:
            os.rename(target, backup)
            moved_old = True
            os.rename(draft, target)
            moved_new = True
        except BaseException:
            if moved_new and target.exists():
                failed = target.with_name(
                    f".{target.name}.{secrets.token_hex(8)}.failed"
                )
                try:
                    os.rename(target, failed)
                    shutil.rmtree(failed, ignore_errors=True)
                except OSError:
                    pass
            if moved_old and backup.exists() and not target.exists():
                os.rename(backup, target)
            raise
        else:
            try:
                shutil.rmtree(backup)
            except OSError:
                self.logger.warning(
                    "Unable to remove committed Skill backup: %s", backup,
                    exc_info=True,
                )

    def _find_conflict_scopes(self, name: str) -> tuple[SkillScope, ...]:
        scanner = SkillScanner(
            self.global_skills_dir,
            self.workspace_skills_dir,
            logger=self.logger,
        )
        conflicts = {
            skill.scope
            for scope in ("global", "workspace")
            for skill in scanner.scan_scope(scope)
            if normalize_skill_name(skill.name) == name
        }
        conflicts.update(
            scope
            for scope in ("global", "workspace")
            if self._scope_entry_exists(scope, name)
        )
        return tuple(
            scope
            for scope in ("global", "workspace")
            if scope in conflicts
        )

    def _scope_entry_exists(self, scope: SkillScope, name: str) -> bool:
        root = self._root(scope)
        root_fd, _ = _open_directory_path(root, create=False)
        if root_fd is None:
            return False
        try:
            try:
                os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return True
        finally:
            os.close(root_fd)

    def _read_visible(self, name: str) -> _ParsedSkill | None:
        return self._read_scope("workspace", name) or self._read_scope(
            "global", name
        )

    def _read_scope(
        self, scope: SkillScope, name: str
    ) -> _ParsedSkill | None:
        root = self._root(scope)
        root_fd, _ = _open_directory_path(root, create=False)
        if root_fd is None:
            return None
        try:
            skill_fd = _open_skill_directory(root_fd, name)
            if skill_fd is None:
                return None
            try:
                _ensure_directory_attached(root_fd, name, skill_fd)
                directory = root / name
                description, directory_hash = _validate_skill_tree(
                    directory, name
                )
                _ensure_directory_attached(root_fd, name, skill_fd)
                return _ParsedSkill(
                    name=name,
                    description=description,
                    scope=scope,
                    path=(directory / "SKILL.md").absolute(),
                    directory_hash=directory_hash,
                )
            finally:
                os.close(skill_fd)
        finally:
            os.close(root_fd)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _safe_name(value: Any) -> str:
    if not isinstance(value, str):
        raise SkillValidationError("Skill name must be a string")
    normalized = normalize_skill_name(value)
    if not _SAFE_NAME.fullmatch(normalized):
        raise SkillSecurityError(
            "Skill name must contain only lowercase ASCII letters, digits, "
            "hyphens, or underscores and be at most 64 characters"
        )
    return normalized


def _validate_skill_tree(root: Path, target_name: str) -> tuple[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise SkillSecurityError(f"Skill Draft is not a safe directory: {root}")
    digest = hashlib.sha256()
    count = 0
    total = 0
    skill_content: str | None = None
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise SkillSecurityError(
                f"Unable to inspect Skill directory: {directory}"
            ) from error
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise SkillSecurityError(
                    f"Unable to inspect Skill entry: {relative}"
                ) from error
            if stat.S_ISLNK(info.st_mode):
                raise SkillSecurityError(f"Skill entry is a symlink: {relative}")
            if stat.S_ISDIR(info.st_mode):
                digest.update(b"D\0" + relative.as_posix().encode() + b"\0")
                stack.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SkillSecurityError(
                    f"Skill entry is not a regular file: {relative}"
                )
            count += 1
            if count > MAX_FILES:
                raise SkillValidationError(
                    f"Skill contains more than {MAX_FILES} files"
                )
            if info.st_size > MAX_FILE_BYTES:
                raise SkillValidationError(
                    f"Skill file exceeds {MAX_FILE_BYTES} bytes: {relative}"
                )
            try:
                data = path.read_bytes()
            except OSError as error:
                raise SkillSecurityError(
                    f"Unable to read Skill entry: {relative}"
                ) from error
            if len(data) > MAX_FILE_BYTES:
                raise SkillValidationError(
                    f"Skill file exceeds {MAX_FILE_BYTES} bytes: {relative}"
                )
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise SkillValidationError(
                    f"Skill exceeds {MAX_TOTAL_BYTES} total bytes"
                )
            digest.update(b"F\0" + relative.as_posix().encode() + b"\0")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            if relative.as_posix() == "SKILL.md":
                try:
                    skill_content = data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise SkillValidationError(
                        "SKILL.md content must be UTF-8 text"
                    ) from error
    if skill_content is None:
        raise SkillValidationError("Skill directory must contain SKILL.md")
    description = _parse_skill_content(skill_content, target_name)
    return description, digest.hexdigest()


def _parse_skill_content(content: str, target_name: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError(
            "SKILL.md must start with YAML front matter"
        )
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise SkillValidationError(
            "SKILL.md YAML front matter is missing its closing delimiter"
        ) from error
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
    except yaml.YAMLError as error:
        raise SkillValidationError("SKILL.md YAML front matter is invalid") from error
    if not isinstance(metadata, dict):
        raise SkillValidationError(
            "SKILL.md YAML front matter must be a mapping"
        )
    metadata_name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(metadata_name, str) or not metadata_name.strip():
        raise SkillValidationError("SKILL.md name must be a non-empty string")
    if _safe_name(metadata_name) != target_name:
        raise SkillValidationError(
            "SKILL.md YAML name must match the repository target name"
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError(
            "SKILL.md description must be a non-empty string"
        )
    return description.strip()


def _copy_skill_tree(source: Path, destination: Path) -> None:
    _validate_skill_tree(source, _safe_name(source.name))
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        target = destination / relative
        info = source_path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise SkillSecurityError(f"Skill entry is a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif stat.S_ISREG(info.st_mode):
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(source_path.read_bytes())
            target.chmod(0o600)
        else:
            raise SkillSecurityError(
                f"Skill entry is not a regular file: {relative}"
            )


def _is_direct_draft(path: Path, root: Path) -> bool:
    return (
        path.parent == (root.absolute() / ".drafts")
        and path.name.startswith(".")
        and path.name.endswith(".draft")
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _write_lock_for(global_root: Path) -> threading.RLock:
    key = global_root.resolve(strict=False)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITE_LOCKS[key] = lock
        return lock


def _open_directory_path(
    path: Path, *, create: bool, tighten_final: bool = False
) -> tuple[int | None, bool]:
    absolute = path.absolute()
    parts = absolute.parts
    if not parts:
        raise SkillSecurityError("Skill root path cannot be empty")
    current_fd = os.open(parts[0], os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    final_created = False
    try:
        for index, component in enumerate(parts[1:], start=1):
            is_final = index == len(parts) - 1
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                if not create:
                    os.close(current_fd)
                    return None, False
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
                os.fsync(current_fd)
                if is_final:
                    final_created = True
                next_fd = os.open(
                    component,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                    dir_fd=current_fd,
                )
            except OSError as error:
                raise SkillSecurityError(
                    f"Directory path contains a symlink or unsafe component: {path}"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise SkillValidationError(f"Skill root is not a directory: {path}")
        if tighten_final:
            os.fchmod(current_fd, 0o700)
        return current_fd, final_created
    except Exception:
        os.close(current_fd)
        raise


def _open_skill_directory(root_fd: int, name: str) -> int | None:
    try:
        descriptor = os.open(
            _safe_name(name),
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SkillSecurityError(
            f"Skill directory is a symlink or unsafe entry: {name}"
        ) from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise SkillValidationError(f"Skill path is not a directory: {name}")
    return descriptor


def _ensure_directory_attached(
    root_fd: int,
    name: str,
    directory_fd: int,
) -> None:
    """Reject a Skill directory exchanged after its descriptor was opened."""
    try:
        attached = os.stat(
            _safe_name(name),
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise SkillConflictError(
            f"Skill directory was detached during operation: {name}"
        ) from error
    opened = os.fstat(directory_fd)
    if stat.S_ISLNK(attached.st_mode) or (
        attached.st_dev,
        attached.st_ino,
    ) != (opened.st_dev, opened.st_ino):
        raise SkillConflictError(
            f"Skill directory was exchanged during operation: {name}"
        )
