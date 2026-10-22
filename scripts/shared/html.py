"""
OpenMDict shared HTML helpers.

Provides the common `esc()` function (replaces 15 copies of `_esc()`)
and optional convenience builders for semantic HTML elements.

These helpers are OPTIONAL — scripts can still build HTML strings
directly if they need finer control.

Escaping
    esc(text)                        element content: escapes &, <, >
    esc(text, decode_entities=True)  same, after decoding the character
                                     references upstream text already carries
                                     ('пи&#769;сьменность', 'vous&nbsp;?'), so
                                     they render instead of showing literally
    esc_attr(text)                   double-quoted attribute values (also ")
    entry_link(target, label)        '<a href="entry://target">label</a>'

Right-to-left text
    Mark RTL text with the HTML ``dir`` attribute, not ``style="direction:rtl"``.
    The attribute sets the base direction AND isolates the run from its
    surroundings, so verse numbers, digits and final punctuation land on the
    correct side. Add ``lang`` as well; it selects the right font and shaping.

    Whole article RTL (e.g. the Arabic Bible)::

        wrap_entry(inner, CSS, rtl=True, lang='ar')
            -> CSS + '<div class="e" dir="rtl" lang="ar">' + inner + '</div>'
        # LTR islands inside it (English subtitle, transliteration):
        f'<span class="sub"{dir_attr("ltr", "en")}>Genesis 1</span>'

    RTL headword on its own (right-aligned) line in an LTR article (AR->EN)::

        headword(word, rtl=True, lang='ar')
            -> '<span class="hw" dir="rtl" lang="ar">كتاب</span>'

    RTL run inside LTR text (keeps the line's alignment)::

        rtl_span('كتاب', lang='ar') -> '<span dir="rtl" lang="ar">كتاب</span>'

    Decide per language or per string with is_rtl_lang('ar') / has_rtl(text);
    strip_bidi_controls() removes stray LRM/RLM marks from upstream text.

    Dictionaries that put .ex, .d / ol / ul or .sn-num blocks INSIDE a
    dir="rtl" element should also add theme.RTL to get_css(); it mirrors the
    left-hand border, indent and margins of those blocks (light and dark).
"""

import html as _stdlib_html
import re
import unicodedata


# ── Escaping ─────────────────────────────────────────────────

