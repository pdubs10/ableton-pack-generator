"""Ableton Custom Pack assembler.

Builds the pack directory structure from a set of NKSF presets.

Pack layout:
  <PackName>/
  ├── Ableton Folder Info/
  │   ├── Properties.cfg
  │   └── Previews/
  │       └── <Category>/
  │           └── <PresetName>.adg.ogg
  └── <Category>/
      └── <PresetName>.adg
"""

import shutil
from pathlib import Path

from . import adg, nksf
from .config import PluginConfig

_PROPERTIES_TEMPLATE = """\
Ableton#04I

FolderConfigData
{{
  String PackUniqueID = "{pack_id}";
  String PackDisplayName = "{display_name}";
  String PackVendor = "{vendor}";
  Int PackMinorVersion = {minor_version};
  Int PackMajorVersion = {major_version};
  Int PackRevision = {revision};
  Int ProductId = {product_id};
  Int MinSoftwareProductId = 3;
}}
"""


def _properties_cfg(plugin: PluginConfig) -> str:
    return _PROPERTIES_TEMPLATE.format(
        pack_id=plugin.pack_id,
        display_name=plugin.pack_display_name,
        vendor=plugin.pack_vendor,
        minor_version=plugin.pack_minor_version,
        major_version=plugin.pack_major_version,
        revision=plugin.pack_revision,
        product_id=plugin.product_id,
    )


def _preview_dirs(nksf_dirs: list[Path]) -> list[Path]:
    """Return the .previews subdirectory for each nksf_dir that has one."""
    return [d / ".previews" for d in nksf_dirs if (d / ".previews").is_dir()]


def _find_preview(preview_dirs: list[Path], category: str, preset_name: str) -> Path | None:
    for preview_dir in preview_dirs:
        # 1. Pre-organized by category with adg naming
        p = preview_dir / category / f"{preset_name}.adg.ogg"
        if p.exists():
            return p
        # 2. Flat NKS layout
        p = preview_dir / f"{preset_name}.nksf.ogg"
        if p.exists():
            return p
    return None


def _find_nksf_files(nksf_dirs: list[Path]) -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for d in nksf_dirs:
        for p in sorted(d.rglob("*.nksf")):
            if p.name not in seen:
                seen.add(p.name)
                files.append(p)
    return files


def assemble(
    plugin: PluginConfig,
    output_dir: Path,
    include_params: bool = True,
    dry_run: bool = False,
) -> Path:
    """Build the pack directory under output_dir/<PackDisplayName>.

    Returns the path to the created pack directory.
    """
    pack_root = output_dir / plugin.pack_display_name
    folder_info = pack_root / "Ableton Folder Info"
    previews_root = folder_info / "Previews"

    nksf_files = _find_nksf_files(plugin.nksf_dirs)
    if not nksf_files:
        raise FileNotFoundError(f"No .nksf files found in {plugin.nksf_dirs}")

    previews = _preview_dirs(plugin.nksf_dirs)

    results: list[tuple[str, str, bytes, Path | None]] = []  # (category, name, adg_bytes, preview_src)

    for nksf_path in nksf_files:
        preset = nksf.parse(nksf_path)
        params = nksf.extract_params(preset.nica)
        category = nksf.get_category(preset.nisi, plugin.category_map)
        preset_name = nksf_path.stem

        adg_bytes = adg.build(preset.pchk, params, plugin, include_params)

        preview_src = _find_preview(previews, category, preset_name)

        results.append((category, preset_name, adg_bytes, preview_src))

    if dry_run:
        for category, name, adg_bytes, preview_src in results:
            dest = pack_root / category / f"{name}.adg"
            preview_note = f" + preview" if preview_src else ""
            print(f"  {dest.relative_to(output_dir)}{preview_note}")
        print(f"\n{len(results)} presets — dry run, nothing written")
        return pack_root

    pack_root.mkdir(parents=True, exist_ok=True)
    folder_info.mkdir(exist_ok=True)
    (folder_info / "Properties.cfg").write_text(_properties_cfg(plugin), encoding="utf-8")

    for category, preset_name, adg_bytes, preview_src in results:
        preset_dir = pack_root / category
        preset_dir.mkdir(exist_ok=True)
        (preset_dir / f"{preset_name}.adg").write_bytes(adg_bytes)

        if preview_src:
            preview_dest_dir = previews_root / category
            preview_dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(preview_src, preview_dest_dir / f"{preset_name}.adg.ogg")

    return pack_root
