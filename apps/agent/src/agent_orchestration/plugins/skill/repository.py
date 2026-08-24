"""Safe filesystem persistence for explicit Skill produce/evolve Jobs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import stat
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


@dataclass(frozen=True)
class SkillSnapshot:
    """Exact source state used for optimistic evolve conflict checks."""

    name: str
    description: str
    scope: SkillScope
    path: Path
    content: str
    content_hash: str

@dataclass(frozen=True)
class _ParsedSkill:
    name: str
    description: str
    scope: SkillScope
    path: Path
    content: str
    content_hash: str


class SkillRepositoryError(Exception):
    """Base class for safe Skill repository failures."""


class SkillValidationError(SkillRepositoryError):
    pass


class SkillSecurityError(SkillRepositoryError):
    pass


class SkillConflictError(SkillRepositoryError):
    pass


class _CommittedWriteError(SkillRepositoryError):
    """The atomic replace succeeded but final durability checking failed."""


class SkillRepository:
    """Read visible Skills and safely commit explicit write Jobs."""

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
        if _paths_overlap(
            self.global_skills_dir,
            self.workspace_skills_dir,
        ):
            raise ValueError(
                "Global and Workspace Skill directories must not overlap"
            )
        self._write_lock = _write_lock_for(self.global_skills_dir)

    def capture(self, name: str) -> SkillSnapshot | None:
        """Capture the exact currently visible Skill for a later evolve."""
        safe_name = _safe_name(name)
        parsed = self._read_visible(safe_name)
        if parsed is None:
            return None
        return SkillSnapshot(
            name=parsed.name,
            description=parsed.description,
            scope=parsed.scope,
            path=parsed.path,
            content=parsed.content,
            content_hash=parsed.content_hash,
        )

    def produce(
        self,
        name: str,
        scope: SkillScope,
        content: str,
    ) -> Path:
        """Create one new Skill after checking both physical scopes."""
        safe_name = _safe_name(name)
        if scope not in ("global", "workspace"):
            raise SkillValidationError(f"unsupported Skill scope: {scope}")
        encoded, _ = _parse_content(content, safe_name)
        with self._write_lock:
            found_scopes = self._find_conflict_scopes(safe_name)
            if found_scopes:
                raise SkillConflictError(
                    f"Skill {safe_name!r} already exists in: "
                    + ", ".join(found_scopes)
                )
            return self._atomic_write(
                safe_name,
                encoded,
                scope=scope,
                require_absent=True,
            )

    def find_conflicts(self, name: str) -> tuple[SkillScope, ...]:
        """Return scopes occupied by a valid Skill or same-name entry."""
        safe_name = _safe_name(name)
        with self._write_lock:
            return self._find_conflict_scopes(safe_name)

    def evolve(self, snapshot: SkillSnapshot, content: str) -> Path:
        """Update a Workspace Skill or override a global Skill in Workspace."""
        safe_name = _safe_name(snapshot.name)
        if snapshot.scope not in ("global", "workspace"):
            raise SkillValidationError(
                f"snapshot {safe_name!r} has an invalid scope"
            )
        encoded, _ = _parse_content(content, safe_name)
        with self._write_lock:
            current = self._read_scope(snapshot.scope, safe_name)
            if current is None:
                raise SkillConflictError(
                    f"evolve target {safe_name!r} disappeared after analysis"
                )
            if (
                current.content_hash != snapshot.content_hash
                or current.path != snapshot.path.absolute()
            ):
                raise SkillConflictError(
                    f"evolve target {safe_name!r} changed after analysis"
                )
            require_absent = snapshot.scope == "global"
            if require_absent and self._scope_entry_exists(
                "workspace", safe_name
            ):
                raise SkillConflictError(
                    f"Workspace override for {safe_name!r} appeared after analysis"
                )
            return self._atomic_write(
                safe_name,
                encoded,
                scope="workspace",
                require_absent=require_absent,
            )

    def _find_conflict_scopes(
        self, name: str
    ) -> tuple[SkillScope, ...]:
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
            scope for scope in ("global", "workspace") if scope in conflicts
        )

    def _scope_entry_exists(self, scope: SkillScope, name: str) -> bool:
        root = (
            self.global_skills_dir
            if scope == "global"
            else self.workspace_skills_dir
        )
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
        workspace = self._read_scope("workspace", name)
        if workspace is not None:
            return workspace
        return self._read_scope("global", name)

    def _read_scope(
        self,
        scope: SkillScope,
        name: str,
    ) -> _ParsedSkill | None:
        root = (
            self.workspace_skills_dir
            if scope == "workspace"
            else self.global_skills_dir
        )
        root_fd, _ = _open_directory_path(root, create=False)
        if root_fd is None:
            return None
        try:
            return self._read_scope_from_root_fd(
                root_fd,
                root,
                scope,
                name,
            )
        finally:
            os.close(root_fd)

    def _read_scope_from_root_fd(
        self,
        root_fd: int,
        root: Path,
        scope: SkillScope,
        name: str,
    ) -> _ParsedSkill | None:
        skill_fd = _open_skill_directory(root_fd, name, create=False)
        if skill_fd is None:
            return None
        file_fd = -1
        try:
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            try:
                file_fd = os.open(
                    "SKILL.md",
                    os.O_RDONLY | _NOFOLLOW,
                    dir_fd=skill_fd,
                )
            except FileNotFoundError:
                return None
            except OSError as error:
                raise SkillSecurityError(
                    "Unable to open SKILL.md without following links: "
                    f"{root / name / 'SKILL.md'}"
                ) from error
            file_status = os.fstat(file_fd)
            if not stat.S_ISREG(file_status.st_mode):
                raise SkillValidationError(
                    f"SKILL.md is not a regular file: {root / name / 'SKILL.md'}"
                )
            with os.fdopen(file_fd, "rb") as skill_file:
                file_fd = -1
                raw_content = skill_file.read()
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            content = raw_content.decode("utf-8")
            _, description = _parse_content(content, name)
            return _ParsedSkill(
                name=name,
                description=description,
                scope=scope,
                path=(root / name / "SKILL.md").absolute(),
                content=content,
                content_hash=hashlib.sha256(raw_content).hexdigest(),
            )
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(skill_fd)

    def _atomic_write(
        self,
        name: str,
        content: bytes,
        *,
        scope: SkillScope,
        require_absent: bool,
    ) -> Path:
        root = (
            self.global_skills_dir
            if scope == "global"
            else self.workspace_skills_dir
        )
        root_fd, _ = _open_directory_path(
            root,
            create=True,
            tighten_final=True,
        )
        assert root_fd is not None
        skill_fd = -1
        descriptor = -1
        temporary_name: str | None = None
        committed = False
        path = (root / name / "SKILL.md").absolute()
        try:
            opened_skill_fd = _open_skill_directory(root_fd, name, create=True)
            assert opened_skill_fd is not None
            skill_fd = opened_skill_fd
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            try:
                current_status = os.stat(
                    "SKILL.md",
                    dir_fd=skill_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(current_status.st_mode):
                    raise SkillSecurityError(
                        f"SKILL.md is a symlink: {root / name / 'SKILL.md'}"
                    )
                if not stat.S_ISREG(current_status.st_mode):
                    raise SkillValidationError(
                        f"SKILL.md is not a regular file: {root / name / 'SKILL.md'}"
                    )
                if require_absent:
                    raise SkillConflictError(
                        f"Skill {name!r} appeared before commit"
                    )
            descriptor, temporary_name = _create_temporary_file(skill_fd)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as temporary_file:
                descriptor = -1
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            os.replace(
                temporary_name,
                "SKILL.md",
                src_dir_fd=skill_fd,
                dst_dir_fd=skill_fd,
            )
            committed = True
            temporary_name = None
            try:
                os.fsync(skill_fd)
                _ensure_skill_directory_attached(root_fd, name, skill_fd)
            except Exception as error:
                raise _CommittedWriteError(
                    "SKILL.md was atomically replaced, but final directory "
                    "durability or attachment verification failed"
                ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name is not None and skill_fd >= 0:
                try:
                    os.unlink(temporary_name, dir_fd=skill_fd)
                except FileNotFoundError:
                    pass
            try:
                if skill_fd >= 0:
                    os.close(skill_fd)
            finally:
                os.close(root_fd)
        if not committed:
            raise AssertionError("atomic write returned without committing")
        return path


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


def _parse_content(content: Any, target_name: str) -> tuple[bytes, str]:
    if not isinstance(content, str):
        raise SkillValidationError("SKILL.md content must be UTF-8 text")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SkillValidationError(
            "SKILL.md content must be UTF-8 text"
        ) from error
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
    metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
    if not isinstance(metadata, dict):
        raise SkillValidationError(
            "SKILL.md YAML front matter must be a mapping"
        )
    metadata_name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(metadata_name, str) or not metadata_name.strip():
        raise SkillValidationError(
            "SKILL.md name must be a non-empty string"
        )
    if _safe_name(metadata_name) != target_name:
        raise SkillValidationError(
            "SKILL.md YAML name must match the repository target name"
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError(
            "SKILL.md description must be a non-empty string"
        )
    return encoded, description.strip()


def _paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return (
        first == second
        or _is_relative_to(first, second)
        or _is_relative_to(second, first)
    )


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _write_lock_for(global_root: Path) -> threading.RLock:
    """Share a lock across Workspaces that can write one global root."""
    key = global_root.resolve(strict=False)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITE_LOCKS[key] = lock
        return lock


def _open_directory_path(
    path: Path,
    *,
    create: bool,
    tighten_final: bool = False,
) -> tuple[int | None, bool]:
    """Open a directory hierarchy without following symlinks."""
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
            raise SkillValidationError(
                f"Skill root is not a directory: {path}"
            )
        if tighten_final:
            os.fchmod(current_fd, 0o700)
        return current_fd, final_created
    except Exception:
        os.close(current_fd)
        raise


def _open_skill_directory(
    root_fd: int,
    name: str,
    *,
    create: bool,
) -> int | None:
    safe_name = _safe_name(name)
    created = False
    try:
        skill_fd = os.open(
            safe_name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        if not create:
            return None
        os.mkdir(safe_name, mode=0o700, dir_fd=root_fd)
        os.fsync(root_fd)
        created = True
        skill_fd = os.open(
            safe_name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=root_fd,
        )
    except OSError as error:
        raise SkillSecurityError(
            f"Skill directory is a symlink or unsafe entry: {safe_name}"
        ) from error
    try:
        if not stat.S_ISDIR(os.fstat(skill_fd).st_mode):
            raise SkillValidationError(
                f"Skill path is not a directory: {safe_name}"
            )
        if create:
            os.fchmod(skill_fd, 0o700)
        return skill_fd
    except Exception:
        os.close(skill_fd)
        if created:
            try:
                os.rmdir(safe_name, dir_fd=root_fd)
            except OSError:
                pass
        raise


def _ensure_skill_directory_attached(
    root_fd: int,
    name: str,
    skill_fd: int,
) -> None:
    """Reject a directory exchanged after its descriptor was opened."""
    try:
        attached_status = os.stat(
            _safe_name(name),
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise SkillConflictError(
            f"Skill directory was detached during operation: {name}"
        ) from error
    open_status = os.fstat(skill_fd)
    if stat.S_ISLNK(attached_status.st_mode) or (
        attached_status.st_dev,
        attached_status.st_ino,
    ) != (open_status.st_dev, open_status.st_ino):
        raise SkillConflictError(
            f"Skill directory was exchanged during operation: {name}"
        )


def _create_temporary_file(skill_fd: int) -> tuple[int, str]:
    for _ in range(128):
        name = f".SKILL.md.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
                dir_fd=skill_fd,
            )
        except FileExistsError:
            continue
        return descriptor, name
    raise OSError("unable to allocate a unique SKILL.md temporary file")