def esc(text: str, decode_entities: bool = False) -> str:
    """HTML-escape text for element content. Replaces &, <, >.

    Args:
        text: Raw text.
        decode_entities: First decode HTML character references that are
            already in the text ('&#769;', '&nbsp;', '&equals;', '&amp;'), so
            they render as the characters they stand for instead of showing
            up literally as '&amp;#769;'. Use it for upstream fields that
            contain entities (e.g. Wiktionary-derived glosses). Leave it off
            for plain text, where a literal '&nbsp;' must stay visible.
    """
    if decode_entities:
        text = _stdlib_html.unescape(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def esc_attr(text: str) -> str:
    """HTML-escape text for a double-quoted attribute value (&, <, >, ").

    Identical to esc() for text without '"'.
    """
    return esc(text).replace('"', '&quot;')


def entry_link(target: str, label: str | None = None) -> str:
    """Cross-reference link to another headword of the same dictionary.

    Args:
        target: Raw key text; it must exist as a key (or @@@LINK key) in the
                same MDX. Targets containing '/', ':' or spaces are fine.
                The app splits the target at the first '#'
                (entry://word#anchor), so a key containing '#' cannot be
                linked.
        label:  Raw link text; defaults to the target.

    Returns:
        '<a href="entry://{target}">{label}</a>' — byte-identical to the
        hand-written f'<a href="entry://{esc(t)}">{esc(t)}</a>' unless the
        target contains '"' (escaped as &quot; so the attribute stays intact).
    """
    if label is None:
        label = target
    return f'<a href="entry://{esc_attr(target)}">{esc(label)}</a>'


# ── Right-to-left text ───────────────────────────────────────

# Primary language subtags (ISO 639-1/2/3, lowercase) whose default script
# is written right to left. A script subtag overrides this (see is_rtl_lang).
RTL_LANGS = frozenset({
    'ar', 'arb', 'arc', 'arz', 'apc', 'ajp', 'acm', 'aeb', 'afb', 'ary',
    'arq', 'ayl', 'apd', 'azb', 'bal', 'bqi', 'ckb', 'dv', 'fa', 'glk',
    'hbo', 'he', 'iw', 'jrb', 'ks', 'khw', 'lrc', 'mzn', 'nqo', 'ota',
    'pes', 'pnb', 'prs', 'ps', 'sam', 'sd', 'skr', 'syc', 'syr', 'ug',
    'ur', 'yi', 'ydd', 'yih', 'ji',
})

# ISO 15924 script subtags written right to left.
_RTL_SCRIPTS = frozenset({
    'Adlm', 'Arab', 'Aran', 'Armi', 'Avst', 'Cprt', 'Hebr', 'Hung', 'Khar',
    'Mand', 'Mani', 'Mend', 'Narb', 'Nbat', 'Nkoo', 'Orkh', 'Palm', 'Phli',
    'Phlp', 'Phnx', 'Prti', 'Rohg', 'Samr', 'Sarb', 'Sogd', 'Sogo', 'Syrc',
    'Thaa', 'Yezi',
})


def is_rtl_lang(code: str) -> bool:
    """True if text in language `code` is written right to left.

    Accepts ISO 639 codes or BCP 47 tags in any case and with '-' or '_':
    'ar', 'AR', 'he-IL', 'fa', 'ur', 'yi' -> True; 'en', 'tr', 'ko' -> False.
    A script subtag decides when present: 'ku-Arab', 'pa-Arab' -> True,
    'ar-Latn' -> False.
    """
    if not code:
        return False
    parts = code.replace('_', '-').split('-')
    for sub in parts[1:]:
        if len(sub) == 4 and sub.isalpha():
            return sub.title() in _RTL_SCRIPTS
    return parts[0].lower() in RTL_LANGS


def _build_rtl_char_re() -> re.Pattern:
    """Character class of every code point whose bidi class is R or AL."""
    blocks = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF),
              (0x10800, 0x10FFF), (0x1E800, 0x1EFFF))
    runs: list[list[int]] = []
    for lo, hi in blocks:
        for cp in range(lo, hi + 1):
            if unicodedata.bidirectional(chr(cp)) in ('R', 'AL'):
                if runs and runs[-1][1] == cp - 1:
                    runs[-1][1] = cp
                else:
                    runs.append([cp, cp])
    cls = ''.join(re.escape(chr(a)) if a == b
                  else f'{re.escape(chr(a))}-{re.escape(chr(b))}'
                  for a, b in runs)
    return re.compile(f'[{cls}]')


_RTL_CHAR_RE = _build_rtl_char_re()


def has_rtl(text: str) -> bool:
    """True if `text` contains a strong right-to-left letter.

    Hebrew, Arabic, Syriac, Thaana, N'Ko, … (Unicode bidi class R or AL).
    Arabic-Indic digits, harakat and punctuation alone do not count.
    """
    return _RTL_CHAR_RE.search(text) is not None


# LRM, RLM, ALM, LRE/RLE/PDF/LRO/RLO, LRI/RLI/FSI/PDI
_BIDI_CONTROLS = dict.fromkeys(map(ord, '\u200e\u200f\u061c'
                                        '\u202a\u202b\u202c\u202d\u202e'
                                        '\u2066\u2067\u2068\u2069'))


def strip_bidi_controls(text: str) -> str:
    """Remove invisible bidi control characters from upstream text.

    Strips LRM/RLM/ALM marks, embeddings, overrides and isolates. Once the
    markup sets dir=, stray marks (e.g. an LRM before a verse-final period)
    only fight it.
    """
    return text.translate(_BIDI_CONTROLS)


def dir_attr(direction: str | bool | None = 'rtl', lang: str = '') -> str:
    """Attribute string to splice into a start tag: ' dir="rtl" lang="ar"'.

    Args:
        direction: 'rtl' (or True), 'ltr', 'auto' (direction of the first
                   strong character), or ''/False/None for no dir attribute.
        lang:      BCP 47 tag ('ar', 'he', 'en', 'ja'); omitted when empty.

    Examples:
        f'<div class="e"{dir_attr("rtl", "ar")}>'   -> '<div class="e" dir="rtl" lang="ar">'
        f'<span class="sub"{dir_attr("ltr", "en")}>' -> '<span class="sub" dir="ltr" lang="en">'
        dir_attr(False) -> ''
    """
    if isinstance(direction, bool) or direction is None:
        direction = 'rtl' if direction else ''
    if direction not in ('', 'rtl', 'ltr', 'auto'):
        raise ValueError(f'dir_attr: unknown direction {direction!r}')
    out = f' dir="{direction}"' if direction else ''
    if lang:
        out += f' lang="{esc_attr(lang)}"'
    return out


