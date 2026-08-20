import importlib
from typing import Any

bpy: Any = importlib.import_module("bpy")


class BLEX_CLASS_PREFIX_PT_hello(bpy.types.Panel):
    bl_idname = "BLEX_PANEL_ID"
    bl_label = "BLEX_DISPLAY_NAME"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BLEX_DISPLAY_NAME"

    def draw(self, _context: Any) -> None:
        self.layout.operator("BLEX_OPERATOR_ID", icon="INFO")
