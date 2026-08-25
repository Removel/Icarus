"""Restricted internal tools used only by Skill generation Agents."""

import asyncio
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any

from apps.agent.src.agent_orchestration.plugins.skill.generation_context import (
    SkillGenerationContext,
    get_generation_context,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    MAX_FILE_BYTES,
)
from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolDefinition


GENERATION_TOOL_NAMES = ["read", "write", "copy", "remove", "bash"]
_READ_ROOT_NAMES = ("draft", "workspace", "workspace_skills", "global_skills")
_MAX_READ_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
_READ_CHUNK_BYTES = 64 * 1024
_SENSITIVE_NAMES = frozenset(
    {
        ".aws", ".docker", ".env", ".git", ".git-credentials", ".netrc",
        ".npmrc", ".pypirc", ".ssh", "credentials", "id_rsa", "id_ed25519",
    }
)
_FORBIDDEN_SHELL = re.compile(r"[;&|<>`\n\r]|\$\(")
_FORBIDDEN_PROGRAMS = frozenset(
    {
        "curl", "wget", "ssh", "scp", "nc", "netcat",
        "pip", "pip3", "npm", "npx", "yarn", "pnpm",
        "bun", "brew", "apt", "apt-get", "sudo", "su",
        "rm", "rmdir", "mv", "cp", "chmod", "chown",
        "kill", "pkill", "git", "open", "launchctl",
    }
)
_ALLOWED_PROGRAMS = frozenset(
    {"python", "python3", "node", "bash", "sh"}
)


def create_generation_tools() -> list[BaseTool]:
    return [
        GenerationReadTool(),
        GenerationWriteTool(),
        GenerationCopyTool(),
        GenerationRemoveTool(),
        GenerationBashTool(),
    ]


class GenerationReadTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=(
                "Read one UTF-8 file or list one directory. Relative paths "
                "resolve inside the current Skill Draft; absolute paths are "
                "limited to the current Workspace and global Skills."
            ),
            input_schema=_object_schema(
                {
                    "path": {"type": "string", "description": "Path to inspect"},
                    "root": {
                        "type": "string",
                        "enum": list(_READ_ROOT_NAMES),
                        "description": "Logical root for a relative path; defaults to draft",
                    },
                },
                ["path"],
            ),
        )

    def invoke(self, arguments: dict[str, Any], **execution) -> ToolExecutionResult:
        del execution
        try:
            path = _readable_path(
                _required_string(arguments, "path"),
                root_name=arguments.get("root", "draft"),
            )
            if path.is_dir():
                entries = []
                for child in sorted(path.iterdir(), key=lambda item: item.name):
                    entries.append(
                        {
                            "name": child.name,
                            "type": (
                                "symlink" if child.is_symlink() else
                                "directory" if child.is_dir() else "file"
                            ),
                        }
                    )
                    if len(entries) >= 256:
                        break
                return ToolExecutionResult(
                    success=True,
                    output={"path": str(path), "entries": entries},
                )
            if path.stat().st_size > _MAX_READ_BYTES:
                raise ValueError("file is too large for text read")
            return ToolExecutionResult(
                success=True,
                output={
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8"),
                },
            )
        except (OSError, UnicodeError, ValueError) as error:
            return ToolExecutionResult(success=False, error=str(error))

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class GenerationWriteTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description="Create or replace one UTF-8 text file inside the Skill Draft",
            input_schema=_object_schema(
                {
                    "path": {"type": "string", "description": "Draft-relative path"},
                    "content": {"type": "string", "description": "Complete file content"},
                },
                ["path", "content"],
            ),
        )

    def invoke(self, arguments: dict[str, Any], **execution) -> ToolExecutionResult:
        del execution
        try:
            relative = _required_string(arguments, "path")
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError(
                    f"file exceeds the {MAX_FILE_BYTES}-byte Draft limit"
                )
            path = _draft_path(relative, create_parent=True)
            path.write_bytes(encoded)
            path.chmod(0o600)
            return ToolExecutionResult(
                success=True,
                output={"path": relative, "bytes_written": len(encoded)},
            )
        except (OSError, UnicodeError, ValueError) as error:
            return ToolExecutionResult(success=False, error=str(error))


class GenerationCopyTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="copy",
            description="Copy one readable text or binary file into the Skill Draft",
            input_schema=_object_schema(
                {
                    "source": {"type": "string", "description": "Readable source path"},
                    "source_root": {
                        "type": "string",
                        "enum": list(_READ_ROOT_NAMES),
                        "description": "Logical root for a relative source; defaults to draft",
                    },
                    "path": {"type": "string", "description": "Draft-relative destination"},
                },
                ["source", "path"],
            ),
        )

    def invoke(self, arguments: dict[str, Any], **execution) -> ToolExecutionResult:
        del execution
        try:
            source = _readable_path(
                _required_string(arguments, "source"),
                root_name=arguments.get("source_root", "draft"),
            )
            if not source.is_file():
                raise ValueError("source must be a regular file")
            source_size = source.stat().st_size
            if source_size > MAX_FILE_BYTES:
                raise ValueError(
                    f"source exceeds the {MAX_FILE_BYTES}-byte Draft limit"
                )
            relative = _required_string(arguments, "path")
            destination = _draft_path(relative, create_parent=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            destination.chmod(0o600)
            return ToolExecutionResult(
                success=True,
                output={"path": relative, "bytes_written": destination.stat().st_size},
            )
        except (OSError, ValueError) as error:
            return ToolExecutionResult(success=False, error=str(error))


class GenerationRemoveTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="remove",
            description="Remove one file or empty directory from the Skill Draft",
            input_schema=_object_schema(
                {"path": {"type": "string", "description": "Draft-relative path"}},
                ["path"],
            ),
        )

    def invoke(self, arguments: dict[str, Any], **execution) -> ToolExecutionResult:
        del execution
        try:
            relative = _required_string(arguments, "path")
            path = _draft_path(relative)
            if path.is_symlink():
                raise ValueError("refusing to remove a symlink")
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
            return ToolExecutionResult(success=True, output={"path": relative})
        except (OSError, ValueError) as error:
            return ToolExecutionResult(success=False, error=str(error))


class GenerationBashTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Run a restricted validation command in the Skill Draft. "
                "Direct network and dependency-install commands, shell "
                "composition, and explicit paths outside the Draft are rejected. "
                "This is a practical guardrail, not an OS sandbox."
            ),
            input_schema=_object_schema(
                {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": _MAX_TIMEOUT,
                    },
                },
                ["command"],
            ),
        )

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        process: subprocess.Popen[bytes] | None = None
        try:
            command = _required_string(arguments, "command")
            timeout = _validate_timeout(
                arguments.get("timeout", _DEFAULT_TIMEOUT)
            )
            argv = _validate_command(command)
            context = get_generation_context()
            process = subprocess.Popen(
                argv,
                cwd=context.draft_dir,
                env=_safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = _communicate_bounded(
                    process, timeout=timeout
                )
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                return ToolExecutionResult(
                    success=False,
                    error=f"Command timed out after {timeout:g} seconds",
                )
            except _OutputLimitExceeded as error:
                _terminate_process_group(process)
                return ToolExecutionResult(success=False, error=str(error))
            output = {
                "exit_code": process.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "truncated": False,
            }
            return ToolExecutionResult(
                success=process.returncode == 0,
                output=output,
                error=(
                    None if process.returncode == 0
                    else output["stderr"] or "Command failed"
                ),
            )
        except (OSError, ValueError) as error:
            if process is not None and process.poll() is None:
                _terminate_process_group(process)
            return ToolExecutionResult(success=False, error=str(error))

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        process: asyncio.subprocess.Process | None = None
        operations: list[asyncio.Task[Any]] = []
        try:
            command = _required_string(arguments, "command")
            timeout = _validate_timeout(arguments.get("timeout", _DEFAULT_TIMEOUT))
            argv = _validate_command(command)
            context = get_generation_context()
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=context.draft_dir,
                env=_safe_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert process.stdout is not None and process.stderr is not None
            operations = [
                asyncio.create_task(_read_stream_bounded(process.stdout)),
                asyncio.create_task(_read_stream_bounded(process.stderr)),
                asyncio.create_task(process.wait()),
            ]
            try:
                stdout, stderr, return_code = await asyncio.wait_for(
                    asyncio.gather(*operations), timeout=timeout
                )
            except TimeoutError:
                await asyncio.shield(_aterminate_process_group(process))
                return ToolExecutionResult(
                    success=False,
                    error=f"Command timed out after {timeout:g} seconds",
                )
            except _OutputLimitExceeded as error:
                await asyncio.shield(_aterminate_process_group(process))
                return ToolExecutionResult(success=False, error=str(error))
            output = {
                "exit_code": return_code,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "truncated": False,
            }
            return ToolExecutionResult(
                success=return_code == 0,
                output=output,
                error=None if return_code == 0 else output["stderr"] or "Command failed",
            )
        except asyncio.CancelledError:
            if process is not None:
                await asyncio.shield(_aterminate_process_group(process))
            raise
        except (OSError, ValueError) as error:
            if process is not None and process.returncode is None:
                await _aterminate_process_group(process)
            return ToolExecutionResult(success=False, error=str(error))
        finally:
            for operation in operations:
                if not operation.done():
                    operation.cancel()
            if operations:
                await asyncio.gather(*operations, return_exceptions=True)


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _required_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _validate_timeout(value: Any) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 < value <= _MAX_TIMEOUT
    ):
        raise ValueError(
            f"timeout must be between 0 and {_MAX_TIMEOUT:g} seconds"
        )
    return float(value)