def rtl_span(text: str, lang: str = '', cls: str = '') -> str:
    """Inline right-to-left run (text is escaped).

    For RTL words inside LTR text, e.g. an Arabic form in an English gloss:
    '<span dir="rtl" lang="ar">كتاب</span>'. The span isolates the run, so
    a following '.', digit or Latin word keeps its place, and the line keeps
    the alignment of its block.

    Args:
        text: Raw text.
        lang: Optional BCP 47 tag ('ar', 'he', 'fa').
        cls:  Optional class name(s) for the span.
    """
    cls_attr = f' class="{esc_attr(cls)}"' if cls else ''
    return f'<span{cls_attr}{dir_attr("rtl", lang)}>{esc(text)}</span>'


# ── Entry wrapper ────────────────────────────────────────────

def wrap_entry(content: str, css: str, rtl: bool = False, lang: str = '') -> str:
    """Wrap inner HTML in CSS + entry root div.

    Args:
        content: Inner HTML (already built — NOT escaped).
        css: CSS string from theme.get_css().
        rtl: True when the whole article reads right to left (dir="rtl").
        lang: Optional BCP 47 language tag of the article ('ar', 'ja').

    Returns:
        '{css}<div class="e">{content}</div>' (with ' dir="rtl"' and/or
        ' lang="…"' on the div when requested).
    """
    return f'{css}<div class="e"{dir_attr("rtl" if rtl else "", lang)}>{content}</div>'


# ── Semantic HTML builders (optional) ────────────────────────
#
# Functions that take raw text call esc() internally.
# Functions that take pre-built HTML do NOT escape.

def dir_label(text: str) -> str:
    """Direction/language label header bar.

    Args:
        text: Pre-built label string (e.g., '🇬🇧🇺🇸 English').
              NOT escaped — may contain emoji or HTML.
    """
    return f'<span class="dir">{text}</span>'


def headword(text: str, rtl: bool = False, lang: str = '') -> str:
    """Headword element.

    Args:
        text: Raw headword text.
        rtl:  True for a right-to-left headword (dir="rtl"); .hw is a block,
              so the line is right-aligned. Use rtl_span() inside a plain
              .hw instead to keep it aligned with an LTR article.
        lang: Optional BCP 47 tag ('ar', 'he').
    """
    return f'<span class="hw"{dir_attr("rtl" if rtl else "", lang)}>{esc(text)}</span>'


def pronunciation(text: str) -> str:
    """IPA or phonetic pronunciation."""
    return f'<span class="pr">{esc(text)}</span>'


def pos_badge(text: str) -> str:
    """Part-of-speech badge."""
    return f'<span class="pos">{esc(text)}</span>'


def mark_badge(text: str) -> str:
    """Usage mark badge (obs., colloq., etc.)."""
    return f'<span class="mk">{esc(text)}</span>'


def sense_card(content: str) -> str:
    """Sense card wrapper.

    Args:
        content: Inner HTML (already built — NOT escaped).
    """
    return f'<div class="sn">{content}</div>'


def sense_header(*badges: str) -> str:
    """Sense header row with pre-built badge HTML strings."""
    return f'<div class="sn-hd">{"".join(badges)}</div>'


def definition(text: str) -> str:
    """Definition text (escaped)."""
    return f'<span class="df">{esc(text)}</span>'


def gloss(text: str) -> str:
    """Gloss/translation text (escaped)."""
    return f'<span class="gl">{esc(text)}</span>'


def example_block(quote: str, author: str = '') -> str:
    """Monolingual quotation example block."""
    parts = [f'<span class="ex-q">{esc(quote)}</span>']
    if author:
        parts.append(f'<span class="ex-a">{esc(author)}</span>')
    return f'<div class="ex">{"".join(parts)}</div>'


def example_bilingual(source: str, translation: str = '') -> str:
    """Bilingual example block (source language + translation)."""
    parts = [f'<span class="ex-src">{esc(source)}</span>']
    if translation:
        parts.append(f'<span class="ex-tr">{esc(translation)}</span>')
    return f'<div class="ex">{"".join(parts)}</div>'


def separator() -> str:
    """Horizontal separator."""
    return '<hr class="sep">'


