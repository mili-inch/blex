import importlib
from typing import Any

bpy: Any = importlib.import_module("bpy")


class BLEX_CLASS_PREFIX_OT_hello(bpy.types.Operator):
    bl_idname = "BLEX_OPERATOR_ID"
    bl_label = "Say Hello"

    def execute(self, _context: Any) -> set[str]:
        self.report({"INFO"}, "BLEX_REPORT_MESSAGE")
        return {"FINISHED"}