def _readable_path(value: str, *, root_name: Any = "draft") -> Path:
    context = get_generation_context()
    if not isinstance(root_name, str) or root_name not in _READ_ROOT_NAMES:
        raise ValueError(f"root must be one of: {', '.join(_READ_ROOT_NAMES)}")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError("relative read paths cannot contain '..'")
        roots = {
            "draft": context.draft_dir,
            "workspace": context.workspace_dir,
            "workspace_skills": context.workspace_skills_dir,
            "global_skills": context.global_skills_dir,
        }
        selected_root = roots[root_name]
        candidate = selected_root / candidate
    else:
        selected_root = None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"path is not readable: {value}") from error
    if selected_root is not None and not resolved.is_relative_to(selected_root):
        raise ValueError("path escapes the selected Skill generation root")
    if selected_root is None and not any(
        resolved.is_relative_to(root) for root in context.readable_roots
    ):
        raise ValueError("path is outside the readable Skill generation roots")
    if any(_is_sensitive_name(part) for part in resolved.parts):
        raise ValueError("path refers to a protected credential file")
    return resolved


def _draft_path(value: str, *, create_parent: bool = False) -> Path:
    context = get_generation_context()
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Draft path must be a safe relative path")
    candidate = context.draft_dir.joinpath(relative)
    parent = candidate.parent
    current = context.draft_dir
    for part in parent.relative_to(context.draft_dir).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("Draft path contains a symlink")
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved_parent = parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(context.draft_dir):
        raise ValueError("Draft path escapes the active Draft")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("Draft path is a symlink")
    return candidate


def _validate_command(command: str) -> list[str]:
    if len(command) > 4096:
        raise ValueError("command is too long")
    if _FORBIDDEN_SHELL.search(command):
        raise ValueError("shell composition and redirection are not allowed")
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise ValueError("command has invalid shell quoting") from error
    if not argv:
        raise ValueError("command cannot be empty")
    program = Path(argv[0]).name.casefold()
    if argv[0].casefold() != program:
        raise ValueError("program must be invoked by its allowlisted name")
    if program in _FORBIDDEN_PROGRAMS or program not in _ALLOWED_PROGRAMS:
        raise ValueError(f"program is not allowed: {program}")
    if any(
        Path(argument).is_absolute() or ".." in Path(argument).parts
        for argument in argv[1:]
    ):
        raise ValueError("command paths must stay inside the Draft")
    if program in {"bash", "sh"} and (len(argv) < 3 or argv[1] != "-n"):
        raise ValueError("bash and sh are limited to syntax checking with -n")
    if program.startswith("python") and "-c" in argv:
        raise ValueError("inline Python is not allowed")
    if program.startswith("python") and "-m" in argv:
        module_index = argv.index("-m") + 1
        if module_index >= len(argv) or argv[module_index] != "py_compile":
            raise ValueError("only python -m py_compile is allowed")
    if program == "node" and any(
        argument in {"-e", "--eval", "-p", "--print"}
        for argument in argv[1:]
    ):
        raise ValueError("inline Node.js is not allowed")
    return argv


def _is_sensitive_name(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in _SENSITIVE_NAMES
        or normalized.startswith(".env.")
        or normalized.startswith("credentials.")
    )


def _safe_environment() -> dict[str, str]:
    return {
        name: value
        for name in ("PATH", "LANG", "LC_ALL")
        if (value := os.environ.get(name)) is not None
    }


class _OutputLimitExceeded(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"Command output exceeded the {_MAX_OUTPUT_BYTES}-byte per-stream limit"
        )


def _communicate_bounded(
    process: subprocess.Popen[bytes], *, timeout: float
) -> tuple[bytes, bytes]:
    assert process.stdout is not None and process.stderr is not None
    streams = (process.stdout, process.stderr)
    buffers = {stream.fileno(): bytearray() for stream in streams}
    selector = selectors.DefaultSelector()
    started_at = time.monotonic()
    try:
        for stream in streams:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            ready = selector.select(remaining)
            if not ready:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in ready:
                descriptor = key.fileobj.fileno()
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[descriptor].extend(chunk)
                if len(buffers[descriptor]) > _MAX_OUTPUT_BYTES:
                    raise _OutputLimitExceeded()
        remaining = timeout - (time.monotonic() - started_at)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        process.wait(timeout=remaining)
        return bytes(buffers[streams[0].fileno()]), bytes(
            buffers[streams[1].fileno()]
        )
    finally:
        selector.close()
        for stream in streams:
            stream.close()


async def _read_stream_bounded(
    stream: asyncio.StreamReader,
) -> bytes:
    output = bytearray()
    while chunk := await stream.read(_READ_CHUNK_BYTES):
        output.extend(chunk)
        if len(output) > _MAX_OUTPUT_BYTES:
            raise _OutputLimitExceeded()
    return bytes(output)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


async def _aterminate_process_group(
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=1)
    except (ProcessLookupError, TimeoutError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