# ── Kana → Romaji conversion (Hepburn) ──────────────────────
#
# Shared by build_jmdict_mdx.py and build_kanjidic2_mdx.py.
# JMdict also uses split_morae() and the raw tables for pitch
# accent rendering.
#
# Modified Hepburn: shi/chi/tsu/fu/ji, ぢ/づ → ji/zu, ん before a vowel or
# y → n', っ doubles the next consonant (tch), ー repeats the previous
# vowel. Iteration marks ゝゞヽヾ repeat the previous kana (ゞ/ヾ voiced).
# Characters that are not kana (・, ＝, Latin, digits) pass through.

_KATA_TO_HIRA = str.maketrans(
    'ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ'
    'タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ'
    'マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶヽヾ',
    'ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞ'
    'ただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽ'
    'まみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖゝゞ'
)

_ROMAJI_MAP = {
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'wi', 'ゑ': 'we', 'を': 'wo', 'ん': 'n',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ゔ': 'vu',
    # Katakana-only voiced w-row (no hiragana counterpart)
    'ヷ': 'va', 'ヸ': 'vi', 'ヹ': 've', 'ヺ': 'vo',
    # Kana digraphs ゟ (yori) and ヿ (koto)
    'ゟ': 'yori', 'ヿ': 'koto',
    # Small kana (used in combinations)
    'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o',
    'ゃ': 'ya', 'ゅ': 'yu', 'ょ': 'yo',
    'ゎ': 'wa',
}

# Two-char combinations (yōon)
_ROMAJI_DIGRAPH = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo',
    'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
    'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho',
    'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
    'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
    'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
    'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo',
    'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
    'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo',
    'ぢゃ': 'ja', 'ぢゅ': 'ju', 'ぢょ': 'jo',
    'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
    'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
    # Extended
    'てぃ': 'ti', 'でぃ': 'di', 'ふぁ': 'fa', 'ふぃ': 'fi',
    'ふぇ': 'fe', 'ふぉ': 'fo', 'ゔぁ': 'va', 'ゔぃ': 'vi',
    'ゔぇ': 've', 'ゔぉ': 'vo',
    'にぃ': 'nyi', 'にぇ': 'nye',
    'しぇ': 'she', 'ちぇ': 'che', 'じぇ': 'je', 'ぢぇ': 'je',
    'つぁ': 'tsa', 'つぃ': 'tsi', 'つぇ': 'tse', 'つぉ': 'tso',
    'きぇ': 'kye', 'ひぇ': 'hye', 'みぇ': 'mye', 'りぇ': 'rye',
    'ぎぇ': 'gye', 'びぇ': 'bye', 'ぴぇ': 'pye',
    'いぇ': 'ye',
    'うぃ': 'wi', 'うぇ': 'we', 'うぉ': 'wo',
    'くぁ': 'kwa', 'くぃ': 'kwi', 'くぇ': 'kwe', 'くぉ': 'kwo',
    'ぐぁ': 'gwa', 'ぐぃ': 'gwi', 'ぐぇ': 'gwe', 'ぐぉ': 'gwo',
    'すぃ': 'si', 'ずぃ': 'zi',
    'とぅ': 'tu', 'どぅ': 'du',
    'てゅ': 'tyu', 'でゅ': 'dyu', 'ふゅ': 'fyu',
    'ゔゅ': 'vyu',
}

_SMALL_VOWEL = {'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o'}
_SMALL_Y = {'ゃ': 'a', 'ゅ': 'u', 'ょ': 'o'}
_ITERATION = {'ゝ': False, 'ゞ': True}   # mark -> voiced?


