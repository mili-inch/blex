from __future__ import annotations

import importlib
import stat
import sys
import tomllib
import traceback
from pathlib import Path
from typing import Any

bpy: Any = importlib.import_module("bpy")


_MANIFEST_ID: str = ""  # BLEX-CONFIG manifest-id
_REPOSITORY_ROOT: str = ""  # BLEX-CONFIG repository-root
_RELOAD_PANEL: bool = False  # BLEX-CONFIG reload-panel
_DEBUGPY_ENABLED: bool = False  # BLEX-CONFIG debugpy-enabled
_DEBUGPY_ROOT: str = ""  # BLEX-CONFIG debugpy-root
_DEBUGPY_HOST: str = ""  # BLEX-CONFIG debugpy-host
_DEBUGPY_PORT: int = 0  # BLEX-CONFIG debugpy-port

_REPOSITORY_MODULE = "blex_dev"
_REPOSITORY_NAME = "Blex Development"
_MODULE_NAME = f"bl_ext.{_REPOSITORY_MODULE}.{_MANIFEST_ID}"


def _operation_finished(result: set[str], label: str) -> None:
    if "FINISHED" not in result:
        raise RuntimeError(f"{label} did not finish: {sorted(result)!r}")


def _read_manifest_id(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"could not inspect extension manifest: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"could not parse extension manifest: {path}") from exc
    manifest_id = document.get("id")
    if not isinstance(manifest_id, str):
        raise RuntimeError(f"extension manifest has no string ID: {path}")
    return manifest_id


def _repository_contains_manifest(repository: Any, manifest_id: str) -> bool:
    directory_text = repository.directory
    if not directory_text:
        return False
    directory = Path(directory_text)

    try:
        children = tuple(directory.iterdir())
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(f"could not inspect extension repository: {directory}") from exc
    for child in children:
        try:
            child_mode = child.stat().st_mode
        except OSError as exc:
            raise RuntimeError(f"could not inspect extension repository entry: {child}") from exc
        if not stat.S_ISDIR(child_mode):
            continue
        candidate = child / "blender_manifest.toml"
        if _read_manifest_id(candidate) == manifest_id:
            return True
    return False


def _create_repository() -> None:
    repositories = bpy.context.preferences.extensions.repos
    existing = tuple(repositories)
    for repository in existing:
        if repository.module == _REPOSITORY_MODULE:
            raise RuntimeError(f"extension repository module already exists: {_REPOSITORY_MODULE}")
    for repository in existing:
        if _repository_contains_manifest(repository, _MANIFEST_ID):
            raise RuntimeError(
                f"extension ID {_MANIFEST_ID!r} already exists in repository {repository.module!r}"
            )

    repository = repositories.new(
        name=_REPOSITORY_NAME,
        module=_REPOSITORY_MODULE,
        custom_directory=_REPOSITORY_ROOT,
        remote_url="",
        source="USER",
    )
    repository.enabled = True
    repository.use_custom_directory = True
    repository.use_remote_url = False
    repository.use_sync_on_startup = False

    _operation_finished(bpy.ops.extensions.repo_refresh_all(), "local repository refresh")


def _start_debugpy() -> None:
    if not _DEBUGPY_ENABLED:
        return
    imported = sorted(
        name for name in sys.modules if name == "debugpy" or name.startswith("debugpy.")
    )
    if imported:
        raise RuntimeError(f"debugpy was already imported before bootstrap: {imported!r}")
    sys.path.insert(0, _DEBUGPY_ROOT)
    debugpy = importlib.import_module("debugpy")
    if getattr(debugpy, "__version__", None) != "1.8.21":
        raise RuntimeError("bundled debugpy version does not match 1.8.21")
    module_file = debugpy.__file__
    if module_file is None:
        raise RuntimeError("bundled debugpy module has no file path")
    module_path = Path(module_file).resolve(strict=True)
    expected_root = Path(_DEBUGPY_ROOT).resolve(strict=True)
    if module_path != expected_root and expected_root not in module_path.parents:
        raise RuntimeError(f"debugpy was imported outside the bundled directory: {module_path}")
    debugpy.listen((_DEBUGPY_HOST, _DEBUGPY_PORT))


def _reload_extension() -> None:
    _operation_finished(
        bpy.ops.preferences.addon_disable(module=_MODULE_NAME),
        "extension disable",
    )
    importlib.invalidate_caches()
    purge_module_names = sorted(
        [
            name
            for name in sys.modules
            if name == _MODULE_NAME or name.startswith(_MODULE_NAME + ".")
        ],
        key=lambda name: name.count("."),
        reverse=True,
    )

    for purge_module_name in purge_module_names:
        sys.modules.pop(purge_module_name, None)

    print(f"[reload_extension] purged modules: {len(purge_module_names)}")
    for purge_module_name in purge_module_names:
        print(f"  - {purge_module_name}")

    _operation_finished(
        bpy.ops.preferences.addon_enable(module=_MODULE_NAME),
        "extension enable",
    )


class BLEX_OT_reload_extension(bpy.types.Operator):
    bl_idname = "blex.reload_extension"
    bl_label = "Reload"

    def execute(self: Any, _context: Any) -> set[str]:
        try:
            _reload_extension()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.report({"ERROR"}, message)
            print(f"[reload_extension] failed: {message}", file=sys.stderr)
            traceback.print_exc()
            return {"CANCELLED"}
        return {"FINISHED"}


class BLEX_PT_reload_extension(bpy.types.Panel):
    bl_idname = "BLEX_PT_reload_extension"
    bl_label = "Reload Extension"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reload"

    def draw(self: Any, _context: Any) -> None:
        self.layout.operator("blex.reload_extension", text="Reload")


def _register_reload_panel() -> None:
    bpy.utils.register_class(BLEX_OT_reload_extension)
    bpy.utils.register_class(BLEX_PT_reload_extension)


def main() -> None:
    _create_repository()
    _start_debugpy()
    _operation_finished(
        bpy.ops.preferences.addon_enable(module=_MODULE_NAME),
        "extension enable",
    )
    if _RELOAD_PANEL:
        _register_reload_panel()


main()
