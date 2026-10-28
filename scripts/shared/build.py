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

# Public repository with the conversion scripts and upstream source archives
# of the GPL/AGPL dictionaries (FreeDict, GCIDE); their descriptions link it.
DICTIONARY_SOURCES_URL = "https://github.com/naco-siren/OpenMDict-dictionary-sources"


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


# ── Dictionary descriptions (the MDX header's "Description") ────
#
# Every preset describes itself in one layout, so the app's dictionary
# info screen (which renders this HTML) reads alike for all of them and
# other MDict readers show the same credits:
#
#   <p><b>Title</b> (subtitle)</p>
#   <p>about …</p>                one paragraph or more: the content, how
#                                 to look things up, notation
#   <p><b>Source:</b> …</p>       the upstream work: name, version or
#                                 date, authors or copyright, links
#   <p><b>License:</b> …</p>      the licence the upstream data is used
#                                 under and the notices it requires
#   <p><b>Conversion:</b> Sourced and converted to MDict by Naco Siren
#       for OpenMDict: changes. adaptation licence. source code.</p>
#   <p><small>…</small></p>       licence texts reproduced verbatim
#
# All arguments are HTML fragments: callers escape their own text
# (shared.html.esc). The labels and the first sentence of the Conversion
# paragraph are localized per description language (the Bibles describe
# themselves in their own language): add a language to DESCRIPTION_LABELS
# instead of writing the layout by hand.

CONVERTER_NAME = "Naco Siren"
CONVERTER_URL = "https://github.com/naco-siren"
APP_NAME = "OpenMDict"

# Per language: "sep" follows every label; the labels carry their own
# punctuation; "converted" is the first sentence of the Conversion
# paragraph without its final period, with {converter} and {app} filled
# in by description_html(). Optional "converted_modified" is used instead
# when a list of changes follows (the GPL and CC BY-SA ask for a notice
# that the work was modified). Optional "colon" and "period" (default
# ": " and ".") surround the list of changes for scripts with their own
# punctuation.
DESCRIPTION_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "sep": " ",
        "source": "Source:",
        "license": "License:",
        "conversion": "Conversion:",
        "converted": "Sourced and converted to MDict by {converter} for {app}",
        "converted_modified": "Sourced, converted to MDict and modified by {converter} for {app}",
        "source_code": "Source code of this conversion:",
    },
    "zh-Hans": {
        "sep": "",
        "colon": "：",
        "period": "。",
        "source": "来源：",
        "license": "许可证：",
        "conversion": "转换：",
        "converted": "由 {converter} 为 {app} 搜集并转换为 MDict 格式",
        "source_code": "本次转换的源代码：",
    },
    "zh-Hant": {
        "sep": "",
        "colon": "：",
        "period": "。",
        "source": "來源：",
        "license": "授權：",
        "conversion": "轉換：",
        "converted": "由 {converter} 為 {app} 蒐集並轉換為 MDict 格式",
        "source_code": "本次轉換的原始碼：",
    },
    "ja": {
        "sep": "",
        "colon": "：",
        "period": "。",
        "source": "出典：",
        "license": "ライセンス：",
        "conversion": "変換：",
        "converted": "{app} のために {converter} が収集し、MDict 形式に変換",
        "source_code": "この変換のソースコード：",
    },
    "ko": {
        "sep": " ",
        "source": "출처:",
        "license": "라이선스:",
        "conversion": "변환:",
        "converted": "{app}를 위해 {converter}이 수집하여 MDict 형식으로 변환",
        "source_code": "이 변환의 소스 코드:",
    },
    "es": {
        "sep": " ",
        "source": "Fuente:",
        "license": "Licencia:",
        "conversion": "Conversión:",
        "converted": "Obtenido y convertido a MDict por {converter} para {app}",
        "source_code": "Código fuente de esta conversión:",
    },
    "fr": {
        "sep": " ",
        "source": "Source :",
        "license": "Licence :",
        "conversion": "Conversion :",
        "converted": "Recueilli et converti en MDict par {converter} pour {app}",
        "source_code": "Code source de cette conversion :",
    },
    "de": {
        "sep": " ",
        "source": "Quelle:",
        "license": "Lizenz:",
        "conversion": "Konvertierung:",
        "converted": "Beschafft und nach MDict konvertiert von {converter} für {app}",
        "source_code": "Quellcode dieser Konvertierung:",
    },
    "pt": {
        "sep": " ",
        "source": "Fonte:",
        "license": "Licença:",
        "conversion": "Conversão:",
        "converted": "Obtido e convertido em MDict por {converter} para {app}",
        "source_code": "Código-fonte desta conversão:",
    },
    "ru": {
        "sep": " ",
        "source": "Источник:",
        "license": "Лицензия:",
        "conversion": "Преобразование:",
        "converted": "Собрано и преобразовано {converter} в формат MDict для {app}",
        "source_code": "Исходный код этого преобразования:",
    },
    "ar": {
        "sep": " ",
        "source": "المصدر:",
        "license": "الترخيص:",
        "conversion": "التحويل:",
        "converted": "جُمع وحُوِّل إلى صيغة MDict على يد {converter} من أجل {app}",
        "source_code": "الشفرة المصدرية لهذا التحويل:",
    },
    "it": {
        "sep": " ",
        "source": "Fonte:",
        "license": "Licenza:",
        "conversion": "Conversione:",
        "converted": "Reperito e convertito in MDict da {converter} per {app}",
        "source_code": "Codice sorgente di questa conversione:",
    },
    "la": {
        "sep": " ",
        "source": "Fons:",
        "license": "Licentia:",
        "conversion": "Conversio:",
        "converted": "{converter} pro {app} collegit et in MDict convertit",
        "source_code": "Codex fontanus hujus conversionis:",
    },
    # Modern Greek: the language the Byzantine New Testament (lang "grc")
    # describes itself in.
    "grc": {
        "sep": " ",
        "source": "Πηγή:",
        "license": "Άδεια:",
        "conversion": "Μετατροπή:",
        "converted": "Άντληση και μετατροπή σε μορφή MDict από {converter} για το {app}",
        "source_code": "Πηγαίος κώδικας αυτής της μετατροπής:",
    },
}