def _combine(base: str, small: str) -> str | None:
    """Romaji for kana + small kana when the pair is not in _ROMAJI_DIGRAPH.

    base is the romaji of the full-size kana, small the small kana.
    """
    if small in _SMALL_Y:
        v = _SMALL_Y[small]
        if base.endswith('i'):
            stem = base[:-1]
            if stem.endswith(('sh', 'ch', 'j')):
                return stem + v                 # ぢゃ ja
            return stem + 'y' + v               # きゃ kya, いゃ ya
        if len(base) > 1 and base[-1] in 'aueo':
            base = base[:-1]
        return base + 'y' + v                   # でゅ dyu, ふゅ fyu
    if small in _SMALL_VOWEL:
        v = _SMALL_VOWEL[small]
        if base[-1] == v:
            return base + v                     # ねぇ nee, りぃ rii
        if base in ('fu', 'tsu', 'vu'):
            return base[:-1] + v                # ふぁ fa, つぁ tsa
        if base == 'u':
            return 'w' + v                      # うぃ wi
        if base == 'i':
            return 'y' + v                      # いぇ ye
        if base.endswith('u'):
            return base[:-1] + 'w' + v          # くぉ kwo, すぇ swe
        if base.endswith('i'):
            stem = base[:-1]
            if stem.endswith(('sh', 'ch', 'j')):
                return stem + v                 # しぇ she
            return stem + 'y' + v               # きぇ kye
        return base + v                         # げぃ gei
    if small == 'ゎ':
        return (base[:-1] if base.endswith('u') else base) + 'wa'   # くゎ kwa
    return None


def _mora_romaji(mora: str) -> str | None:
    """Romaji for one hiragana mora (1 char, or kana + small kana)."""
    rom = _ROMAJI_DIGRAPH.get(mora)
    if rom:
        return rom
    if len(mora) == 1:
        return _ROMAJI_MAP.get(mora)
    base = _ROMAJI_MAP.get(mora[0])
    return _combine(base, mora[1]) if base else None


def _iterate(prev: str, voiced: bool) -> str:
    """Kana repeated by an iteration mark: ゝ unvoiced, ゞ voiced."""
    first = unicodedata.normalize('NFD', prev[0])[0]          # drop (han)dakuten
    if voiced:
        v = unicodedata.normalize('NFC', first + '\u3099')
        if len(v) == 1:
            first = v
    return first + prev[1:]


def split_morae(kana: str) -> list[str]:
    """Split kana into morae (accounting for small kana combinations)."""
    small_kana = set('ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ')
    morae: list[str] = []
    i = 0
    while i < len(kana):
        ch = kana[i]
        if i + 1 < len(kana) and kana[i + 1] in small_kana:
            morae.append(kana[i:i+2])
            i += 2
        else:
            morae.append(ch)
            i += 1
    return morae


def kana_to_romaji_morae(kana: str, particle_wa: bool = False) -> list[str]:
    """Convert kana to Hepburn romaji, returning one string per mora.

    Keeps segments aligned 1:1 with split_morae(), so pitch accent
    contour can be applied to romaji as well (used by JMdict).

    Args:
        kana: Hiragana and/or katakana.
        particle_wa: Romanize a final は as the particle 'wa' (こんにちは →
            konnichiwa, では → dewa). Kana alone cannot tell the particle
            from a lexical は (歯 ha, 枯れ葉 kareha), so the caller decides,
            e.g. from a written form that ends in kana は. Needs 2+ morae.
    """
    orig = split_morae(kana)
    morae = split_morae(kana.translate(_KATA_TO_HIRA))
    # Resolve iteration marks to the kana they repeat.
    for idx, mora in enumerate(morae):
        if mora in _ITERATION and idx > 0 and _mora_romaji(morae[idx - 1]):
            morae[idx] = _iterate(morae[idx - 1], _ITERATION[mora])
    roms = [_mora_romaji(m) for m in morae]
    result: list[str] = []
    for idx, mora in enumerate(morae):
        if mora == 'っ':
            next_rom = roms[idx + 1] if idx + 1 < len(morae) else None
            if next_rom and next_rom[0].isalpha():
                result.append('t' if next_rom.startswith('ch') else next_rom[0])
            else:
                result.append('t')
            continue
        if mora == 'ー':
            if result and result[-1] and result[-1][-1] in 'aeiou':
                result.append(result[-1][-1])
            else:
                result.append('-')
            continue
        rom = roms[idx]
        if rom is None:
            result.append(orig[idx])     # not kana: keep as written
            continue
        if rom == 'n' and mora == 'ん' and idx + 1 < len(morae):
            next_rom = roms[idx + 1]
            if next_rom and next_rom[0] in 'aiueoy':
                rom = "n'"
        result.append(rom)
    if particle_wa and len(morae) > 1 and morae[-1] == 'は':
        result[-1] = 'wa'
    return result


def kana_to_romaji(kana: str, particle_wa: bool = False) -> str:
    """Convert kana string to Hepburn romaji.

    See kana_to_romaji_morae() for `particle_wa`.
    """
    return ''.join(kana_to_romaji_morae(kana, particle_wa))
