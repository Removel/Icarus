"""Safe filesystem repository for automatic Workspace Skill maintenance."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import threading
from typing import Any, Literal, Protocol

import yaml

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    LifecycleStatus,
    SkillScope,
    SkillUsage,
    normalize_skill_name,
)


RepositoryResultStatus = Literal["success", "skipped", "failed"]
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_WORKSPACE_LOCKS_GUARD = threading.Lock()
_WORKSPACE_LOCKS: dict[Path, threading.RLock] = {}


class RepositoryOperation(Protocol):
    """Structural input accepted by :meth:`SkillRepository.apply`."""

    action: str
    target_name: str | None
    source_names: Sequence[str]
    content: str | None


@dataclass(frozen=True)
class SkillSnapshot:
    """Immutable Skill state used for optimistic conflict checks."""

    name: str
    description: str
    scope: SkillScope
    path: Path
    content: str
    content_hash: str
    lifecycle_status: LifecycleStatus = "active"
    last_used_at: datetime | None = None
    use_count: int = 0

    @property
    def normalized_name(self) -> str:
        return normalize_skill_name(self.name)

    @property
    def skill_key(self) -> str:
        return f"{self.scope}:{self.normalized_name}"


@dataclass(frozen=True)
class RepositoryOperationResult:
    """Outcome of one repository operation."""

    action: str
    target_name: str
    status: RepositoryResultStatus
    message: str
    path: Path | None = None
    source_names: tuple[str, ...] = ()
    target_written: bool = False
    file_deleted: bool = False
    directory_removed: bool = False
    deleted_sources: tuple[str, ...] = ()
    retained_sources: tuple[str, ...] = ()
    cleanup_errors: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True)
class RepositoryBatchResult:
    """Ordered, failure-isolated outcomes for a maintenance plan."""

    results: tuple[RepositoryOperationResult, ...]

    @property
    def success_count(self) -> int:
        return sum(result.status == "success" for result in self.results)

    @property
    def skipped_count(self) -> int:
        return sum(result.status == "skipped" for result in self.results)

    @property
    def failed_count(self) -> int:
        return sum(result.status == "failed" for result in self.results)

    @property
    def ok(self) -> bool:
        return self.failed_count == 0


@dataclass(frozen=True)
class _ParsedSkill:
    name: str
    description: str
    scope: SkillScope
    path: Path
    content: str
    content_hash: str


@dataclass(frozen=True)
class _OperationSuccess:
    path: Path
    message: str
    target_written: bool = False
    file_deleted: bool = False
    directory_removed: bool = False
    deleted_sources: tuple[str, ...] = ()
    retained_sources: tuple[str, ...] = ()


class _RepositoryError(Exception):
    """Base class for an operation-local repository failure."""


class _ValidationError(_RepositoryError):
    pass


class _SecurityError(_RepositoryError):
    pass


class _ConflictError(_RepositoryError):
    pass


class _PartialMergeError(_RepositoryError):
    def __init__(
        self,
        message: str,
        *,
        path: Path,
        deleted_sources: Sequence[str],
        retained_sources: Sequence[str],
        cleanup_errors: Sequence[str],
    ) -> None:
        super().__init__(message)
        self.path = path
        self.deleted_sources = tuple(deleted_sources)
        self.retained_sources = tuple(retained_sources)
        self.cleanup_errors = tuple(cleanup_errors)


class _CommittedWriteError(_RepositoryError):
    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path


@dataclass(frozen=True)
class _RemoveOutcome:
    path: Path
    file_deleted: bool
    directory_removed: bool


class _PartialDeleteError(_RepositoryError):
    def __init__(
        self,
        message: str,
        *,
        outcome: _RemoveOutcome,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome


class SkillRepository:
    """Read global Skills and safely maintain only Workspace Skills."""

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
        self._write_lock = _workspace_lock_for(self.workspace_skills_dir)

    def snapshot(
        self,
        *,
        lifecycle_by_skill_key: Mapping[str, LifecycleStatus] | None = None,
        usage_by_skill_key: Mapping[str, SkillUsage] | None = None,
    ) -> tuple[SkillSnapshot, ...]:
        """Capture the visible global/Workspace Skill state and exact hashes."""
        lifecycle = lifecycle_by_skill_key or {}
        usage = usage_by_skill_key or {}
        visible = {
            skill.name: skill
            for skill in self._scan_scope(self.global_skills_dir, "global")
        }
        visible.update(
            {
                skill.name: skill
                for skill in self._scan_scope(
                    self.workspace_skills_dir,
                    "workspace",
                )
            }
        )
        snapshots: list[SkillSnapshot] = []
        for name in sorted(visible):
            skill = visible[name]
            skill_key = f"{skill.scope}:{name}"
            skill_usage = usage.get(skill_key) or usage.get(name)
            snapshots.append(
                SkillSnapshot(
                    name=name,
                    description=skill.description,
                    scope=skill.scope,
                    path=skill.path,
                    content=skill.content,
                    content_hash=skill.content_hash,
                    lifecycle_status=(
                        lifecycle.get(skill_key)
                        or lifecycle.get(name)
                        or "active"
                    ),
                    last_used_at=(
                        skill_usage.last_used_at
                        if skill_usage is not None
                        else None
                    ),
                    use_count=(
                        skill_usage.use_count
                        if skill_usage is not None
                        else 0
                    ),
                )
            )
        return tuple(snapshots)

    def apply(
        self,
        operations: Iterable[RepositoryOperation],
        analysis_snapshots: Iterable[SkillSnapshot],
    ) -> RepositoryBatchResult:
        """Apply operations in order without letting one failure abort the rest."""
        snapshots = tuple(analysis_snapshots)
        results: list[RepositoryOperationResult] = []
        for operation in operations:
            action = "unknown"
            target_name = ""
            try:
                action_value = _operation_field(operation, "action")
                action = getattr(action_value, "value", action_value)
                if not isinstance(action, str):
                    raise _ValidationError("operation action must be a string")
                target_value = _operation_field(
                    operation,
                    "target_name",
                    default="",
                )
                target_name = target_value or ""
                if action == "no_op":
                    results.append(
                        RepositoryOperationResult(
                            action=action,
                            target_name=str(target_name),
                            status="skipped",
                            message="maintenance plan requested no operation",
                        )
                    )
                    continue
                content = _operation_field(
                    operation,
                    "content",
                    default=None,
                )
                if action == "create":
                    result = self.create(
                        target_name,
                        content,
                        snapshots,
                    )
                elif action == "update":
                    result = self.update(
                        target_name,
                        content,
                        snapshots,
                    )
                elif action == "merge":
                    result = self.merge(
                        target_name,
                        _operation_field(
                            operation,
                            "source_names",
                            default=(),
                        ),
                        content,
                        snapshots,
                    )
                elif action == "delete":
                    result = self.delete(target_name, snapshots)
                else:
                    raise _ValidationError(
                        f"unsupported repository action: {action}"
                    )
            except Exception as error:  # isolate malformed operation objects
                self.logger.warning(
                    "Skill repository operation could not be dispatched: %s",
                    error,
                )
                result = RepositoryOperationResult(
                    action=str(action),
                    target_name=str(target_name),
                    status="failed",
                    message=str(error),
                )
            results.append(result)
        return RepositoryBatchResult(tuple(results))

    def create(
        self,
        target_name: str,
        content: str,
        analysis_snapshots: Iterable[SkillSnapshot] = (),
    ) -> RepositoryOperationResult:
        """Create a new Workspace Skill if it was absent during analysis."""
        return self._execute(
            "create",
            target_name,
            (),
            lambda name: self._create(
                name,
                content,
                self._snapshot_index(analysis_snapshots),
            ),
        )

    def update(
        self,
        target_name: str,
        content: str,
        analysis_snapshots: Iterable[SkillSnapshot],
    ) -> RepositoryOperationResult:
        """Update a visible Skill by writing a Workspace version."""
        return self._execute(
            "update",
            target_name,
            (),
            lambda name: self._update(
                name,
                content,
                self._snapshot_index(analysis_snapshots),
            ),
        )

    def merge(
        self,
        target_name: str,
        source_names: Sequence[str],
        content: str,
        analysis_snapshots: Iterable[SkillSnapshot],
    ) -> RepositoryOperationResult:
        """Write a merged target, then remove unchanged Workspace sources."""
        sources: tuple[str, ...]
        try:
            sources = tuple(source_names)
        except (TypeError, ValueError):
            sources = ()
        return self._execute(
            "merge",
            target_name,
            sources,
            lambda name: self._merge(
                name,
                sources,
                content,
                self._snapshot_index(analysis_snapshots),
            ),
        )

    def delete(
        self,
        target_name: str,
        analysis_snapshots: Iterable[SkillSnapshot],
    ) -> RepositoryOperationResult:
        """Delete an unchanged Workspace deletion candidate."""
        return self._execute(
            "delete",
            target_name,
            (),
            lambda name: self._delete(
                name,
                self._snapshot_index(analysis_snapshots),
            ),
        )

    def _execute(
        self,
        action: str,
        target_name: Any,
        source_names: Sequence[Any],
        operation: Any,
    ) -> RepositoryOperationResult:
        display_target = str(target_name)
        display_sources = tuple(str(source) for source in source_names)
        try:
            safe_target = _safe_name(target_name)
            outcome = operation(safe_target)
            return RepositoryOperationResult(
                action=action,
                target_name=safe_target,
                status="success",
                message=outcome.message,
                path=outcome.path,
                source_names=display_sources,
                target_written=outcome.target_written,
                file_deleted=outcome.file_deleted,
                directory_removed=outcome.directory_removed,
                deleted_sources=outcome.deleted_sources,
                retained_sources=outcome.retained_sources,
            )
        except _ConflictError as error:
            self.logger.info(
                "Skipping conflicting Skill %s operation for %s: %s",
                action,
                display_target,
                error,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="skipped",
                message=str(error),
                source_names=display_sources,
            )
        except _PartialMergeError as error:
            self.logger.warning(
                "Skill merge partially completed for %s: %s",
                display_target,
                error,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="failed",
                message=str(error),
                path=error.path,
                source_names=display_sources,
                target_written=True,
                deleted_sources=error.deleted_sources,
                retained_sources=error.retained_sources,
                cleanup_errors=error.cleanup_errors,
            )
        except _CommittedWriteError as error:
            self.logger.warning(
                "Skill %s content was committed for %s, but finalization failed: %s",
                action,
                display_target,
                error,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="failed",
                message=str(error),
                path=error.path,
                source_names=display_sources,
                target_written=True,
                retained_sources=(
                    display_sources if action == "merge" else ()
                ),
                cleanup_errors=(str(error),),
            )
        except _PartialDeleteError as error:
            self.logger.warning(
                "Skill %s deletion partially completed for %s: %s",
                action,
                display_target,
                error,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="failed",
                message=str(error),
                path=error.outcome.path,
                source_names=display_sources,
                file_deleted=error.outcome.file_deleted,
                directory_removed=error.outcome.directory_removed,
                deleted_sources=(
                    (display_target,)
                    if error.outcome.file_deleted
                    else ()
                ),
                retained_sources=(
                    ()
                    if error.outcome.file_deleted
                    else (display_target,)
                ),
                cleanup_errors=(str(error),),
            )
        except (_RepositoryError, OSError, UnicodeError, yaml.YAMLError) as error:
            self.logger.warning(
                "Skill %s operation failed for %s: %s",
                action,
                display_target,
                error,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="failed",
                message=str(error),
                source_names=display_sources,
            )
        except Exception as error:  # an unexpected failure remains operation-local
            self.logger.exception(
                "Unexpected Skill %s operation failure for %s",
                action,
                display_target,
            )
            return RepositoryOperationResult(
                action=action,
                target_name=display_target,
                status="failed",
                message=str(error),
                source_names=display_sources,
            )

    def _create(
        self,
        target_name: str,
        content: str,
        snapshots: Mapping[str, SkillSnapshot],
    ) -> _OperationSuccess:
        if target_name in snapshots:
            raise _ValidationError(
                f"create target {target_name!r} already existed during analysis"
            )
        parsed_content = _parse_content(content, target_name)
        with self._write_lock:
            try:
                current = self._read_visible(target_name)
            except _ValidationError as error:
                raise _ConflictError(
                    f"create target {target_name!r} appeared or became invalid "
                    "after analysis"
                ) from error
            if current is not None:
                raise _ConflictError(
                    f"create target {target_name!r} appeared after analysis"
                )
            path = self._atomic_write(target_name, parsed_content[0])
        return _OperationSuccess(
            path=path,
            message="Workspace Skill created",
            target_written=True,
        )

    def _update(
        self,
        target_name: str,
        content: str,
        snapshots: Mapping[str, SkillSnapshot],
    ) -> _OperationSuccess:
        expected = self._require_snapshot(target_name, snapshots, "update")
        parsed_content = _parse_content(content, target_name)
        with self._write_lock:
            self._verify_unchanged(expected, "update target")
            path = self._atomic_write(target_name, parsed_content[0])
        return _OperationSuccess(
            path=path,
            message="Workspace Skill updated",
            target_written=True,
        )

    def _merge(
        self,
        target_name: str,
        source_names: Sequence[str],
        content: str,
        snapshots: Mapping[str, SkillSnapshot],
    ) -> _OperationSuccess:
        normalized_sources = tuple(_safe_name(source) for source in source_names)
        if len(set(normalized_sources)) < 2:
            raise _ValidationError(
                "merge requires at least two distinct source names"
            )
        parsed_content = _parse_content(content, target_name)
        expected_sources = [
            self._require_snapshot(source, snapshots, "merge source")
            for source in normalized_sources
        ]
        with self._write_lock:
            for expected in expected_sources:
                self._verify_unchanged(expected, "merge source")

            expected_target = snapshots.get(target_name)
            if target_name not in set(normalized_sources):
                if expected_target is None:
                    try:
                        current_target = self._read_visible(target_name)
                    except _ValidationError as error:
                        raise _ConflictError(
                            f"merge target {target_name!r} appeared or became "
                            "invalid after analysis"
                        ) from error
                    if current_target is not None:
                        raise _ConflictError(
                            f"merge target {target_name!r} appeared after analysis"
                        )
                else:
                    self._verify_unchanged(expected_target, "merge target")

            path = self._atomic_write(target_name, parsed_content[0])
            cleanup_failures: list[str] = []
            deleted_sources: list[str] = []
            retained_sources: list[str] = []
            for source, expected in zip(
                normalized_sources,
                expected_sources,
                strict=True,
            ):
                if source == target_name or expected.scope == "global":
                    retained_sources.append(source)
                    continue
                if expected.lifecycle_status != "deletion_candidate":
                    retained_sources.append(source)
                    continue
                try:
                    self._verify_unchanged(expected, "merge cleanup source")
                    removal = self._remove_workspace_skill(source)
                    deleted_sources.append(source)
                except _PartialDeleteError as error:
                    if error.outcome.file_deleted:
                        deleted_sources.append(source)
                    else:
                        retained_sources.append(source)
                    cleanup_failures.append(f"{source}: {error}")
                except (
                    _RepositoryError,
                    OSError,
                    UnicodeError,
                    yaml.YAMLError,
                ) as error:
                    retained_sources.append(source)
                    cleanup_failures.append(f"{source}: {error}")
            if cleanup_failures:
                raise _PartialMergeError(
                    "merge target was written, but Workspace source cleanup "
                    "failed: " + "; ".join(cleanup_failures),
                    path=path,
                    deleted_sources=deleted_sources,
                    retained_sources=retained_sources,
                    cleanup_errors=cleanup_failures,
                )
        return _OperationSuccess(
            path=path,
            message="Workspace Skill merged",
            target_written=True,
            deleted_sources=tuple(deleted_sources),
            retained_sources=tuple(retained_sources),
        )

    def _delete(
        self,
        target_name: str,
        snapshots: Mapping[str, SkillSnapshot],
    ) -> _OperationSuccess:
        expected = self._require_snapshot(target_name, snapshots, "delete")
        if expected.scope != "workspace":
            raise _SecurityError("global Skills are read-only and cannot be deleted")
        if expected.lifecycle_status != "deletion_candidate":
            raise _ValidationError(
                "only Workspace Skills marked deletion_candidate may be deleted"
            )
        with self._write_lock:
            self._verify_unchanged(expected, "delete target")
            removal = self._remove_workspace_skill(target_name)
        return _OperationSuccess(
            path=removal.path,
            message="Workspace Skill deleted",
            file_deleted=removal.file_deleted,
            directory_removed=removal.directory_removed,
            deleted_sources=(target_name,),
        )

    def _require_snapshot(
        self,
        name: str,
        snapshots: Mapping[str, SkillSnapshot],
        role: str,
    ) -> SkillSnapshot:
        snapshot = snapshots.get(name)
        if snapshot is None:
            raise _ValidationError(
                f"{role} {name!r} was absent from the analysis snapshot"
            )
        return snapshot

    def _verify_unchanged(
        self,
        expected: SkillSnapshot,
        role: str,
    ) -> _ParsedSkill:
        try:
            current = self._read_visible(expected.normalized_name)
        except _ValidationError as error:
            raise _ConflictError(
                f"{role} {expected.normalized_name!r} became invalid after analysis"
            ) from error
        if current is None:
            raise _ConflictError(
                f"{role} {expected.normalized_name!r} disappeared after analysis"
            )
        if (
            current.content_hash != expected.content_hash
            or current.scope != expected.scope
        ):
            raise _ConflictError(
                f"{role} {expected.normalized_name!r} changed after analysis"
            )
        return current

    def _snapshot_index(
        self,
        snapshots: Iterable[SkillSnapshot],
    ) -> dict[str, SkillSnapshot]:
        index: dict[str, SkillSnapshot] = {}
        for snapshot in snapshots:
            name = _safe_name(snapshot.name)
            if snapshot.scope not in ("global", "workspace"):
                raise _ValidationError(
                    f"snapshot {name!r} has an invalid scope"
                )
            existing = index.get(name)
            if existing is None or snapshot.scope == "workspace":
                index[name] = snapshot
        return index

    def _scan_scope(
        self,
        root: Path,
        scope: SkillScope,
    ) -> tuple[_ParsedSkill, ...]:
        try:
            root_fd, _ = _open_directory_path(root, create=False)
        except (_RepositoryError, OSError) as error:
            self.logger.warning(
                "Skipping unsafe %s Skill root %s: %s",
                scope,
                root,
                error,
            )
            return ()
        if root_fd is None:
            return ()
        discovered: dict[str, _ParsedSkill] = {}
        try:
            entries = sorted(os.listdir(root_fd))
            for entry_name in entries:
                try:
                    entry_status = os.stat(
                        entry_name,
                        dir_fd=root_fd,
                        follow_symlinks=False,
                    )
                    if not (
                        stat.S_ISDIR(entry_status.st_mode)
                        or stat.S_ISLNK(entry_status.st_mode)
                    ):
                        # The global root also contains skill-state.sqlite3.
                        continue
                    name = _safe_name(entry_name)
                    if name != entry_name:
                        raise _ValidationError(
                            "Skill directory name is not normalized"
                        )
                    skill = self._read_scope_from_root_fd(
                        root_fd,
                        root,
                        scope,
                        name,
                    )
                    if skill is None:
                        continue
                    if name in discovered:
                        raise _ValidationError(
                            f"duplicate {scope} Skill name {name!r}"
                        )
                    discovered[name] = skill
                except (
                    _RepositoryError,
                    OSError,
                    UnicodeError,
                    yaml.YAMLError,
                ) as error:
                    self.logger.warning(
                        "Skipping invalid %s Skill entry %s: %s",
                        scope,
                        root / entry_name,
                        error,
                    )
        finally:
            os.close(root_fd)
        return tuple(discovered.values())

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
                raise _SecurityError(
                    f"Unable to open SKILL.md without following links: "
                    f"{root / name / 'SKILL.md'}"
                ) from error
            file_status = os.fstat(file_fd)
            if not stat.S_ISREG(file_status.st_mode):
                raise _ValidationError(
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

    def _atomic_write(self, name: str, content: bytes) -> Path:
        root_fd, _ = _open_directory_path(
            self.workspace_skills_dir,
            create=True,
            tighten_final=True,
        )
        assert root_fd is not None
        skill_fd = -1
        descriptor = -1
        temporary_name: str | None = None
        committed = False
        path = (self.workspace_skills_dir / name / "SKILL.md").absolute()
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
                    raise _SecurityError(
                        f"SKILL.md is a symlink: "
                        f"{self.workspace_skills_dir / name / 'SKILL.md'}"
                    )
                if not stat.S_ISREG(current_status.st_mode):
                    raise _ValidationError(
                        f"SKILL.md is not a regular file: "
                        f"{self.workspace_skills_dir / name / 'SKILL.md'}"
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
                    "durability or attachment verification failed",
                    path=path,
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

    def _remove_workspace_skill(self, name: str) -> _RemoveOutcome:
        root_fd, _ = _open_directory_path(
            self.workspace_skills_dir,
            create=False,
        )
        if root_fd is None:
            raise _ConflictError(
                f"Workspace Skill root disappeared while deleting {name}"
            )
        skill_fd = -1
        path = (self.workspace_skills_dir / name / "SKILL.md").absolute()
        file_deleted = False
        directory_removed = False
        try:
            opened_skill_fd = _open_skill_directory(root_fd, name, create=False)
            if opened_skill_fd is None:
                raise _ConflictError(
                    f"Workspace Skill directory disappeared: {name}"
                )
            skill_fd = opened_skill_fd
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            try:
                file_status = os.stat(
                    "SKILL.md",
                    dir_fd=skill_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError as error:
                raise _ConflictError(
                    f"Workspace SKILL.md disappeared: {name}"
                ) from error
            if stat.S_ISLNK(file_status.st_mode):
                raise _SecurityError(
                    f"refusing to delete a symlinked Workspace Skill: {name}"
                )
            if not stat.S_ISREG(file_status.st_mode):
                raise _ValidationError(
                    f"SKILL.md is not a regular file: "
                    f"{self.workspace_skills_dir / name / 'SKILL.md'}"
                )
            _ensure_skill_directory_attached(root_fd, name, skill_fd)
            os.unlink("SKILL.md", dir_fd=skill_fd)
            file_deleted = True
            try:
                os.fsync(skill_fd)
                _ensure_skill_directory_attached(root_fd, name, skill_fd)
                try:
                    os.rmdir(name, dir_fd=root_fd)
                except OSError as error:
                    if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                        raise
                else:
                    directory_removed = True
                    os.fsync(root_fd)
            except Exception as error:
                raise _PartialDeleteError(
                    "SKILL.md was deleted, but directory durability or cleanup "
                    "failed",
                    outcome=_RemoveOutcome(
                        path=path,
                        file_deleted=file_deleted,
                        directory_removed=directory_removed,
                    ),
                ) from error
        finally:
            if skill_fd >= 0:
                os.close(skill_fd)
            os.close(root_fd)
        return _RemoveOutcome(
            path=path,
            file_deleted=file_deleted,
            directory_removed=directory_removed,
        )

    def _workspace_skill_file(self, name: str) -> Path:
        return self._skill_paths(self.workspace_skills_dir, name)[1]

    def _skill_paths(self, root: Path, name: str) -> tuple[Path, Path]:
        safe_name = _safe_name(name)
        skill_dir = root / safe_name
        skill_file = skill_dir / "SKILL.md"
        _assert_within(root, skill_dir)
        _assert_within(root, skill_file)
        return skill_dir, skill_file

def _safe_name(value: Any) -> str:
    if not isinstance(value, str):
        raise _ValidationError("Skill name must be a string")
    normalized = normalize_skill_name(value)
    if not _SAFE_NAME.fullmatch(normalized):
        raise _SecurityError(
            "Skill name must contain only lowercase ASCII letters, digits, "
            "hyphens, or underscores and be at most 64 characters"
        )
    return normalized


def _parse_content(content: Any, target_name: str) -> tuple[bytes, str]:
    if not isinstance(content, str):
        raise _ValidationError("SKILL.md content must be UTF-8 text")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _ValidationError("SKILL.md content must be UTF-8 text") from error
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise _ValidationError("SKILL.md must start with YAML front matter")
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise _ValidationError(
            "SKILL.md YAML front matter is missing its closing delimiter"
        ) from error
    metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
    if not isinstance(metadata, dict):
        raise _ValidationError("SKILL.md YAML front matter must be a mapping")
    metadata_name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(metadata_name, str) or not metadata_name.strip():
        raise _ValidationError("SKILL.md name must be a non-empty string")
    if _safe_name(metadata_name) != target_name:
        raise _ValidationError(
            "SKILL.md YAML name must match the repository target name"
        )
    if not isinstance(description, str) or not description.strip():
        raise _ValidationError(
            "SKILL.md description must be a non-empty string"
        )
    return encoded, description.strip()


def _assert_within(root: Path, candidate: Path) -> None:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise _SecurityError(
            f"Skill path escapes its configured root: {candidate}"
        ) from error


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


def _workspace_lock_for(root: Path) -> threading.RLock:
    """Return the process-local lock shared by Repository instances."""
    canonical = root.resolve(strict=False)
    with _WORKSPACE_LOCKS_GUARD:
        lock = _WORKSPACE_LOCKS.get(canonical)
        if lock is None:
            lock = threading.RLock()
            _WORKSPACE_LOCKS[canonical] = lock
        return lock


def _open_directory_path(
    path: Path,
    *,
    create: bool,
    tighten_final: bool = False,
) -> tuple[int | None, bool]:
    """Open a directory hierarchy without following any symlink.

    Returns the final directory descriptor and whether the final component was
    created. ``None`` means a non-creating lookup found no directory.
    """
    absolute = path.absolute()
    parts = absolute.parts
    if not parts:
        raise _SecurityError("Skill root path cannot be empty")
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
                raise _SecurityError(
                    f"Directory path contains a symlink or unsafe component: {path}"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        final_status = os.fstat(current_fd)
        if not stat.S_ISDIR(final_status.st_mode):
            raise _ValidationError(f"Skill root is not a directory: {path}")
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
        raise _SecurityError(
            f"Skill directory is a symlink or unsafe entry: {safe_name}"
        ) from error
    try:
        if not stat.S_ISDIR(os.fstat(skill_fd).st_mode):
            raise _ValidationError(
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
    """Reject a directory that was exchanged after its FD was opened."""
    try:
        attached_status = os.stat(
            _safe_name(name),
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise _ConflictError(
            f"Workspace Skill directory was detached during operation: {name}"
        ) from error
    open_status = os.fstat(skill_fd)
    if stat.S_ISLNK(attached_status.st_mode) or (
        attached_status.st_dev,
        attached_status.st_ino,
    ) != (open_status.st_dev, open_status.st_ino):
        raise _ConflictError(
            f"Workspace Skill directory was exchanged during operation: {name}"
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


def _fsync_directory(directory: Path) -> None:
    descriptor, _ = _open_directory_path(directory, create=False)
    if descriptor is None:
        raise FileNotFoundError(directory)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_MISSING = object()


def _operation_field(
    operation: RepositoryOperation,
    name: str,
    *,
    default: Any = _MISSING,
) -> Any:
    if isinstance(operation, Mapping):
        if name in operation:
            return operation[name]
    elif hasattr(operation, name):
        return getattr(operation, name)
    if default is not _MISSING:
        return default
    raise _ValidationError(f"operation is missing required field {name!r}")
