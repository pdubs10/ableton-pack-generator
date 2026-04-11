"""VST3 and NKS library scanner.

Scans NKS library roots for directories containing .nksf files, samples one
preset per library to extract plugin identity metadata (PLID + NISI), then
resolves each library to an installed VST3 plugin.

Match strategies (tried in order):
  1. VST3 UID from PLID — direct lookup, zero ambiguity
  2. pluginName / pluginVendor from PLID — NI and similar vendors embed these
  3. NISI vendor + normalized directory name — vendor-confirmed name match
  4. Normalized directory name only — fallback substring match

VST3 class IDs are read from the bundle's moduleinfo.json (VST3 SDK ≥ 3.7).
moduleinfo.json often contains trailing commas (technically invalid JSON, but
common in JUCE-based plugins). The parser strips them before decoding.
"""

import json
import re
from pathlib import Path

import click
import msgpack

# ------------------------------------------------------------------
# VST3 scanning
# ------------------------------------------------------------------

_VST3_SEARCH_PATHS = [
    Path("/Library/Audio/Plug-Ins/VST3"),
    Path.home() / "Library/Audio/Plug-Ins/VST3",
]


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or } to tolerate common JSON quirks."""
    return re.sub(r",\s*([\]\}])", r"\1", text)


def _cid_to_uuid(cid: str) -> str:
    """Convert a 32-char hex CID string to standard UUID format."""
    h = cid.replace("-", "").lower()
    if len(h) != 32:
        raise ValueError(f"Expected 32 hex chars, got {len(h)}: {cid!r}")
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _read_moduleinfo(bundle: Path) -> dict | None:
    """Parse Contents/Resources/moduleinfo.json from a VST3 bundle.

    Returns None if the file is absent or unparseable.
    """
    p = bundle / "Contents" / "Resources" / "moduleinfo.json"
    if not p.exists():
        return None
    try:
        return json.loads(_strip_trailing_commas(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _extract_instrument_class(moduleinfo: dict) -> dict | None:
    """Return the first Audio Module Class entry that is an instrument, or None."""
    for cls in moduleinfo.get("Classes", []):
        if cls.get("Category") != "Audio Module Class":
            continue
        sub = [s.lower() for s in cls.get("Sub Categories", [])]
        if not any(s in sub for s in ("instrument", "synth")):
            continue
        cid = cls.get("CID", "")
        if len(cid.replace("-", "")) != 32:
            continue
        return cls
    return None


def scan_vst3(search_paths: list[Path]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Return discovered instrument plugin dicts and unresolvable bundle warnings.

    Returns (plugins, warnings) where each plugin has: name, vendor,
    vst3_class_id, bundle_path; and each warning is (bundle_name, reason).
    """
    plugins = []
    warnings = []
    for base in search_paths:
        if not base.is_dir():
            continue
        for bundle in sorted(base.glob("*.vst3")):
            info = _read_moduleinfo(bundle)
            if info is None:
                warnings.append((bundle.name, "no moduleinfo.json — class ID not resolvable"))
                continue
            cls = _extract_instrument_class(info)
            if cls is None:
                continue
            try:
                class_id = _cid_to_uuid(cls["CID"])
            except ValueError:
                warnings.append((bundle.name, f"malformed CID: {cls.get('CID')!r}"))
                continue
            plugins.append(
                {
                    "name": cls.get("Name") or info.get("Name") or bundle.stem,
                    "vendor": cls.get("Vendor") or info.get("Factory Info", {}).get("Vendor", ""),
                    "vst3_class_id": class_id,
                    "bundle_path": bundle,
                }
            )
    return plugins, warnings