def converter_link() -> str:
    """The converter's name linked to the public profile."""
    return f'<a href="{CONVERTER_URL}">{CONVERTER_NAME}</a>'


def description_html(
    *,
    title: str,
    source: str,
    license: str,
    subtitle: str = "",
    about: Iterable[str] = (),
    changes: str = "",
    adaptation_license: str = "",
    source_code_url: str = "",
    date: str = "",
    notices: Iterable[str] = (),
    lang: str = "en",
) -> str:
    """Compose a preset's header description in the shared layout.

    Args (HTML fragments; escape plain text with ``shared.html.esc``):
        title: Dictionary name, bold on the first line.
        source: The upstream work: name, version or date, authors or
            copyright holders, links.
        license: The licence of the upstream data and any notice it
            requires.
        subtitle: Edition or release shown after the title in parentheses.
        about: Paragraphs on the content and how to look things up.
        changes: What the conversion changed, as a lower-case list without
            a final period ("new layout, lookup keys for inflected forms").
            Licences such as CC BY-SA require it.
        adaptation_license: Sentence(s) on the licence of the converted
            file ("This adaptation is licensed under <a …>CC BY-SA 4.0</a>.").
        source_code_url: Where the conversion's source code is published
            (the GPL/AGPL dictionaries: DICTIONARY_SOURCES_URL).
        date: Month or date of the conversion ("October 2026"). Keep it a
            constant in the build script: the header must be reproducible.
        notices: Licence texts reproduced verbatim, shown small.
        lang: Language of the description, a key of DESCRIPTION_LABELS.
    """
    labels = DESCRIPTION_LABELS[lang]
    sep = labels["sep"]
    head = f"<b>{title}</b>"
    if subtitle:
        head += f" ({subtitle})"
    parts = [f"<p>{head}</p>"]
    parts += [f"<p>{p}</p>" for p in about if p]
    parts.append(f"<p><b>{labels['source']}</b>{sep}{source}</p>")
    parts.append(f"<p><b>{labels['license']}</b>{sep}{license}</p>")
    sentence = labels.get("converted_modified", labels["converted"]) if changes else labels["converted"]
    converted = sentence.format(converter=converter_link(), app=APP_NAME)
    if date:
        converted += f" ({date})"
    colon, period = labels.get("colon", ": "), labels.get("period", ".")
    conversion = converted + (f"{colon}{changes}{period}" if changes else period)
    if adaptation_license:
        conversion += f" {adaptation_license}"
    if source_code_url:
        shown = re.sub(r"^https?://", "", source_code_url)
        conversion += (f" {labels['source_code']}{sep}"
                       f'<a href="{source_code_url}">{shown}</a>.')
    parts.append(f"<p><b>{labels['conversion']}</b>{sep}{conversion}</p>")
    parts += [f"<p><small>{n}</small></p>" for n in notices if n]
    return "".join(parts)
