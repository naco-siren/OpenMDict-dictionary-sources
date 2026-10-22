"""
OpenMDict shared CSS theme system.

Every MDX entry is self-contained HTML — CSS MUST be embedded in every entry
(MDict format requirement). This module provides the single source of truth
for all CSS rules, replacing the duplicated CSS blocks across 15 build scripts.

Architecture:
    CORE rules are included automatically (`.e`, `.dir`, `.hw`, `.sn`, etc.).
    EXTENSIONS are named (light, dark) tuples that scripts opt into.
    PRESETS are convenience lists grouping common extension combos.
    `get_css()` composes the final `<style>` block.

Usage:
    from shared.theme import get_css, PRONUNCIATION, EXAMPLES

    # Standard monolingual dictionary
    CSS = get_css(PRONUNCIATION, ETYMOLOGY, DEFINITION, EXAMPLES, SYNONYMS, CROSSREF)

    # Using a preset
    CSS = get_css(*BILINGUAL)

    # With script-specific extras
    CSS = get_css(*BILINGUAL, extra_light='.alt{color:#6e6e73}', extra_dark='.alt{color:#98989d}')

    # Dictionary with right-to-left blocks (see RTL below and shared.html)
    CSS = get_css(*BILINGUAL, RTL)

Colors:
    Every text color here reaches WCAG AA (>= 4.5:1) against the surface it
    is drawn on: the article background (#fff light / #1c1c1e dark, the
    app's secondarySystemGroupedBackground), .sn cards (#f8f8fa / #2c2c2e)
    and each badge's own background. Every light color has a dark override
    (the white .pos text works on both badge backgrounds). Keep both when
    adding rules: a light-only color stays in force in dark mode.
"""

# ── Color tokens (light, dark) ───────────────────────────────
#
# AA-checked pairs for builders' extra_light / extra_dark rules. Worst case
# is on an .sn card (#f8f8fa light / #2c2c2e dark):
#   f'.info{{color:{TEXT_SECONDARY[0]}}}'  +  dark f'.info{{color:{TEXT_SECONDARY[1]}}}'
# Avoid the light #8e8e93 / dark #636366 pair: 3.1:1 and 2.3:1 on cards.

TEXT_SECONDARY = ('#6e6e73', '#98989d')   # 4.78:1 / 4.85:1
TEXT_LINK = ('#0066cc', '#64d2ff')        # 5.25:1 / 8.10:1


# ── Core CSS rules (included in ALL dictionaries) ────────────
#
# These appear in 10+ of 15 scripts with identical styling.
# Scripts that don't use certain core classes (e.g., Moby doesn't
# use .sn) are unaffected — unused CSS adds ~200 bytes, negligible.

_CORE_LIGHT = (
    '.e{font-family:-apple-system,system-ui,sans-serif;max-width:600px;'
    'line-height:1.5;color:#1d1d1f;padding:0 12px}'
    '.dir{font-size:0.8em;font-weight:500;letter-spacing:0.05em;color:#6e6e73;'
    'display:block;margin:0 0 4px;padding:4px 0;border-bottom:1px solid #e5e5ea}'
    '.hw{font-size:1.7em;font-weight:600;display:block}'
    '.sn{margin:8px 0;padding:8px 10px;background:#f8f8fa;border-radius:6px;'
    'border:1px solid #e5e5ea}'
    '.sn+.sn{margin-top:6px}'
    '.sn-hd{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin-bottom:4px}'
    '.pos{display:inline-block;font-size:0.85em;font-style:normal;color:#fff;'
    'background:#6e6e73;border-radius:3px;padding:0 5px}'
    '.mk{display:inline-block;font-size:0.75em;color:#636366;background:#e8e8ed;'
    'border-radius:3px;padding:0 5px}'
    '.sep{margin:10px 0;border:none;border-top:1px solid #e5e5ea}'
)

_CORE_DARK = (
    '.e{color:#f5f5f7}.dir{color:#98989d;border-color:#38383a}'
    '.sn{background:#2c2c2e;border-color:#38383a}'
    '.pos{background:#636366}'
    '.mk{color:#aeaeb2;background:#3a3a3c}'
    '.sep{border-color:#38383a}'
)


# ── Named Extensions ─────────────────────────────────────────
#
# Each is a (light_rules, dark_rules) tuple.
# Scripts include only the extensions they need.

PRONUNCIATION = (
    '.pr{font-size:1.1em;color:#0066cc;font-style:italic;display:block;margin-top:2px}',
    '.pr{color:#64d2ff}'
)

