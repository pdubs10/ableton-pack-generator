"""ADG preset builder.

Renders the instrument_rack.adg.j2 Jinja2 template with per-preset data and
gzip-compresses the result. The template is loaded once at import time.
"""

import gzip
from pathlib import Path

import jinja2

from .config import PluginConfig

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,          # XML-safe escaping for vendor/plugin name strings
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)

_template = _env.get_template("instrument_rack.adg.j2")


def build(
    pchk: bytes,
    params: list[dict],
    plugin: PluginConfig,
    include_params: bool = True,
) -> bytes:
    """Return gzip-compressed ADG XML bytes for a single preset.

    Args:
        pchk: Raw plugin state bytes (PCHK chunk with version header already stripped).
        params: List of {'visual_index': int, 'id': int} dicts from NICA.
        plugin: Loaded plugin configuration.
        include_params: When False, emits <ParameterSettings /> instead of the full list.
    """
    buffer_lines = [
        pchk[i : i + 40].hex().upper() for i in range(0, len(pchk), 40)
    ]

    xml = _template.render(
        plugin=plugin,
        uid_fields=plugin.uid_fields,
        params=params if include_params else [],
        include_params=include_params,
        buffer_lines=buffer_lines,
    )

    return gzip.compress(xml.encode("utf-8"))