def _index_vst3(plugins: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Build lookup indexes from a list of VST3 plugin dicts.

    Returns:
      by_uid  — normalized 32-char hex class ID → plugin
      by_name — normalized plugin name → list of plugins (multiple vendors
                may share a normalized name)
    """
    by_uid: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for plugin in plugins:
        uid_key = plugin["vst3_class_id"].replace("-", "").lower()
        by_uid[uid_key] = plugin
        name_key = _normalize(plugin["name"])
        by_name.setdefault(name_key, []).append(plugin)
    return by_uid, by_name


# ------------------------------------------------------------------
# NKS library scanning
# ------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lowercase, strip common NKS suffixes, keep only alphanumerics."""
    s = s.lower()
    for word in ("factory library", "factory", "library", "presets", "preset", "pack", "content"):
        s = s.replace(word, "")
    return re.sub(r"[^a-z0-9]", "", s)


# Directory names that organise content within a library but are not
# themselves the library root (e.g. Pigments/Presets/Bass → root is Presets).
_STRUCTURAL_DIR_NAMES = frozenset({
    "presets", "preset", "patches", "patch", "banks", "bank",
    "content", "factory content", "user content",
    "sounds", "samples", "instruments", "data", "files", "nks presets",
})


def _read_nksf_meta(path: Path) -> dict | None:
    """Read PLID and NISI vendor from a single .nksf file.

    Returns {"plid": ..., "nisi_vendor": ...} or None if unparseable.
    """
    import struct
    try:
        data = path.read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"NIKS":
            return None
        chunks: dict[bytes, bytes] = {}
        offset = 12
        while offset + 8 <= len(data):
            chunk_id = data[offset:offset + 4]
            chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
            chunks[chunk_id] = data[offset + 8:offset + 8 + chunk_size]
            offset += 8 + chunk_size + (chunk_size % 2)
        if b"PLID" not in chunks:
            return None
        plid = msgpack.unpackb(chunks[b"PLID"][4:], raw=False)
        nisi_vendor = ""
        if b"NISI" in chunks:
            nisi = msgpack.unpackb(chunks[b"NISI"][4:], raw=False)
            nisi_vendor = nisi.get("vendor", "")
        return {"plid": plid, "nisi_vendor": nisi_vendor}
    except Exception:
        return None


def _find_library_root(leaf: Path, root: Path) -> Path:
    """Walk up from a leaf directory to the library root.

    Lifts through structural directories (Presets, Patches, etc.) in both
    directions: if the current directory is itself structural, or if its parent
    is structural, keep walking up. Stops when neither applies or when the
    path would go above root.

    Examples (root = /Users/Shared):
      Massive X Factory Library/Presets/Bass → Massive X Factory Library
      Massive X Factory Library/Presets      → Massive X Factory Library
      Drive Library/Presets                  → Drive Library

    Examples (root = /Library/Application Support):
      u-he/Diva/NKS/Diva                    → u-he/Diva/NKS/Diva  (NKS not structural)
      u-he/Repro-1/NKS/Repro-1              → u-he/Repro-1/NKS/Repro-1
    """
    current = leaf
    while current != root:
        parent = current.parent
        if parent == root:
            return current  # direct child of root, cannot lift further
        if (current.name.lower() in _STRUCTURAL_DIR_NAMES
                or parent.name.lower() in _STRUCTURAL_DIR_NAMES):
            current = parent  # current or parent is a structural container, go up
        else:
            return current
    return current


def scan_nks_libraries(roots: list[Path]) -> list[dict]:
    """Scan roots for NKS library directories at any depth.

    Walks the entire subtree of each root looking for directories that directly
    contain .nksf files (leaf dirs). Each leaf is lifted to its library root by
    walking up through structural parent directories (Presets, Patches, etc.).
    Multiple category dirs within one library collapse to a single entry.
    Separate libraries for the same VST3 remain distinct.

    Returns a list of dicts with:
      path        — library root directory
      plid        — PLID msgpack dict, or {} if unreadable
      nisi_vendor — vendor string from NISI, or ""
    """
    library_meta: dict[Path, dict] = {}  # lib_root → metadata (first seen wins)

    for root in roots:
        if not root.is_dir():
            continue
        for nksf_path in sorted(root.rglob("*.nksf")):
            leaf = nksf_path.parent
            lib_root = _find_library_root(leaf, root)
            if lib_root not in library_meta:
                library_meta[lib_root] = _read_nksf_meta(nksf_path) or {}

    return [
        {
            "path": lib_root,
            "plid": meta.get("plid", {}),
            "nisi_vendor": meta.get("nisi_vendor", ""),
        }
        for lib_root, meta in sorted(library_meta.items())
    ]


# ------------------------------------------------------------------
# Matching
# ------------------------------------------------------------------

def _plid_vst3_uid(plid: dict) -> str | None:
    """Extract a normalized 32-char hex VST3 UID from a PLID dict, or None."""
    for key in ("VST3.uid", "VST3UID", "vst3Uid", "vst3_uid"):
        val = plid.get(key)
        if isinstance(val, str):
            h = val.replace("-", "").lower()
            if len(h) == 32:
                return h
    return None


def _plid_key(plid: dict) -> str:
    """Stable string key for a PLID dict, used to cache resolved plugins."""
    uid = _plid_vst3_uid(plid)
    if uid:
        return f"vst3:{uid}"
    magic = plid.get("VST.magic")
    if magic is not None:
        return f"vst2:{magic}"
    return ""


def match_library_to_vst3(
    lib: dict,
    vst3_by_uid: dict[str, dict],
    vst3_by_name: dict[str, list[dict]],
) -> tuple[dict | None, str]:
    """Attempt to match a NKS library to an installed VST3 plugin.

    Returns (plugin, match_method) on success, (None, reason) on failure.
    """
    # Strategy 1: direct VST3 UID from PLID
    uid = _plid_vst3_uid(lib["plid"])
    if uid:
        plugin = vst3_by_uid.get(uid)
        if plugin:
            return plugin, "VST3 UID (PLID)"
        return None, f"PLID contains VST3 UID {uid[:8]}… but no matching installed plugin"

    # Strategy 2: pluginName / pluginVendor from PLID
    plugin_name_from_plid = lib["plid"].get("pluginName", "")
    if plugin_name_from_plid:
        name_norm = _normalize(plugin_name_from_plid)
        plid_candidates = vst3_by_name.get(name_norm, [])
        if len(plid_candidates) == 1:
            return plid_candidates[0], "pluginName (PLID)"
        if len(plid_candidates) > 1:
            vendor_norm = _normalize(lib["plid"].get("pluginVendor", ""))
            vendor_matched = [p for p in plid_candidates if _normalize(p["vendor"]) == vendor_norm]
            if len(vendor_matched) == 1:
                return vendor_matched[0], "pluginName + pluginVendor (PLID)"

    # Strategy 3: normalize directory name, match against VST3 plugin names
    dir_norm = _normalize(lib["path"].name)
    nisi_vendor_norm = _normalize(lib["nisi_vendor"])

    candidates: list[tuple[dict, str]] = []
    for name_key, plugins in vst3_by_name.items():
        if not name_key:
            continue
        if name_key not in dir_norm and dir_norm not in name_key:
            continue
        for plugin in plugins:
            vendor_norm = _normalize(plugin["vendor"])
            if nisi_vendor_norm and vendor_norm and (
                vendor_norm in nisi_vendor_norm or nisi_vendor_norm in vendor_norm
            ):
                candidates.append((plugin, "name + vendor (NISI)"))
            else:
                candidates.append((plugin, "name (directory)"))

    # Prefer vendor-confirmed matches
    vendor_confirmed = [c for c in candidates if "vendor" in c[1]]
    ranked = vendor_confirmed or candidates

    if len(ranked) == 1:
        return ranked[0]
    if len(ranked) > 1:
        names = ", ".join(c[0]["name"] for c in ranked)
        return None, f"ambiguous — multiple VST3 matches: {names}"

    return None, "no matching VST3 found"


# ------------------------------------------------------------------
# TOML generation
# ------------------------------------------------------------------

def _format_nksf_dir(paths: list[Path]) -> str:
    if len(paths) == 1:
        return f'nksf_dir = "{paths[0]}"'
    lines = ["nksf_dir = ["]
    for p in paths:
        lines.append(f'    "{p}",')
    lines.append("]")
    return "\n".join(lines)


def render_toml(plugin: dict, nksf_dirs: list[Path], pack_dir: Path) -> str:
    name = plugin["name"]
    vendor = plugin["vendor"]
    class_id = plugin["vst3_class_id"]
    pack_id = f"{vendor.lower().replace(' ', '-')}/{name.lower().replace(' ', '-')}"

    if class_id:
        class_id_line = f'vst3_class_id = "{class_id}"'
    else:
        class_id_line = 'vst3_class_id = ""  # TODO: fill in — see README for how to find this'

    return f"""\
[plugin]
name = "{name}"
vendor = "{vendor}"
{class_id_line}
mpe_enabled = 2          # 1 = MPE enabled, 2 = MPE disabled — verify against plugin docs
controller_state = "empty"  # set to "same" for some u-he plugins (e.g. Diva)

[pack]
id = "{pack_id}"
display_name = "{name}"
vendor = "{vendor}"

[input]
{_format_nksf_dir(nksf_dirs)}

[output]
pack_dir = "{pack_dir}"

[category_map]
"Piano / Keys" = "Piano & Keys"
"Piano/Keys" = "Piano & Keys"
"""


# ------------------------------------------------------------------
# CLI command
# ------------------------------------------------------------------

@click.command("scan")
@click.option(
    "--vst3-path",
    multiple=True,
    type=click.Path(path_type=Path),
    help="VST3 directory to scan. May be repeated. "
         "Defaults to /Library/Audio/Plug-Ins/VST3 and ~/Library/Audio/Plug-Ins/VST3.",
)
@click.option(
    "--nks-root",
    multiple=True,
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Root directory to search for NKS preset libraries. May be repeated.",
)
@click.option(
    "--output-dir", "-o",
    default=".",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory to write generated .toml files.",
)
@click.option(
    "--pack-dir",
    default="~/Music/Ableton/Custom Packs",
    show_default=True,
    help="Value to write for output.pack_dir in generated configs.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite existing .toml files.",
)
def scan_cmd(
    vst3_path: tuple[Path, ...],
    nks_root: tuple[Path, ...],
    output_dir: Path,
    pack_dir: str,
    overwrite: bool,
):
    """Scan NKS preset libraries and match each to an installed VST3 plugin.

    For each NKS library directory found under --nks-root, one preset is sampled
    to extract plugin identity metadata (PLID VST3 UID, NISI vendor). That
    metadata drives VST3 resolution before falling back to directory-name
    matching. A plugin.toml is written for each successfully resolved pair.
    """
    vst3_paths = list(vst3_path) if vst3_path else _VST3_SEARCH_PATHS
    pack_dir_path = Path(pack_dir).expanduser()

    click.echo("Scanning VST3 plugins...")
    plugins, vst3_warnings = scan_vst3(vst3_paths)
    click.echo(f"  Found {len(plugins)} instrument plugin(s) with readable class IDs")
    for bundle_name, reason in vst3_warnings:
        click.echo(f"  warning: {bundle_name} — {reason}", err=True)

    vst3_by_uid, vst3_by_name = _index_vst3(plugins)

    click.echo("Scanning NKS libraries...")
    libraries = scan_nks_libraries(list(nks_root))
    click.echo(f"  Found {len(libraries)} NKS library director(ies)")
    for lib in libraries:
        click.echo(f"    {lib['path']}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Group libraries by their resolved VST3 plugin so multi-dir inputs are
    # consolidated into a single TOML (same plugin, multiple library roots).
    resolved: dict[str, tuple[dict, list[Path]]] = {}  # vst3_class_id → (plugin, [dirs])
    # Unresolved: grouped by (name, vendor) so expansion packs collapse to one stub TOML.
    stubs: dict[tuple[str, str], list[Path]] = {}  # (name, vendor) → [dirs]

    # Pass 1: match each library against installed VST3s, building a PLID cache
    # so that expansion packs with unrelated names can be resolved in pass 2.
    match_results: list[tuple[dict, dict | None, str]] = []
    plid_cache: dict[str, dict] = {}  # plid_key → resolved plugin
    for lib in libraries:
        plugin, method = match_library_to_vst3(lib, vst3_by_uid, vst3_by_name)
        pk = _plid_key(lib["plid"])
        if plugin is not None and pk:
            plid_cache[pk] = plugin
        match_results.append((lib, plugin, method))

    # Pass 2: fill in unresolved libraries whose PLID was resolved via a sibling.
    for lib, plugin, method in match_results:
        if plugin is None:
            pk = _plid_key(lib["plid"])
            if pk and pk in plid_cache:
                plugin = plid_cache[pk]
                method = "PLID (sibling library)"
        if plugin is None:
            # Build a stub key from the best available name/vendor metadata.
            stub_name = lib["plid"].get("pluginName") or lib["path"].name
            stub_vendor = lib["plid"].get("pluginVendor") or lib["nisi_vendor"]
            stubs.setdefault((stub_name, stub_vendor), []).append(lib["path"])
            continue
        key = plugin["vst3_class_id"]
        if key not in resolved:
            resolved[key] = (plugin, [])
        resolved[key][1].append(lib["path"])
        click.echo(f"  matched {lib['path'].name} → {plugin['name']}  [{method}]")

    written = []
    for plugin, nksf_dirs in resolved.values():
        slug = re.sub(r"[^a-z0-9_-]", "-", plugin["name"].lower()).strip("-")
        out_path = output_dir / f"{slug}.toml"

        if out_path.exists() and not overwrite:
            click.echo(f"  skip (exists) {out_path.name}")
            continue

        missing_previews = [d for d in nksf_dirs if not (d / ".previews").is_dir()]
        toml_text = render_toml(plugin, nksf_dirs, pack_dir_path)
        out_path.write_text(toml_text, encoding="utf-8")
        written.append(out_path)
        preview_note = f" — no .previews in: {', '.join(d.name for d in missing_previews)}" if missing_previews else ""
        click.echo(f"  wrote {out_path.name}  ({len(nksf_dirs)} NKS dir(s)){preview_note}")

    stub_written = []
    if stubs:
        click.echo("\nNKS libraries with no matching installed VST3 — writing stub configs:")
        for (stub_name, stub_vendor), nksf_dirs in stubs.items():
            stub_plugin = {"name": stub_name, "vendor": stub_vendor, "vst3_class_id": ""}
            slug = re.sub(r"[^a-z0-9_-]", "-", stub_name.lower()).strip("-")
            out_path = output_dir / f"{slug}.toml"

            if out_path.exists() and not overwrite:
                click.echo(f"  skip (exists) {out_path.name}")
                continue

            toml_text = render_toml(stub_plugin, nksf_dirs, pack_dir_path)
            out_path.write_text(toml_text, encoding="utf-8")
            stub_written.append(out_path)
            lines = [
                f"  wrote {out_path.name} (vst3_class_id is blank — fill in before running generate)",
                f"    Plugin : {stub_name}" + (f" ({stub_vendor})" if stub_vendor else ""),
                f"    NKS dirs ({len(nksf_dirs)}):",
            ]
            for d in nksf_dirs:
                lines.append(f"      {d}")
            click.echo("\n".join(lines), err=True)

    click.echo(f"\n{len(written)} config(s) written, {len(stub_written)} stub(s) written to {output_dir}")