ETYMOLOGY = (
    '.et{font-size:0.85em;color:#6e6e73;margin:4px 0;line-height:1.4;display:block}',
    '.et{color:#98989d}'
)

INFLECTION = (
    '.inf{font-size:0.95em;color:#6e6e73;display:block;margin-top:2px}',
    '.inf{color:#98989d}'
)

DEFINITION = (
    '.df{font-size:1.1em}'
    '.tr{font-weight:600}',
    ''
)

SENSE_NUMBER = (
    '.sn-num{font-size:0.85em;font-weight:700;color:#6e6e73;margin-right:4px}',
    '.sn-num{color:#98989d}'
)

SENSE_NUMBER_INLINE = (
    '.sn-n{display:inline-block;font-size:0.8em;color:#636366;font-weight:600;min-width:18px}',
    '.sn-n{color:#98989d}'
)

EXAMPLES = (
    '.ex{font-size:0.85em;color:#6e6e73;margin:4px 0 2px 10px;padding:4px 0 4px 10px;'
    'border-left:3px solid #d1d1d6;line-height:1.4}'
    '.ex-q{display:block;font-style:italic}'
    '.ex-a{display:block;text-align:right;color:#6e6e73}',
    '.ex{color:#98989d;border-color:#48484a}'
    '.ex-a{color:#98989d}'
)

EXAMPLES_BILINGUAL = (
    '.ex{font-size:0.85em;color:#6e6e73;margin:4px 0 2px 10px;padding:4px 0 4px 10px;'
    'border-left:3px solid #d1d1d6;line-height:1.4}'
    '.ex-src{display:block;font-style:italic}'
    '.ex-tr{display:block;color:#48484a}',
    '.ex{color:#98989d;border-color:#48484a}'
    '.ex-tr{color:#a0a0a5}'
)

SYNONYMS = (
    '.syn{font-size:0.85em;color:#636366;margin:4px 0 0}',
    '.syn{color:#98989d}'
)

CROSSREF = (
    '.xr a{color:#0066cc;text-decoration:underline}',
    '.xr a{color:#64d2ff}'
)

GREEK_TEXT = (
    '.grk{color:#636366}',
    '.grk{color:#98989d}'
)

DOMAIN_BADGE = (
    '.dm{display:inline-block;font-size:0.75em;color:#636366;background:#e8e8ed;'
    'border-radius:3px;padding:0 5px}',
    '.dm{color:#aeaeb2;background:#3a3a3c}'
)

TAG_BADGE = (
    '.tg{display:inline-block;font-size:0.75em;color:#7d5f2e;background:#fef3d0;'
    'border-radius:3px;padding:0 5px}',
    '.tg{color:#d4a94c;background:#3a3520}'
)

GLOSS = (
    '.gl{font-size:1.15em}',
    ''
)

ORDERED_LIST = (
    '.d{margin:2px 0;padding-left:1.5em}'
    'ol,ul{padding-left:1.5em}'
    '.d li{margin:3px 0;font-size:1.15em}'
    '.d a{color:#0066cc;text-decoration:underline}',
    '.d a{color:#64d2ff}'
)

ROMANIZATION = (
    '.rm{font-size:1.1em;color:#6e6e73;display:block;margin-top:1px}',
    '.rm{color:#98989d}'
)

NESTED_SENSES = (
    '.sn .sn{margin:6px 0 0;background:#fff}'
    '.sn .sn .sn{background:#f8f8fa}',
    '.sn .sn{background:#1c1c1e}'
    '.sn .sn .sn{background:#2c2c2e}'
)

CJK_READING_TAGS = (
    '.tag{display:inline-block;font-size:1.0em;border-radius:4px;padding:1px 8px;margin:2px}'
    '.tag-on{background:#e8f0fe;color:#1967d2}'
    '.tag-kun{background:#e6f4ea;color:#137333}'
    '.tag-py{background:#fef3d0;color:#7d5f2e}'
    '.tag-ko{background:#f3e8fd;color:#7c3aed}'
    '.tag-vi{background:#fce8e6;color:#c5221f}',
    '.tag-on{background:#1e3a5f;color:#8ab4f8}'
    '.tag-kun{background:#1e3a2f;color:#81c995}'
    '.tag-py{background:#3a3520;color:#d4a94c}'
    '.tag-ko{background:#2d1b4e;color:#b794f4}'
    '.tag-vi{background:#3a1c1a;color:#f28b82}'
)

