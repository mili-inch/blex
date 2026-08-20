from __future__ import annotations

import ntpath
import os
import shlex
import shutil
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from blex.core.errors import BlexError


def resolve_blender_executable(value: str, project_root: Path) -> Path:
    """Resolve an explicit Blender path or a Blender command on PATH."""
    if not value:
        raise BlexError("Blender executable must not be empty")
    if "\x00" in value:
        raise BlexError("Blender executable must not contain NUL")

    drive, drive_tail = ntpath.splitdrive(value)
    if drive and not drive_tail.startswith(("/", "\\")):
        raise BlexError(
            f"Blender executable uses a drive-relative path: {value!r}; "
            "use an absolute or explicit relative path"
        )

    is_path = Path(value).is_absolute() or "/" in value or "\\" in value
    if is_path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BlexError(f"Blender executable does not exist: {candidate}") from exc
    else:
        located = shutil.which(value)
        if located is None:
            raise BlexError(f"Blender executable was not found on PATH: {value}")
        try:
            resolved = Path(located).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BlexError(f"Blender executable does not exist: {located}") from exc

    if resolved.suffix.lower() == ".app":
        raise BlexError(
            f"Blender .app bundles are not executable paths: {resolved}; "
            "specify the executable inside the bundle"
        )
    if not resolved.is_file():
        raise BlexError(f"Blender executable is not a regular file: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise BlexError(f"Blender executable is not executable: {resolved}")
    return resolved


def blender_command(
    executable: Path,
    *,
    blend_file: Path | None,
    bootstrap_script: Path,
    extra_arguments: Sequence[str],
) -> tuple[str, ...]:
    command = [str(executable)]
    if blend_file is not None:
        command.append(str(blend_file))
    command.extend(("--python-exit-code", "1", "--python", str(bootstrap_script)))
    command.extend(extra_arguments)
    return tuple(command)


def format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _signal_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is not None:
        return
    try:
        if os.name == "nt":
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(child.pid, signal.SIGINT)
    except ProcessLookupError:
        return
    except OSError:
        # The process group can disappear between poll() and signal delivery.
        if child.poll() is None:
            raise


def _stop_interrupted_child(child: subprocess.Popen[str]) -> None:
    try:
        _signal_child(child)
    except OSError as exc:
        print(
            f"warning: failed to send an interrupt signal to Blender; continuing shutdown: {exc}",
            file=sys.stderr,
        )

    try:
        child.wait(timeout=5)
        return
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass

    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=5)
        return
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass

    if child.poll() is None:
        child.kill()
    while child.poll() is None:
        try:
            child.wait()
        except KeyboardInterrupt:
            continue


def run_blender(command: Sequence[str], *, environment: Mapping[str, str]) -> int:
    """Run Blender in a separate process group and supervise Ctrl+C shutdown."""
    try:
        if os.name == "nt":
            child = subprocess.Popen(
                command,
                env=dict(environment),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                text=True,
            )
        else:
            child = subprocess.Popen(
                command,
                env=dict(environment),
                start_new_session=True,
                text=True,
            )
    except OSError as exc:
        raise BlexError(f"failed to start Blender: {exc}") from exc

    try:
        return child.wait()
    except KeyboardInterrupt:
        _stop_interrupted_child(child)
        return 130
