"""Plugin configuration loader.

Reads plugin.toml and returns a PluginConfig dataclass. The VST3 class ID
is converted to the 4-field signed int32 representation used in ADG files.
"""

import struct
import tomllib
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginConfig:
    # [plugin]
    name: str
    vendor: str
    vst3_class_id: str
    mpe_enabled: int          # 1 = MPE on, 2 = MPE off/default
    controller_state: str     # "empty" | "same"

    # [pack]
    pack_id: str
    pack_display_name: str
    pack_vendor: str
    pack_major_version: int
    pack_minor_version: int
    pack_revision: int
    product_id: int

    # [input]
    nksf_dirs: list[Path]

    # [output]
    pack_dir: Path

    # [category_map]
    category_map: dict = field(default_factory=dict)

    @property
    def uid_fields(self) -> tuple[int, int, int, int]:
        """VST3 class ID UUID as 4 big-endian signed int32 values."""
        b = _uuid.UUID(self.vst3_class_id).bytes
        return struct.unpack(">4i", b)

    @property
    def browser_content_path(self) -> str:
        return f"view:X-Plugins#{self.vendor}:{self.name}"

    @property
    def branch_device_id(self) -> str:
        return f"device:vst3:instr:{self.vst3_class_id}"


def load(path: Path) -> PluginConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    plugin = raw["plugin"]
    pack = raw.get("pack", {})
    inp = raw["input"]
    out = raw["output"]
    category_map = raw.get("category_map", {})

    controller_state = plugin.get("controller_state", "empty")
    if controller_state not in ("empty", "same"):
        raise ValueError(
            f"plugin.controller_state must be 'empty' or 'same', got {controller_state!r}"
        )

    mpe_enabled = plugin.get("mpe_enabled", 2)
    if mpe_enabled not in (1, 2):
        raise ValueError(f"plugin.mpe_enabled must be 1 or 2, got {mpe_enabled!r}")

    # Validate VST3 class ID is a parseable UUID
    try:
        _uuid.UUID(plugin["vst3_class_id"])
    except ValueError:
        raise ValueError(
            f"plugin.vst3_class_id is not a valid UUID: {plugin['vst3_class_id']!r}"
        )

    def _to_paths(value) -> list[Path]:
        """Accept a single string or a list of strings; return list[Path]."""
        if isinstance(value, list):
            return [Path(p).expanduser() for p in value]
        return [Path(value).expanduser()]

    return PluginConfig(
        name=plugin["name"],
        vendor=plugin["vendor"],
        vst3_class_id=plugin["vst3_class_id"],
        mpe_enabled=mpe_enabled,
        controller_state=controller_state,
        pack_id=pack.get("id", f"{plugin['vendor'].lower()}/{plugin['name'].lower()}"),
        pack_display_name=pack.get("display_name", plugin["name"]),
        pack_vendor=pack.get("vendor", plugin["vendor"]),
        pack_major_version=pack.get("major_version", 1),
        pack_minor_version=pack.get("minor_version", 0),
        pack_revision=pack.get("revision", 1),
        product_id=pack.get("product_id", 0),
        nksf_dirs=_to_paths(inp["nksf_dir"]),
        pack_dir=Path(out["pack_dir"]).expanduser(),
        category_map=category_map,
    )
