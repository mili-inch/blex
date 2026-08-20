import importlib
from typing import Any

from .operators import BLEX_CLASS_PREFIX_OT_hello
from .panels import BLEX_CLASS_PREFIX_PT_hello

bpy: Any = importlib.import_module("bpy")

_CLASSES = (
    BLEX_CLASS_PREFIX_OT_hello,
    BLEX_CLASS_PREFIX_PT_hello,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
