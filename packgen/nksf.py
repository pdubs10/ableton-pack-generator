"""NKSF preset parser.

NKSF files are RIFF containers (form type 'NIKS') with four chunks:
  NISI — preset metadata (4-byte version header + MessagePack)
  NICA — NKS parameter mapping (4-byte version header + MessagePack)
  PLID — plugin identifier (4-byte version header + MessagePack)
  PCHK — raw plugin state (4-byte version header + binary)
"""

from dataclasses import dataclass
from pathlib import Path
import struct

import msgpack


@dataclass
class NksfPreset:
    nisi: dict
    nica: dict
    plid: dict
    pchk: bytes
    path: Path


def parse(path: Path) -> NksfPreset:
    data = path.read_bytes()

    if data[:4] != b"RIFF":
        raise ValueError(f"Not a RIFF file: {path}")
    form_type = data[8:12]
    if form_type != b"NIKS":
        raise ValueError(f"Expected NIKS form type, got {form_type!r}: {path}")

    chunks: dict[bytes, bytes] = {}
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_data = data[offset + 8 : offset + 8 + chunk_size]
        chunks[chunk_id] = chunk_data
        offset += 8 + chunk_size
        if chunk_size % 2 != 0:
            offset += 1

    for required in (b"NISI", b"NICA", b"PLID", b"PCHK"):
        if required not in chunks:
            raise ValueError(f"Missing chunk {required!r} in {path}")

    def decode(chunk: bytes) -> dict:
        return msgpack.unpackb(chunk[4:], raw=False)  # skip 4-byte version header

    return NksfPreset(
        nisi=decode(chunks[b"NISI"]),
        nica=decode(chunks[b"NICA"]),
        plid=decode(chunks[b"PLID"]),
        pchk=chunks[b"PCHK"][4:],  # skip version header; remainder is raw plugin state
        path=path,
    )


def extract_params(nica: dict) -> list[dict]:
    """Extract up to 128 parameters from NICA ni8 pages.

    Returns list of dicts with 'visual_index' (position in the NICA grid,
    page*8+slot) and 'id' (plugin parameter ID). Unassigned slots (no 'id'
    key) are skipped. id=0 is a valid parameter ID.
    """
    params: list[dict] = []
    for page_index, page in enumerate(nica.get("ni8", [])):
        for slot_index, param in enumerate(page):
            if "id" not in param:
                continue
            params.append(
                {
                    "visual_index": page_index * 8 + slot_index,
                    "id": param["id"],
                }
            )
            if len(params) >= 128:
                return params
    return params


def get_category(nisi: dict, category_map: dict) -> str:
    """Return the preset category derived from NISI types[0][0].

    types is a list-of-lists; the second level is a sub-type qualifier, not
    a separate category. category_map handles plugin-specific name divergences.
    """
    types = nisi.get("types", [])
    raw = types[0][0] if types and types[0] else "Misc"
    return category_map.get(raw, raw)
