"""
OpenMDict shared build utilities.

Standardized MDX packing, preview generation, MDict source writing,
and catalog metadata sidecar generation.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import struct
import subprocess
import zlib
from collections.abc import Iterable
from pathlib import Path

# preset-dicts/ (this file lives in preset-dicts/scripts/shared/)
PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PIPELINE_ROOT / "data"
# CreationDate per MDX file name, recorded by the first build (see
# resolve_creation_date). Checked in, so a rebuild anywhere reproduces it.
PINS_PATH = PIPELINE_ROOT / "build-pins.json"


# ── Default flag lookup (matches iOS flagEmoji logic) ───────────
FLAGS: dict[str, str] = {
    "EN": "🇬🇧🇺🇸", "ZH": "🇨🇳🇹🇼", "JA": "🇯🇵", "KO": "🇰🇷",
    "ES": "🇪🇸", "FR": "🇫🇷", "DE": "🇩🇪", "IT": "🇮🇹",
    "PT": "🇵🇹🇧🇷", "RU": "🇷🇺", "AR": "🇸🇦", "TR": "🇹🇷",
    "EL": "🇬🇷", "LA": "🏛️",
}


def pack_mdx(entries_txt: Path, title: str, description: str,
             output_path: Path, *,
             creation_date: "str | datetime.date | None" = None,
             inputs: "Iterable[Path] | None" = None) -> tuple[bool, float, str]:
    """Pack an MDict (.mdx) dictionary.

    Writes title/description HTML files alongside the MDX, then
    invokes the ``mdict`` CLI tool and finally pins the header's
    ``CreationDate`` so that rebuilding unchanged inputs yields a
    byte-identical file (see :func:`resolve_creation_date`).

    Args:
        entries_txt: Path to the MDict source text file.
        title: Plain text title (written to title HTML file).
        description: HTML description string (written to desc HTML file).
        output_path: Where to write the .mdx file.
        creation_date: Explicit ``CreationDate`` (``YYYY-MM-DD`` or a
            ``datetime.date``). Overrides every other source.
        inputs: The source files this dictionary was built from. When
            given (and no explicit date / ``SOURCE_DATE_EPOCH``), the
            newest mtime among them becomes the ``CreationDate``.

    Returns:
        (success, size_mb, error_msg)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Derive auxiliary file names from the entries file stem
    stem = entries_txt.stem.replace('_entries', '')
    title_path = output_path.parent / f"{stem}_title.html"
    desc_path = output_path.parent / f"{stem}_desc.html"

    title_path.write_text(title, encoding='utf-8')
    desc_path.write_text(description, encoding='utf-8')

    print(f"  Packing -> {output_path}...")
    result = subprocess.run(
        ["mdict", "--title", str(title_path), "--description", str(desc_path),
         "-a", str(entries_txt), str(output_path)],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        return False, 0.0, result.stderr

    if result.stdout.strip():
        print(result.stdout.strip())

    # mdict-utils stamps CreationDate="<today>" into the header, which
    # makes every rebuild a different file. Pin it (D12 determinism).
    date_str = resolve_creation_date(creation_date, inputs, name=output_path.name)
    set_mdx_creation_date(output_path, date_str)
    print(f"  CreationDate pinned to {date_str}")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    return True, size_mb, ''


# ── Reproducible MDX headers ────────────────────────────────────
#
# mdict-utils writes ``CreationDate="{year}-{month}-{day}"`` (today's
# date) into the MDX header. Everything else it emits is a pure
# function of the input text, so this one attribute is what makes
# two builds of the same input differ -- and what would force every
# published asset to be re-uploaded after every ``make``.
#
# The header layout (see docs/MDict_Format_Specification.md):
#
#   u32 big-endian   length N of the header text
#   N bytes          UTF-16LE XML, ``<Dictionary .../>\r\n`` + NUL
#   u32 little-endian adler32 of those N bytes
#   ...              key block info (no absolute offsets follow, so the
#                    header may change length)
#
# A preset's dictionary ID is the SHA-256 of the whole MDX file
# (PresetDictionaryService.importMDXFile -> PresetDictionaryService
# .fileSHA256 hashes the file in 1 MB chunks). CreationDate is part of
# those bytes, so a stable CreationDate keeps the ID stable across rebuilds
# of unchanged content. (The header-only hash is just the sidecar cache key.)

_CREATION_DATE_RE = re.compile(r'CreationDate="[^"]*"')


def _newest_mtime(paths: Iterable[Path]) -> float | None:
    newest: float | None = None
    for p in paths:
        try:
            if p.is_dir():
                for child in p.rglob("*"):
                    if child.is_file() and not child.name.startswith("."):
                        m = child.stat().st_mtime
                        newest = m if newest is None or m > newest else newest
            elif p.is_file():
                m = p.stat().st_mtime
                newest = m if newest is None or m > newest else newest
        except OSError:
            continue
    return newest


def _utc_date(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()


_PIN_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def load_creation_date_pins() -> dict[str, str]:
    """``build-pins.json``: ``{"<Name>.mdx": {"creationDate": "..."}}``."""
    if not PINS_PATH.exists():
        return {}
    data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    return {name: v["creationDate"] for name, v in data.items()
            if isinstance(v, dict) and v.get("creationDate")}


def record_creation_date_pin(name: str, date_str: str) -> None:
    data = {}
    if PINS_PATH.exists():
        data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    data.setdefault(name, {})["creationDate"] = date_str
    PINS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True,
                                    ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_creation_date(explicit: "str | datetime.date | None" = None,
                          inputs: "Iterable[Path] | None" = None, *,
                          name: "str | None" = None) -> str:
    """Pick the ``CreationDate`` to stamp into an MDX header.

    The date is part of the header bytes, so it is part of the file's
    SHA-256, which is the dictionary ID the app derives for a preset. A
    rebuild with unchanged content must therefore produce the same date,
    or every installed copy would be offered an "update" that changes
    nothing (and moves the user's state to a new ID).

    Resolution order (first hit wins):

    1. ``explicit`` -- a ``datetime.date`` or a ``YYYY-MM-DD`` string.
    2. ``SOURCE_DATE_EPOCH`` environment variable (reproducible-builds
       convention; UNIX seconds, interpreted in UTC).
    3. The pin for ``name`` in ``build-pins.json`` (kept verbatim, so an
       unpadded date written by mdict-utils reproduces the shipped
       header byte for byte).
    4. Newest mtime among ``inputs`` (files or directories).
    5. Newest mtime of any file under ``preset-dicts/data/``.
    6. Today (UTC) -- a warning is printed.

    When ``name`` has no pin, the date chosen by 4-6 is recorded as its
    pin, so later builds reproduce it. 1 and 2 never touch the pins.
    """
    if explicit is not None:
        if isinstance(explicit, datetime.date):
            return explicit.isoformat()
        text = str(explicit).strip()
        datetime.date.fromisoformat(text)  # validate
        return text

    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        return _utc_date(int(epoch))

    if name is not None:
        pinned = load_creation_date_pins().get(name)
        if pinned is not None:
            if not _PIN_DATE_RE.match(pinned):
                raise ValueError(f"{PINS_PATH.name}: bad creationDate {pinned!r} for {name}")
            return pinned

    date_str = None
    if inputs is not None:
        newest = _newest_mtime(inputs)
        if newest is not None:
            date_str = _utc_date(newest)

    if date_str is None:
        newest = _newest_mtime([DATA_DIR])
        if newest is not None:
            date_str = _utc_date(newest)

    if date_str is None:
        print("  WARNING: no SOURCE_DATE_EPOCH / pin / inputs / data/ found; "
              "CreationDate falls back to today")
        date_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    if name is not None:
        record_creation_date_pin(name, date_str)
        print(f"  CreationDate for {name} recorded in {PINS_PATH.name}")
    return date_str


def read_mdx_header(mdx_path: Path) -> tuple[str, bytes]:
    """Return ``(header_xml, remainder)`` of an MDX/MDD file.

    Verifies the header adler32. ``remainder`` is every byte after the
    header checksum (key block info onwards).
    """
    raw = mdx_path.read_bytes()
    if len(raw) < 8:
        raise ValueError(f"{mdx_path}: file too short to be an MDX")
    (header_len,) = struct.unpack(">I", raw[:4])
    header = raw[4:4 + header_len]
    if len(header) != header_len:
        raise ValueError(f"{mdx_path}: truncated header")
    (stored,) = struct.unpack("<I", raw[4 + header_len:8 + header_len])
    if stored != zlib.adler32(header) & 0xFFFFFFFF:
        raise ValueError(f"{mdx_path}: header adler32 mismatch")
    return header.decode("utf_16_le"), raw[8 + header_len:]


def set_mdx_creation_date(mdx_path: Path, date_str: str) -> None:
    """Rewrite ``CreationDate`` in a finished MDX/MDD and fix its adler32.

    The header may change length (mdict-utils writes unpadded dates,
    we write ISO ``YYYY-MM-DD``); nothing after the header stores an
    absolute file offset, so the rest of the file is copied verbatim.
    The file is replaced atomically.
    """
    header_xml, remainder = read_mdx_header(mdx_path)
    new_xml, n = _CREATION_DATE_RE.subn(f'CreationDate="{date_str}"',
                                        header_xml, count=1)
    if n == 0:
        raise ValueError(f"{mdx_path}: header has no CreationDate attribute")
    new_header = new_xml.encode("utf_16_le")

    tmp = mdx_path.with_name(mdx_path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(struct.pack(">I", len(new_header)))
        f.write(new_header)
        f.write(struct.pack("<I", zlib.adler32(new_header) & 0xFFFFFFFF))
        f.write(remainder)
    os.replace(tmp, mdx_path)


def generate_preview(entries_html: list[str], output_path: Path,
                     title: str) -> None:
    """Generate a preview HTML file for visual inspection.

    Produces a standalone HTML page with all entry samples separated
    by horizontal rules, supporting both light and dark mode.

    Args:
        entries_html: List of complete entry HTML strings
                      (each already includes embedded CSS).
        output_path: Where to write the preview HTML file.
        title: Title shown in the preview header and ``<title>`` tag.
    """
    joiner = '<hr style="margin:20px 0;border:none;border-top:1px solid #ddd">'

    page = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title>'
        '<style>body{font-family:-apple-system,system-ui,sans-serif;'
        'max-width:650px;margin:40px auto;padding:0 20px;background:#fff}'
        '@media(prefers-color-scheme:dark){body{background:#1c1c1e}}</style>'
        '</head><body>'
        '<h2 style="color:#999;font-weight:300;font-size:14px">'
        f'{title}</h2>'
    )
    page += joiner.join(entries_html)
    page += '</body></html>'

    output_path.write_text(page, encoding='utf-8')
    print(f"  Preview: {output_path}")


def write_mdict_source(entries: list[tuple[str, str]],
                       output_path: Path) -> int:
    """Write MDict source text file.

    Args:
        entries: List of (headword, html_or_redirect) pairs.
                 Redirects use ``@@@LINK=target`` as body.
        output_path: Where to write the .txt file.

    Returns:
        Number of entries written.
    """
    count = 0
    with open(output_path, 'w', encoding='utf-8') as f:
        for hw, body in entries:
            f.write(f"{hw}\n{body}\n</>\n")
            count += 1
    return count


def write_catalog_metadata(
    mdx_path: Path,
    display_name: str,
    description: str,
    direction: dict,          # {"src": "ZH", "dest": ["EN"]}
    entries: int,
    license: str,
    data_source: str,
    tier: int | None = 2,     # 0=core, 1=secondary, 2=alternative, 3=extra, None=hidden
    flag: str | None = None,  # Override; default derives from direction["src"]
) -> None:
    """Write a ``.meta.json`` sidecar alongside an MDX for catalog generation.

    The sidecar contains all per-dictionary metadata that the catalog
    generator needs.  Fields like ``id``, ``source``, and ``odrTag`` are
    derived later by ``generate_preset_catalog.py``.

    Args:
        mdx_path: Path to the packed ``.mdx`` file (must already exist).
        display_name: Human-readable dictionary name.
        description: One-line description shown in the app.
        direction: ``{"src": "XX", "dest": ["YY"]}`` language direction.
        entries: Total number of MDict entries (headwords + redirects).
        license: SPDX-style license string.
        data_source: Attribution / upstream project name.
        tier: Importance tier (0–3) or ``None`` to hide from catalog.
        flag: Emoji flag override.  Defaults to ``FLAGS[direction["src"]]``.
    """
    if flag is None:
        flag = FLAGS.get(direction["src"], "🏳️")

    size_bytes = mdx_path.stat().st_size

    meta = {
        "filename": mdx_path.name,
        "displayName": display_name,
        "description": description,
        "direction": direction,
        "flag": flag,
        "entries": entries,
        "sizeBytes": size_bytes,
        "license": license,
        "dataSource": data_source,
        "tier": tier,
    }

    meta_path = mdx_path.with_suffix('.meta.json')
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    print(f"  Metadata: {meta_path}")
