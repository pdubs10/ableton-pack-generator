# packgen

Generates Ableton Live 12 Custom Packs from VST3 plugin NKSF presets. Each NKSF file becomes an `.adg` Instrument Rack containing the plugin state, VST3 identity, and (optionally) NKS parameter mappings.

## Requirements

- Python 3.11+
- NKS preset files (`.nksf`) for the target plugin
- The plugin's VST3 class ID UUID (see [Finding the VST3 class ID](#finding-the-vst3-class-id))
- This has only been tested on macOS

## Installation

```bash
pipx install .
```

## Quick start

1. Create a `plugin.toml` for your plugin (see [Configuration](#configuration)).
2. Run:

```bash
packgen generate --config plugin.toml
```

The pack is written to `pack_dir/<PackDisplayName>/`. Install it by placing that directory in `~/Music/Ableton/Custom Packs/` and rescanning in Live's browser.

## Commands

### `packgen generate`

Processes all `.nksf` files found recursively under `nksf_dir` and assembles a full Custom Pack.

```
packgen generate [OPTIONS]

  -c, --config PATH   Path to plugin.toml  [required]
  --no-params         Emit empty <ParameterSettings /> instead of NKS mappings
  --dry-run           Print what would be generated without writing anything
```

### `packgen scan`

Scans NKS preset library directories and matches each to an installed VST3 plugin, generating a ready-to-use `plugin.toml` for each match. Libraries that can't be resolved get a stub config with a blank `vst3_class_id` to fill in manually.

```
packgen scan [OPTIONS]

  --nks-root PATH       Root directory to search for NKS libraries. May be repeated.  [required]
  --vst3-path PATH      VST3 directory to scan. May be repeated.
                        Defaults to /Library/Audio/Plug-Ins/VST3 and ~/Library/Audio/Plug-Ins/VST3.
  -o, --output-dir DIR  Directory to write generated .toml files.  [default: .]
  --pack-dir PATH       Value to write for output.pack_dir in generated configs.
                        [default: ~/Music/Ableton/Custom Packs]
  --overwrite           Overwrite existing .toml files.
```

**Example:**

```bash
packgen scan \
  --nks-root "/Users/Shared/NI" \
  --nks-root "/Library/Application Support/Native Instruments" \
  --output-dir ~/configs
```

Match strategies (tried in order):

1. VST3 UID from PLID — direct lookup, zero ambiguity
2. `pluginName` / `pluginVendor` from PLID — works for NI and similar vendors
3. NISI vendor + normalized directory name — vendor-confirmed name match
4. Normalized directory name only — fallback substring match

Libraries with the same PLID (e.g. expansion packs for the same instrument) are consolidated into a single config with multiple `nksf_dir` entries.

### `packgen adg`

Generates a single `.adg` file from one `.nksf` file. Useful for testing a config before processing an entire library.

```
packgen adg [OPTIONS]

  -c, --config PATH   Path to plugin.toml  [required]
  --nksf PATH         Path to a single .nksf file  [required]
  -o, --out PATH      Output .adg path  [required]
  --no-params         Emit empty <ParameterSettings />
```

## Configuration

Each plugin requires a `plugin.toml`. Only `[plugin]`, `[input]`, and `[output]` sections are required.

```toml
[plugin]
name = "Diva"
vendor = "u-he"
vst3_class_id = "d39d5b69-d6af-42fa-1234-567844695661"
mpe_enabled = 1          # 1 = MPE enabled, 2 = MPE disabled (default: 2)
controller_state = "empty"  # "empty" or "same" — see below (default: "empty")

[pack]
id = "u-he.com/diva"
display_name = "Diva"
vendor = "u-he"
major_version = 1
minor_version = 1
revision = 3425
product_id = 0

[input]
# Single path or array of paths — all are scanned recursively for .nksf files
nksf_dir = "/Users/Shared/Diva Factory Library/Presets"

[output]
pack_dir = "~/Music/Ableton/Custom Packs"

# Optional: remap NISI category names to Ableton browser names
[category_map]
"Piano / Keys" = "Piano & Keys"
```

### Multiple input directories

`nksf_dir` accepts a TOML array when a plugin's presets span multiple library locations:

```toml
[input]
nksf_dir = [
    "/Users/Shared/Diva Factory Library/Presets",
    "/Users/Shared/Diva Expansion Pack/Presets",
]
```

When the same filename appears in more than one directory, the first occurrence wins.

### `controller_state`

Controls the content of the ADG's `<ControllerState>` element:

- `"empty"` *(default)* — emits `<ControllerState />`. Works for most plugins.
- `"same"` — duplicates `<ProcessorState>` into `<ControllerState>`. Potentially required by some u-he plugins (e.g. Diva) to preserve preset state correctly.

### `[category_map]`

Preset categories are derived from the `types[0][0]` field in each NKSF's NISI metadata. If your plugin's NKS category names differ from how you want them named in the Ableton browser, add mappings here. Any category not listed passes through unchanged.

### `[pack]` defaults

All `[pack]` fields are optional and default to:

| Field | Default |
|---|---|
| `id` | `"<vendor>/<name>"` (lowercased) |
| `display_name` | `plugin.name` |
| `vendor` | `plugin.vendor` |
| `major_version` | `1` |
| `minor_version` | `0` |
| `revision` | `1` |
| `product_id` | `0` |

## Output structure

```
<PackDisplayName>/
├── Ableton Folder Info/
│   ├── Properties.cfg
│   └── Previews/
│       └── <Category>/
│           └── <PresetName>.adg.ogg
└── <Category>/
    └── <PresetName>.adg
```

### Preview files

packgen automatically looks for a `.previews` subdirectory alongside each `nksf_dir`. For each preset it checks in order:

1. `<nksf_dir>/.previews/<Category>/<PresetName>.adg.ogg` — pre-organized layout
2. `<nksf_dir>/.previews/<PresetName>.nksf.ogg` — flat NKS layout (renamed on copy)

## Finding the VST3 class ID

The `vst3_class_id` is a UUID identifying the VST3 instrument component. It is **not** stored in the NKSF files — you need to find it from another source.

**Option 1 — from an existing ADG saved by Ableton**

Load the plugin in Live, save an Instrument Rack preset, then inspect the `.adg`:

```bash
python3 - <<'EOF'
import gzip, re, sys
xml = gzip.decompress(open(sys.argv[1], 'rb').read()).decode()
m = re.search(r'BranchDeviceId Value="device:vst3:instr:([^"]+)"', xml)
print(m.group(1) if m else "not found")
EOF
~/path/to/preset.adg
```

**Option 2 — from the VST3 bundle**

On macOS, VST3 plugins include a `moduleinfo.json` listing all components and their class IDs:

```bash
cat "/Library/Audio/Plug-Ins/VST3/MyPlugin.vst3/Contents/Resources/moduleinfo.json" \
  | python3 -c "
import json, sys
info = json.load(sys.stdin)
for cls in info.get('Classes', []):
    print(cls.get('UID'), cls.get('Name'), cls.get('Category'))
"
```

Look for the entry with `Category` = `"Audio Module Class"` (the instrument processor).

## ADG compatibility

Generated `.adg` files target Ableton Live 12.3 (`MajorVersion="5" MinorVersion="12.0_12300"`). The version header has no functional effect on loading — presets load correctly in any Live 12 release.

The template (`templates/instrument_rack.adg.j2`) is a single Jinja2 template used for all VST3 plugins. Plugin-specific values (UID fields, MPE setting, browser path) come entirely from `plugin.toml`.

## Adding packs to Ableton
After creating the pack, add it to your Ableton User Library. I generally set up a `~/Music/Ableton/Custom Packs` for this type of pack. It's a bit inconsistent between versions, but the pack may not be recognized initially as a "Pack". If the pack is visible in the "Custom Packs" section of the Ableton browser, drag it to the "Packs" section. This seems to be necessary for preset previews to work.

## Known Issues
- Preset scanning is a a bit iffy. It's probably a good starting point, but the information in the NKSF files and VST3 bundles are wildly inconsistent. Name matching and inconsistent directory structures are also unreliable. This could be improved, but I decided it wasn't worth the effort for me.
- Switching between Diva presets in Ableton, if the presets contain parameter mappings, does not work correctly. The preset will correctly load initially, but going directly to another one results in some strange parameters being set. If you remove the instrument rack first or switch from a different plugin's preset everything works as expected. This is due to a confirmed bug in Ableton.
- I have no idea if this works on Windows. Probably not. I don't plan on adding support myself, but welcome contributions.
- Plugins are a bit all over the place with their file structures and the way they load preset data. There are likely edge cases I haven't covered.

## Acknowledgements
This builds heavily on previous work done in [jhorology/nks-presets-collection](https://github.com/jhorology/nks-presets-collection). I had previously gotten that project limping along to generate .adg files and separately scripted pack creation based on information in [this Reddit post](https://www.reddit.com/r/abletonlive/comments/ilo2ru/figured_out_how_to_make_previews_that_work_in_the/). I used Claude Code to rebuild those tools using modern Python and with integrated Pack generation.