# Right-to-left layout. Mark RTL content with the HTML dir attribute
# (shared.html: wrap_entry(rtl=True), headword(rtl=True), rtl_span(),
# dir_attr()); the attribute alone sets text direction and bidi isolation.
# This extension mirrors the theme's few physical left/right decorations —
# the .ex quote bar and indent, .ex-a alignment, .sn-num spacing and the
# .d / ol / ul indent — for blocks inside (or carrying) dir="rtl". Include
# it only in dictionaries that emit such blocks in RTL context. The
# [dir=rtl] selectors outrank the plain class rules, so the position of RTL
# in the get_css() argument list does not matter.
# Inside an RTL container, badges (.pos .mk .dm .tg) and the IPA /
# romanization lines (.pr .rm) take their direction from their own first
# letter (unicode-bidi:plaintext), so 'n.' or 'kitāb.' do not come out as
# '.n' or '.kitāb'; .pr/.rm stay aligned with the RTL article. Other Latin
# text inside an RTL container needs dir_attr('ltr').
# Limitation: an .ex / .d block inside a dir="ltr" island nested in a
# dir="rtl" container is still mirrored; keep LTR islands to inline text.
RTL = (
    '[dir=rtl] .ex,.ex[dir=rtl]{margin:4px 10px 2px 0;padding:4px 10px 4px 0;'
    'border-left:0;border-right:3px solid #d1d1d6}'
    '[dir=rtl] .ex-a,.ex-a[dir=rtl]{text-align:left}'
    '[dir=rtl] .sn-num{margin-right:0;margin-left:4px}'
    '[dir=rtl] .d,[dir=rtl] ol,[dir=rtl] ul,.d[dir=rtl],ol[dir=rtl],ul[dir=rtl]'
    '{padding-left:0;padding-right:1.5em}'
    '[dir=rtl] .pos,[dir=rtl] .mk,[dir=rtl] .dm,[dir=rtl] .tg,[dir=rtl] .pr,'
    '[dir=rtl] .rm{unicode-bidi:plaintext}'
    '[dir=rtl] .pr,[dir=rtl] .rm{text-align:right}',
    '[dir=rtl] .ex,.ex[dir=rtl]{border-right-color:#48484a}'
)


# ── Presets (convenience combos) ─────────────────────────────

# For standard Western monolingual dictionaries (GCIDE, etc.)
WESTERN_MONO = [
    PRONUNCIATION, ETYMOLOGY, DEFINITION,
    EXAMPLES, SYNONYMS, CROSSREF,
]

# For bilingual dictionaries (Kaikki, FreeDict, WikDict, etc.)
BILINGUAL = [
    GLOSS, ORDERED_LIST, DOMAIN_BADGE, TAG_BADGE,
    EXAMPLES_BILINGUAL,
]

# For Perseus classical dictionaries (LSJ, Lewis-Short)
CLASSICAL = [
    INFLECTION, DEFINITION, SENSE_NUMBER,
    EXAMPLES, GREEK_TEXT, CROSSREF, NESTED_SENSES,
]


# ── Composition ──────────────────────────────────────────────

def get_css(*extensions: tuple[str, str],
            extra_light: str = '', extra_dark: str = '') -> str:
    """Compose the final <style> block from core + selected extensions.

    Args:
        *extensions: Extension tuples from this module
                     (e.g., PRONUNCIATION, EXAMPLES).
                     Preset lists can be unpacked: get_css(*BILINGUAL).
        extra_light: Additional light-mode CSS rules (script-specific one-offs).
        extra_dark:  Additional dark-mode CSS rules (script-specific one-offs).

    Returns:
        Complete '<style>...</style>' string ready for embedding.

    Examples:
        CSS = get_css(PRONUNCIATION, EXAMPLES, SYNONYMS)
        CSS = get_css(*BILINGUAL)
        CSS = get_css(*BILINGUAL, extra_light='.alt{color:red}')
    """
    light_parts = [_CORE_LIGHT]
    dark_parts = [_CORE_DARK]

    for ext_light, ext_dark in extensions:
        light_parts.append(ext_light)
        if ext_dark:
            dark_parts.append(ext_dark)

    if extra_light:
        light_parts.append(extra_light)
    if extra_dark:
        dark_parts.append(extra_dark)

    return (
        '<style>'
        + ''.join(light_parts)
        + '@media(prefers-color-scheme:dark){'
        + ''.join(dark_parts)
        + '}'
        + '</style>'
    )
