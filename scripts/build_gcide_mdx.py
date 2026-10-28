#!/usr/bin/env python3
"""
Build GCIDE (GNU Collaborative International Dictionary of English) MDX
from the CIDE.A-Z source files.

Usage:
    python build_gcide_mdx.py

Input:  ../data/gcide/gcide-0.53/CIDE.A .. CIDE.Z (+ INFO)
Output: ../output/GCIDE-EN/GCIDE-EN.mdx

How the source is read
----------------------
GCIDE is SGML-like markup, one <p>...</p> per paragraph (see the upstream
README, tagset.txt and webfont.txt). Characters are written as entities of
the form <eacute/ (no closing '>') or as \\'xx escapes. Every paragraph is
parsed into a small tree (entities resolved FIRST, so an entity can never be
mistaken for a tag), and the whole paragraph is rendered: every text run of
the source reaches the article. A build check compares the letters of every
paragraph with the letters of its rendered HTML and fails on any loss.

Articles and keys
-----------------
A paragraph with <ent> starts a homograph group; paragraphs without <ent>
continue the current group. Groups whose key is equal ignoring case form one
article, each group with its own headword, pronunciation, part of speech
and etymology. Every <ent> of a paragraph is a key (the first is the
article key, the others redirect to it). Paragraphs that list several
headwords (the "Un-" lists) give each listed word its own short article.
Collocations (<col> inside <cs>) get their own short article keyed by the
phrase ("let alone", "take care of"). Accented spellings (café), variant
spellings (<asp>), inflected forms (<conjf>, <plw>, <adjf>, ...) and derived
forms (<wf>) redirect to the article that defines them.
"""

import codecs
import html as _stdlib_html
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── Shared infrastructure ─────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from shared.theme import get_css, PRONUNCIATION, ETYMOLOGY, DEFINITION, \
    SENSE_NUMBER, EXAMPLES, SYNONYMS, TEXT_SECONDARY, TEXT_LINK
from shared.html import esc, esc_attr, dir_label
from shared.build import (pack_mdx, generate_preview, write_catalog_metadata,
                          description_html, DICTIONARY_SOURCES_URL)

# ── Paths ──────────────────────────────────────────────────────
GCIDE_VERSION = "0.53"
DATA_DIR = SCRIPT_DIR.parent / "data" / "gcide" / f"gcide-{GCIDE_VERSION}"
OUTPUT_DIR = SCRIPT_DIR.parent / "output" / "GCIDE-EN"
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


# ══════════════════════════════════════════════════════════════
# 1. Source text
# ══════════════════════════════════════════════════════════════

# Three bytes in 0.53 are not UTF-8 (cp1252 leftovers). Anything else
# that fails to decode stops the build instead of turning into U+FFFD.
#   CIDE.B "market\x92s"  CIDE.T "fa\xe7ade"  CIDE.U "haven\xb9t"
_STRAY_BYTES = {0x92: '’', 0xE7: 'ç', 0xB9: '’'}


def _stray_byte_handler(err: UnicodeDecodeError):
    byte = err.object[err.start]
    if byte in _STRAY_BYTES:
        return _STRAY_BYTES[byte], err.start + 1
    raise err


codecs.register_error('gcide_stray', _stray_byte_handler)


def read_source(path: Path) -> str:
    return path.read_bytes().decode('utf-8', errors='gcide_stray')


# Headword typos that cannot be fixed by comparing <ent> and <hw>, each
# applied to its exact source context (CIDE file letter -> (old, new)).
#   over-the-top: the <mhw> repeats the headwords of over-the-counter, so
#     the article showed the wrong headword and "over the counter" found it
#   wannabe: the headword lost its last syllable (the <pr> has it)
#   fritz out: "frits" (the <ent> and the cross-reference say "fritz")
SOURCE_FIXES = {
    'O': [('<ent>over the top</ent><br/\n<mhw><hw>over-the-counter</hw>, '
           '<hw>over the counter</hw></mhw>',
           '<ent>over the top</ent><br/\n<mhw><hw>over-the-top</hw>, '
           '<hw>over the top</hw></mhw>')],
    'W': [('<hw>wan"na</hw> <pr>(w<ocr/n"n<ucr/*b<emac/`)</pr>',
           '<hw>wan"na*be`</hw> <pr>(w<ocr/n"n<ucr/*b<emac/`)</pr>')],
    'F': [('<hw>frits` out"</hw>', '<hw>fritz` out"</hw>')],
    # Two entries upstream has as commented-out drafts, kept as entries.
    'S': [('<-- <hw>shoo-in</hw>  <def>a candidate who is certain to win easily.</def> --><br/',
           '<hw>shoo-in</hw>  <def>a candidate who is certain to win easily.</def><br/')],
}
SOURCE_FIXES['W'] += [
    ('<-- <hw>water jacket</hw>.  A chamber surrounding a vessel or tube in which water '
     'may be circulated, thereby regulating the temperature or supply of heat to the '
     'vessel.  Used in laboratory and manufacturing equipment.<br/',
     '<hw>water jacket</hw>.  <def>A chamber surrounding a vessel or tube in which water '
     'may be circulated, thereby regulating the temperature or supply of heat to the '
     'vessel.  Used in laboratory and manufacturing equipment.</def><br/'),
    ('<p>  water-jacketed.  adj. Having a water jacket; -- as, a water-jacketed condenser. --><br/',
     '<p><hw>water-jacketed</hw>, <pos>a.</pos> <def>Having a water jacket; -- as, '
     'a water-jacketed condenser.</def><br/'),
]
SOURCE_FIXES_UNUSED: list[str] = []


def apply_source_fixes(letter: str, text: str) -> str:
    for old, new in SOURCE_FIXES.get(letter, []):
        n = text.count(old)
        if n > 1:
            raise SystemExit(f'source fix for CIDE.{letter} matches {n} times: {old[:60]!r}')
        if n == 0:          # fixed upstream (or the text changed): report it
            SOURCE_FIXES_UNUSED.append(f'CIDE.{letter}: {old[:60]!r}')
        text = text.replace(old, new)
    return text


_COMMENT_RE = re.compile(r'<--.*?-->', re.S)
# "[<source>1913 Webster</source>]", "[<source>A</source> + <source>B</source>]",
# "[<source>WordNet 1.5</source> PC]", and a few with the "]" missing.
# <source> never contains markup, so [^<]* cannot run past its own
# </source> (a lazy .*? did, and swallowed whole entries up to a later
# "</source>]": Alkanet, Bloodroot, Home run, ...).
_SOURCE_BLOCK_RE = re.compile(
    r'\[[^\[\]<>\n]{0,12}(?:<source>[^<]*</source>[^\[\]<>\n]{0,12})+\]?')
_SOURCE_TAG_RE = re.compile(r'<source>[^<]*</source>')
# Upstream typo: "<ent>newton<ent>" (opening tag used as the closing one)
_ENT_TYPO_RE = re.compile(r'<ent>([^<\n]*)<ent>')


def split_paragraphs(text: str) -> list[str]:
    """Paragraph bodies of one CIDE file, in order.

    Text between </p> and the next <p> that carries letters (a few entries
    were typed without <p>) is kept as a paragraph of its own.
    """
    text = _COMMENT_RE.sub('', text)
    text = _SOURCE_BLOCK_RE.sub('', text)
    text = _SOURCE_TAG_RE.sub('', text)
    text = _ENT_TYPO_RE.sub(r'<ent>\1</ent>', text)
    chunks = text.split('<p>')
    out = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            # File header (licence comment, already removed) and the
            # leading "<centered>NUMBERS.</centered>" style banners.
            body, tail = '', chunk
        else:
            j = chunk.find('</p>')
            body, tail = (chunk, '') if j < 0 else (chunk[:j], chunk[j + 4:])
        if body.strip():
            out.append(body)
        tail = tail.replace('</p>', ' ')
        if i and re.search(r'[A-Za-z]', re.sub(r'<[^<>]*>', '', tail)):
            out.append(tail)
    return out


# ══════════════════════════════════════════════════════════════
# 2. Entities (webfont.txt, pronunc.txt)
# ══════════════════════════════════════════════════════════════

_SUP = str.maketrans('0123456789', '⁰¹²³⁴⁵⁶⁷⁸⁹')
_SUB = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
_VULGAR = {
    '1/2': '½', '1/3': '⅓', '2/3': '⅔', '1/4': '¼', '3/4': '¾', '1/5': '⅕',
    '2/5': '⅖', '3/5': '⅗', '4/5': '⅘', '1/6': '⅙', '5/6': '⅚', '1/7': '⅐',
    '1/8': '⅛', '3/8': '⅜', '5/8': '⅝', '7/8': '⅞', '1/9': '⅑', '1/10': '⅒',
}

ENTITIES = {
    # Latin letters with diacritics (webfont.txt symbol table)
    'Cced': 'Ç', 'cced': 'ç', 'ccedil': 'ç', 'uum': 'ü', 'Uum': 'Ü',
    'eacute': 'é', 'Eacute': 'É', 'acir': 'â', 'acirc': 'â', 'aum': 'ä',
    'Aum': 'Ä', 'agrave': 'à', 'aring': 'å', 'Aring': 'Å', 'ecir': 'ê',
    'eum': 'ë', 'egrave': 'è', 'ium': 'ï', 'icir': 'î', 'icirc': 'î',
    'igrave': 'ì', 'ae': 'æ', 'AE': 'Æ', 'oe': 'œ', 'OE': 'Œ', 'ocir': 'ô',
    'oum': 'ö', 'Oum': 'Ö', 'ograve': 'ò', 'oacute': 'ó', 'Oacute': 'Ó',
    'ucir': 'û', 'ugrave': 'ù', 'uacute': 'ú', 'yum': 'ÿ', 'aacute': 'á',
    'iacute': 'í', 'ntil': 'ñ', 'Ntil': 'Ñ', 'ccar': 'č', 'cacute': 'ć',
    'ocar': 'ǒ', 'uring': 'ů', 'atil': 'ã', 'etil': 'ẽ',
    # "stil" occurs once, in Homer "(h<omac/"m<stil/r)": a typo for etil
    'stil': 'ẽ', 'mtil': 'm̃', 'ltil': 'l̃', 'ncir': 'n̂',
    # macrons, breves ("crescent"), semilong (macron + acute, as GNU Dico)
    'amac': 'ā', 'emac': 'ē', 'imac': 'ī', 'omac': 'ō', 'umac': 'ū',
    'ymac': 'ȳ', 'Omac': 'Ō', 'aemac': 'ǣ', 'oemac': 'œ̄',
    'acr': 'ă', 'ecr': 'ĕ', 'ecrl': 'ĕ', 'icr': 'ĭ', 'ocr': 'ŏ', 'ucr': 'ŭ',
    'ycr': 'y̆',
    'asl': 'ā́', 'esl': 'ḗ', 'isl': 'ī́', 'osl': 'ṓ', 'usl': 'ū́',
    # pronunciation-only letters (pronunc.txt)
    'adot': 'ȧ', 'dot': 'ȧ',          # "dot": once, Phanar "(făn"<dot/r)"
    'add': 'a̤', 'udd': 'ṳ',     # double dot below
    'oomac': 'o͞o', 'oocr': 'o͝o',
    'nsm': 'ṉ',                       # n sub-macron ("ng")
    'nsc': 'ɴ',                       # small-capital N (French nasal)
    'th': 't͟h',                 # ligated "th" of "this" (vs. thin)
    # dotted letters (Sanskrit, Arabic, Hebrew transliterations)
    'ndot': 'ṅ', 'mdot': 'ṁ', 'zdot': 'ż', 'dsdot': 'ḍ', 'nsdot': 'ṇ',
    'tsdot': 'ṭ', 'tsdo': 'ṭ', 'rsdot': 'ṛ', 'hsdot': 'ḥ', 'lsdot': 'ḷ',
    'msdot': 'ṃ', 'zsdot': 'ẓ', 'csdot': 'c̣', 'usdot': 'ụ',
    # Old English and Norse
    'edh': 'ð', 'EDH': 'Ð', 'thorn': 'þ', 'yogh': 'ȝ',
    # Greek letters
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε',
    'zeta': 'ζ', 'eta': 'η', 'theta': 'θ', 'iota': 'ι', 'kappa': 'κ',
    'lambda': 'λ', 'mu': 'μ', 'nu': 'ν', 'xi': 'ξ', 'omicron': 'ο',
    'pi': 'π', 'rho': 'ρ', 'sigma': 'σ', 'sigmat': 'ς', 'tau': 'τ',
    'upsilon': 'υ', 'phi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
    'digamma': 'ϝ', 'ALPHA': 'Α', 'BETA': 'Β', 'GAMMA': 'Γ', 'DELTA': 'Δ',
    'EPSILON': 'Ε', 'ZETA': 'Ζ', 'ETA': 'Η', 'THETA': 'Θ', 'IOTA': 'Ι',
    'KAPPA': 'Κ', 'LAMBDA': 'Λ', 'MU': 'Μ', 'NU': 'Ν', 'XI': 'Ξ',
    'OMICRON': 'Ο', 'PI': 'Π', 'RHO': 'Ρ', 'SIGMA': 'Σ', 'TAU': 'Τ',
    'UPSILON': 'Υ', 'PHI': 'Φ', 'CHI': 'Χ', 'PSI': 'Ψ', 'OMEGA': 'Ω',
    # accents and marks
    'prime': '′', 'min': '′', 'bprime': '″', 'Prime': '″', 'sec': '″',
    'cre': '˘', 'breve': '˘', 'umlaut': '¨', 'asper': 'ʽ',
    # punctuation and symbols
    'ldquo': '“', 'rdquo': '”', 'lsquo': '‘', 'rsquo': '’',
    'mdash': '—', 'sect': '§', 'para': '¶', 'hand': '☞', 'dagger': '†',
    'dag': '†', 'Dagger': '‡', 'ddag': '‡', 'deg': '°', 'pound': '£',
    'middot': '•', 'root': '√', 'cuberoot': '∛', 'divide': '÷', 'times': '×',
    'nabla': '∇', 'integral2l': '∫', 'ounceap': '℥', 'asterism': '⁂',
    'rarr': '→', 'larr': '←', 'gt': '>', 'lt': '<', 'nbsp': ' ',
    'schwa': 'ə', 'iques': '¿', 'bar': '|', 'lbrace2': '{', 'rbrace2': '}',
    'Crev': 'Ↄ', 'dele': '₰', '8star': '⚹', '2dot': '2̇', '3dot': '3̇',
    'filig': 'fi', 'fllig': 'fl', 'fflig': 'ff', 'ffllig': 'ffl',
    # music
    'sharp': '♯', 'flat': '♭', 'natural': '♮', 'pause': '\U0001D110',
    'segno': '\U0001D10B', 'upslur': '⌣', 'downslur': '⌢',
    # astronomy and astrology
    'Aries': '♈', 'Taurus': '♉', 'Gemini': '♊', 'Cancer': '♋', 'Leo': '♌',
    'Virgo': '♍', 'Libra': '♎', 'Scorpio': '♏', 'Sagittarius': '♐',
    'Capricorn': '♑', 'Aquarius': '♒', 'Pisces': '♓', 'Sun': '☉',
    'Mercury': '☿', 'Venus': '♀', 'Mars': '♂', 'Jupiter': '♃', 'Male': '♂',
    'astascending': '☊', 'astdescending': '☋',
    # layout-only entities
    'colbreak': ' ', 'colret': ' ', 'sb': ' ',
}

# Entities that stand for italic text: <or/ <and/ and the italic
# (obscure) vowels of pronunciations <ait/ <eit/ ...; <ai/ occurs once,
# in the pronunciation of oeillade.
ITALIC_ENTITIES = {'or': 'or', 'and': 'and', 'ai': 'a'}
_ITALIC_LETTER_RE = re.compile(r'^([A-Za-z])(?:it|IT)$')
_FRAC_RE = re.compile(r'^frac(\d+)x(\d+)$|^frac(\d)(\d)$')

# \'xx escapes (webfont.txt): only these two occur
HEX_ESCAPES = {'d8': '‖', '94': 'ö'}

UNKNOWN_ENTITIES: Counter = Counter()


def _fraction(num: str, den: str) -> str:
    return _VULGAR.get(f'{num}/{den}') or \
        f'{num.translate(_SUP)}⁄{den.translate(_SUB)}'


# ══════════════════════════════════════════════════════════════
# 3. Markup tree
# ══════════════════════════════════════════════════════════════

class El:
    """One tagged field. kids are El or str."""
    __slots__ = ('tag', 'attrs', 'kids')

    def __init__(self, tag: str, attrs: str = '', kids=None):
        self.tag = tag
        self.attrs = attrs
        self.kids = kids if kids is not None else []

    def __repr__(self):
        return f'El({self.tag!r}, {self.kids!r})'


# Pseudo-tags produced by entities
BR = 'br'        # <br/  line break
UNK = 'unk'      # <?/   character the typists could not read
VOID_TAGS = {'img', BR, UNK}

_TOKEN_RE = re.compile(
    # 1 entity; also the typos "</rdquo/", "</br/" and "<eit/>"
    r"</?([A-Za-z0-9?]+)/>?"
    r"|<(/?)([A-Za-z][A-Za-z0-9.]*)(\s[^<>]*)?>"      # 2 close, 3 tag, 4 attrs
    r"|\\'([0-9a-fA-F]{2})")                          # 5 \'xx escape

STRAY_CLOSERS: Counter = Counter()


def _entity_node(name: str):
    if name == 'br':
        return El(BR)
    if name == '?':
        return El(UNK)
    if name in ENTITIES:
        return ENTITIES[name]
    if name in ITALIC_ENTITIES:
        return El('it', kids=[ITALIC_ENTITIES[name]])
    m = _ITALIC_LETTER_RE.match(name)
    if m:
        return El('it', kids=[m.group(1)])
    m = _FRAC_RE.match(name)
    if m:
        num, den = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return _fraction(num, den)
    if name == 'frac00':
        return '0⁄0'
    UNKNOWN_ENTITIES[name] += 1
    return ''


def parse_markup(text: str) -> El:
    """Parse a paragraph into a tree. Tolerant: unknown closers are
    ignored, unclosed tags close at the end of the paragraph."""
    root = El('#root')
    stack = [root]
    pos = 0

    def add_text(s: str):
        if not s:
            return
        kids = stack[-1].kids
        if kids and isinstance(kids[-1], str):
            kids[-1] += s
        else:
            kids.append(s)

    for m in _TOKEN_RE.finditer(text):
        add_text(text[pos:m.start()])
        pos = m.end()
        if m.group(1) is not None:
            node = _entity_node(m.group(1))
            if isinstance(node, str):
                add_text(node)
            else:
                stack[-1].kids.append(node)
        elif m.group(5) is not None:
            add_text(HEX_ESCAPES.get(m.group(5).lower(), ''))
            if m.group(5).lower() not in HEX_ESCAPES:
                UNKNOWN_ENTITIES["\\'" + m.group(5)] += 1
        else:
            tag = m.group(3).lower()
            if m.group(2):  # closing tag
                for i in range(len(stack) - 1, 0, -1):
                    if stack[i].tag == tag:
                        del stack[i:]
                        break
                else:
                    STRAY_CLOSERS[tag] += 1
                    stack[-1].kids.append(El('/' + tag))
            else:
                node = El(tag, (m.group(4) or '').strip())
                stack[-1].kids.append(node)
                if tag not in VOID_TAGS:
                    stack.append(node)
    add_text(text[pos:])
    return root


def iter_nodes(node: El):
    yield node
    for k in node.kids:
        if isinstance(k, El):
            yield from iter_nodes(k)


# Fields that are bookkeeping, not dictionary text
DROP_TAGS = {'source', 'wns', 'w16ns', 'wn16s', 'fr', 'ver', 'img'}


def plain_text(node, greek: bool = True) -> str:
    """Text of a subtree with entities resolved (and <grk> as Greek)."""
    if isinstance(node, str):
        return node
    tag = node.tag
    if tag in DROP_TAGS or tag.startswith('/'):
        return ''
    if tag == BR:
        return ' '
    if tag == UNK:
        return '?'
    inner = ''.join(plain_text(k, greek) for k in node.kids)
    if tag == 'grk' and greek:
        return greek_from_translit(inner)
    return inner


def collapse_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


# Fields that upstream sometimes leaves as bare punctuation ("<def>.</def>")
_PUNCT_ONLY_TAGS = {'def', 'cd', 'note', 'usage', 'syn', 'ety', 'mark', 'pos', 'q'}
_KEEP_EMPTY = VOID_TAGS | {'td', 'th', 'item', 'mitem', 'coltitle', 'row', 'str',
                           'headrow', 'colheads', 'table', 'mtable', 'matrix',
                           'matrix2x5'}
_NO_TIDY = {'pre', 'tt', 'song', 'text', 'table', 'mtable', 'matrix', 'matrix2x5'}
_LEAD_WS_PUNCT_RE = re.compile(r'^\s+(?=[,;:](?:\s|$))')
_INNER_WS_PUNCT_RE = re.compile(r'(?<=[^\W_)\]])\s+(?=[,;](?:\s|$))')


def _has_void(node: El) -> bool:
    return any(k.tag in VOID_TAGS for k in iter_nodes(node) if k is not node)


def tidy(node: El, ws: bool = True):
    """Clean upstream noise that renders as stray punctuation:
    placeholder pronunciations "(?)", empty or punctuation-only fields
    ("<def>.</def>"), and the space a removed field leaves before a comma
    or semicolon ("Abacuses (?); L. pl." -> "Abacuses; L. pl."). Only
    letterless content is removed, so the letter check still holds."""
    ws = ws and node.tag not in _NO_TIDY
    kids = []
    after_pr = dropped_pr = False
    for k in node.kids:
        if isinstance(k, str) and after_pr:
            k = re.sub(r'^\(#\)', '', k)      # "<pr>(<?/)</pr>(#)" typo
        if isinstance(k, str) and dropped_pr and ws and re.match(r'\s*[.,;:)]', k):
            # "Vocabularies (?)." -> "Vocabularies."
            k = k.lstrip()
            if kids and isinstance(kids[-1], str):
                kids[-1] = kids[-1].rstrip()
        after_pr = isinstance(k, El) and k.tag == 'pr'
        dropped_pr = False
        if isinstance(k, El):
            if k.tag.startswith('/') or k.tag in _KEEP_EMPTY:
                kids.append(k)
                continue
            if k.tag == 'pr' and pr_is_placeholder(k):
                dropped_pr = True
                continue
            t = plain_text(k)
            if not t.strip() and not _has_void(k):
                continue
            if k.tag in _PUNCT_ONLY_TAGS and not re.sub(r'[\s.,;:]', '', t) \
                    and not _has_void(k):
                continue
            tidy(k, ws)
            kids.append(k)
        elif kids and isinstance(kids[-1], str):
            kids[-1] += k
        else:
            kids.append(k)
    if ws:
        for i, k in enumerate(kids):
            if isinstance(k, str):
                if i and isinstance(kids[i - 1], El) and kids[i - 1].tag != BR:
                    k = _LEAD_WS_PUNCT_RE.sub('', k)
                kids[i] = _INNER_WS_PUNCT_RE.sub('', k)
    node.kids = [k for k in kids if not (isinstance(k, str) and not k)]


# ══════════════════════════════════════════════════════════════
# 4. Greek transliteration (webfont.txt, "Greek transliteration")
# ══════════════════════════════════════════════════════════════

_GRK_LETTERS = {
    'a': 'α', 'b': 'β', 'g': 'γ', 'd': 'δ', 'e': 'ε', 'z': 'ζ', 'h': 'η',
    'q': 'θ', 'i': 'ι', 'k': 'κ', 'l': 'λ', 'm': 'μ', 'n': 'ν', 'x': 'ξ',
    'o': 'ο', 'p': 'π', 'r': 'ρ', 's': 'σ', 't': 'τ', 'y': 'υ', 'u': 'υ',
    'f': 'φ', 'w': 'ω',
}
_GRK_VOWELS = set('aehiouwyAEHIOUWYrR')       # take a breathing
_GRK_IOTA_SUB = set('ahwAHW')
_GRK_ACCENT = {'`': '́', '~': '̀', '^': '͂'}
_GRK_BREATHING = {"'": '̓', '"': '̔'}
_GREEK_CHAR_RE = re.compile(r'[Ͱ-Ͽἀ-῿]')


def greek_from_translit(s: str) -> str:
    """'archai:`zein -> ἀρχαῒζειν, zw^,on -> ζῷον, "ydra`rgyros -> ὑδράργυρος."""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        breathing = ''
        if c in _GRK_BREATHING and i + 1 < n and s[i + 1] in _GRK_VOWELS:
            breathing = _GRK_BREATHING[c]
            i += 1
            c = s[i]
        low = c.lower()
        if low == 'c' and i + 1 < n and s[i + 1].lower() == 'h':
            base, i = ('Χ' if c.isupper() else 'χ'), i + 2
        elif low == 'p' and i + 1 < n and s[i + 1].lower() == 's':
            base, i = ('Ψ' if c.isupper() else 'ψ'), i + 2
        elif low in _GRK_LETTERS:
            base = _GRK_LETTERS[low]
            base = base.upper() if c.isupper() else base
            i += 1
        else:
            out.append('’' if c == "'" else c)
            i += 1
            continue
        diaeresis = accent = iota = ''
        while i < n:
            m = s[i]
            if m == ':' and base.lower() in 'ιυ':
                diaeresis = '̈'
            elif m in _GRK_ACCENT and not accent:
                accent = _GRK_ACCENT[m]
            elif m == ',' and c in _GRK_IOTA_SUB:
                iota = 'ͅ'
            else:
                break
            i += 1
        out.append(unicodedata.normalize('NFC', base + diaeresis + breathing + accent + iota))
    text = ''.join(out)
    # final sigma
    return re.sub(r'σ(?![Ͱ-Ͽἀ-῿̀-ͯ])', 'ς', text)


# ══════════════════════════════════════════════════════════════
# 5. Headword text, keys, folding
# ══════════════════════════════════════════════════════════════

# Stress and syllable marks in <hw>, <wf>, <conjf>, ... and <pr>
# (tagset.txt, pronunc.txt): " heavy accent, ` light accent, * syllable
# break; an accent mark FOLLOWS its syllable and then also ends it.
# They are shown the way current dictionaries do: ˈ (primary) and ˌ
# (secondary) BEFORE the stressed syllable, · between syllables:
#   Ab`a*lo"ne -> ˌAb·a·ˈlo·ne    (ȧ*băn"dŭn) -> (ȧ·ˈbăn·dŭn)
# Rendering keeps the marks as private-use placeholders until the whole
# field is rendered, because a syllable can span several text nodes
# (italic letters in pronunciations), then place_stress() moves them.
_P1, _P2, _SY = '\ue001', '\ue002', '\ue003'
_STRESS_DISPLAY = str.maketrans({'"': _P1, '`': _P2, '*': _SY})
_STRESS_STRIP = str.maketrans('', '', '"`*')
_PLACEHOLDER_RE = re.compile('[\ue001-\ue003]')
# characters that end a syllable run (besides the marks themselves).
# "?" is not one: it stands for a letter the typists could not read
# ('(k?-r?"jent)' -> '(k?-ˈr?·jent)').
_SYL_BOUNDARY = set(' \t\n -‐–—/()[]{},;:.+=&!|') | {'·'}
# Upstream typos: two marks in a row ('Aer"*o', 'In*"fra', 'A*raise""').
# An accent mark already ends its syllable, so a run means one mark: the
# accent if there is one, else a syllable break.
_MARK_RUN_RE = re.compile(r'[*"`]{2,}')


def _collapse_marks(m) -> str:
    run = m.group(0)
    return '"' if '"' in run else '`' if '`' in run else '*'


def place_stress(html: str) -> str:
    """Turn placeholder stress/syllable marks in rendered HTML into
    ˈ/ˌ before the stressed syllable and · between syllables."""
    if not _PLACEHOLDER_RE.search(html):
        return html
    out: list[str] = []
    start = 0                      # index in out where the syllable began
    i, n = 0, len(html)

    def next_visible(j):
        while j < n:
            if html[j] == '<':
                j = html.find('>', j) + 1 or n
                continue
            return html[j]
        return ''

    while i < n:
        c = html[i]
        if c == '<':
            j = html.find('>', i) + 1 or n
            tag = html[i:j]
            out.append(tag)
            if tag.startswith('</') and start == len(out) - 1:
                start = len(out)   # a closing tag right after a boundary
            i = j
            continue
        if c == '&':               # character reference: one visible char
            j = html.find(';', i)
            j = j + 1 if 0 < j - i <= 8 else i + 1
            out.append(html[i:j])
            i = j
            continue
        if c in (_P1, _P2):
            out.insert(start, 'ˈ' if c == _P1 else 'ˌ')
            nxt = next_visible(i + 1)
            if nxt and (nxt.isalnum() or nxt in "'’?" or nxt in (_P1, _P2)):
                out.append('·')
            start = len(out)
        elif c == _SY:
            out.append('·')
            start = len(out)
        else:
            out.append(c)
            if c in _SYL_BOUNDARY or c.isspace():
                start = len(out)
        i += 1
    return ''.join(out)


def stress_plain(s: str) -> str:
    """place_stress() for a marked-up word in plain text."""
    return place_stress(_MARK_RUN_RE.sub(_collapse_marks, s).translate(_STRESS_DISPLAY))


_FOLD_SPECIAL = str.maketrans({
    'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ß': 'ss', 'ø': 'o', 'Ø': 'O',
    'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ł': 'l', 'Ł': 'L', 'ı': 'i',
    '‖': '', '’': "'", '‘': "'", 'ȝ': 'y',
})


def fold_ascii(s: str) -> str:
    """café -> cafe, Aërobic -> Aerobic, Æsthetics -> AEsthetics."""
    s = s.translate(_FOLD_SPECIAL)
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c))


# In headword-like fields "/" between letters is a typo for "*"
# ("Tra"cer/y", "Gair"ish/ness", "Trace"a/bly"); a final "/" too ("Ad"der fly/").
_SLASH_TYPO_RE = re.compile(r'(?<=[^\W\d_])/(?=[^\W\d_])|(?<=[^\W\d_])/$')


def clean_form(s: str) -> str:
    """Headword-like text without stress/syllable marks and ‖."""
    s = _SLASH_TYPO_RE.sub('', s.replace('‖', '').translate(_STRESS_STRIP))
    return collapse_ws(s)


def _squash(s: str) -> str:
    return re.sub(r"[\s'/,]", '', fold_ascii(s)).casefold()


def _unspaced(s: str) -> str:
    return re.sub(r'[\s-]', '', s).casefold()


# Upstream <ent> typos that the headword repeats, so they cannot be
# corrected by comparing the two: a stress mark typed as "'" or "/".
TYPO_KEYS = {
    "Alectryom'achy": 'Alectryomachy', "Disperson'ate": 'Dispersonate',
    "Fres'nel' lan'tern": 'Fresnel lantern', "Fri'ableness": 'Friableness',
    "Fron'tated": 'Frontated', "Fruit'y": 'Fruity',
    "Spermatozo'on": 'Spermatozoon', "Xyla'riaceae": 'Xylariaceae',
    'Tracer/y': 'Tracery', 'Gairish/ness': 'Gairishness',
    'Accountable ness': 'Accountableness', 'Cockyoly, bird': 'Cockyoly bird',
    "Friseur'": 'Friseur', "Friskily'": 'Friskily', "Guelderrose'": 'Guelderrose',
    "I' ll": "I'll", "Prominent' moth": 'Prominent moth',
    # an <ent> typed wrong next to a correct headword
    'Lacinulaa': 'Lacinulae', 'Laminaa': 'Laminae', 'Microzo0spore': 'Microzoospore',
    'hyperegol': 'hypergol', 'hyperegolic': 'hypergolic',
}

# The same typos as they are displayed (raw source text, before the stress
# marks are placed): "'" typed for a stress mark, a stray space or comma.
DISPLAY_TYPOS = {
    "Fruit'y": 'Fruit"y', 'per"son\'*ate': 'per"son*ate',
    "Fres'nel' lan'tern": 'Fres"nel" lan"tern', "om'a*chy": 'om"a*chy',
    'count"a*ble ness': 'count"a*ble*ness', "Xy*la'ri": 'Xy*la`ri',
    'ol"y, bird': 'ol"y bird', "Fron'ta*ted": 'Fron"ta*ted',
    'Fri"seur\'': 'Fri"seur', 'Frisk"i*ly\'': 'Frisk"i*ly', "I' ll": "I'll",
    "Prominent' moth": 'Prominent moth', "Fri'a*ble*ness": 'Fri"a*ble*ness',
    'Guel"der*rose\'': 'Guel"der*rose`',
    # literal asterisks, not syllable marks (kept as LITERAL_STAR until
    # the article is finished)
    'M*A*S*H': 'M\ue010A\ue010S\ue010H',
}
LITERAL_STAR = '\ue010'
_DISPLAY_TYPO_RE = re.compile('|'.join(map(re.escape, DISPLAY_TYPOS)))


def fix_display_typos(s: str) -> str:
    return _DISPLAY_TYPO_RE.sub(lambda m: DISPLAY_TYPOS[m.group(0)], s)


def make_key(ent_text: str, hw_text: str = '') -> str:
    """Key from an <ent> (ASCII by upstream convention, sometimes with
    entities or typos) checked against its headword.

    Stress marks and ‖ are removed; whitespace collapsed. Where the <ent>
    carries a typo that the headword does not, the headword's form is
    used; TYPO_KEYS corrects the typos both share.
    """
    key = clean_form(ent_text)
    key = re.sub(r'\s*/\s*$', '', key)          # "Adder fly/"
    if key in TYPO_KEYS:
        return TYPO_KEYS[key]
    if hw_text:
        hw = clean_form(hw_text)
        if (hw and fold_ascii(key).casefold() != fold_ascii(hw).casefold()
                and _squash(key) == _squash(hw)
                and re.search(r"[/',]|\s", key)):
            key = hw
    return key


def has_letters(s: str) -> bool:
    return any(c.isalnum() for c in s)


# ══════════════════════════════════════════════════════════════
# 6. Corpus model
# ══════════════════════════════════════════════════════════════

TRANSPARENT = {'pre', 'tt'}      # looked through when finding headwords
HEADER_UNITS = {'hw', 'mhw'}


class Para:
    __slots__ = ('tree', 'letter', 'idx', 'header', 'body', 'role')

    def __init__(self, tree, letter, idx):
        self.tree = tree
        self.letter = letter
        self.idx = idx
        self.header = []     # header nodes (hw, pr, pos, ety, ...) of a head paragraph
        self.body = tree.kids
        self.role = 'cont'


class Group:
    """One homograph: a head paragraph and its continuation paragraphs."""
    __slots__ = ('key', 'alt_keys', 'paras', 'hw_nodes', 'parent_key',
                 'hw_forms', 'from_list')

    def __init__(self, key):
        self.key = key
        self.alt_keys = []
        self.paras = []
        self.hw_nodes = []
        self.parent_key = None   # list items: the article the list belongs to
        self.hw_forms = []       # clean headword forms (for redirects)
        self.from_list = False


def top_level_units(nodes):
    """Indices of top-level header units (hw / mhw), looking into pre/tt."""
    flat = []
    for k in nodes:
        if isinstance(k, El) and k.tag in TRANSPARENT and any(
                isinstance(x, El) and x.tag in HEADER_UNITS for x in k.kids):
            flat.extend(k.kids)
        else:
            flat.append(k)
    return flat, [i for i, k in enumerate(flat) if isinstance(k, El) and k.tag in HEADER_UNITS]


def take_ents(root: El) -> list[str]:
    """Remove every <ent> from the tree; return their texts."""
    ents = []

    def walk(node):
        keep = []
        for k in node.kids:
            if isinstance(k, El) and k.tag == 'ent':
                ents.append(collapse_ws(plain_text(k, greek=False)))
            else:
                if isinstance(k, El):
                    walk(k)
                keep.append(k)
        node.kids = keep
    walk(root)
    return ents


def hw_text_of(unit: El) -> list[str]:
    """Raw headword texts of a header unit (one per <hw>)."""
    if unit.tag == 'hw':
        return [collapse_ws(plain_text(unit))]
    return [collapse_ws(plain_text(k)) for k in unit.kids
            if isinstance(k, El) and k.tag == 'hw']


# Header fields pulled out of a head paragraph, in source order
HEADER_TAGS = {'hw', 'mhw', 'pr', 'pos', 'ety', 'plu', 'vmorph', 'amorph',
               'nmorph', 'altsp', 'sing'}


def split_header(nodes):
    """Leading header fields of a head paragraph -> (header, body)."""
    header = []
    i = 0
    while i < len(nodes):
        k = nodes[i]
        if isinstance(k, str):
            if has_letters(k):
                break
            if '‖' in k:
                header.append('‖')   # "\'d8<hw>Carte</hw>": not naturalized
        elif k.tag == BR:
            pass
        elif k.tag in HEADER_TAGS:
            header.append(k)
        else:
            break
        i += 1
    return header, nodes[i:]


class Corpus:
    def __init__(self):
        self.groups: list[Group] = []
        self.stats = Counter()
        self.dropped_ents: list[str] = []

    def load(self, data_dir: Path):
        for letter in LETTERS:
            path = data_dir / f'CIDE.{letter}'
            if not path.exists():
                raise SystemExit(f'missing source file {path}')
            paras = split_paragraphs(apply_source_fixes(letter, read_source(path)))
            self._load_file(letter, paras)
            print(f'  CIDE.{letter}: {len(paras):,} paragraphs')

    def _new_group(self, key, alt=(), para=None):
        g = Group(key)
        g.alt_keys = [a for a in alt if a]
        if para is not None:
            g.paras.append(para)
        self.groups.append(g)
        return g

    def _drop_pending(self, ents):
        """<ent>s left by an entry that upstream commented out
        ("<p><ent>shoo-in</ent><br/ <-- <hw>shoo-in</hw> ... --></p>"):
        the draft is not dictionary text (GNU Dico, the reference GCIDE
        server, does not show it either), so the word gets no article; a
        few get a redirect to the entry the draft points to
        (DRAFT_REDIRECTS)."""
        for e in ents:
            self.stats['commented_out_ents'] += 1
            self.dropped_ents.append(e)

    def _load_file(self, letter, raw_paras):
        current = None
        pending_ents: list[str] = []
        for idx, raw in enumerate(raw_paras):
            tree = parse_markup(raw)
            ents = take_ents(tree)
            tidy(tree)
            # "<ent>. . .</ent>" (an editorial note in Vocabulary) is no entry
            dropped = [e for e in ents if not has_letters(e)]
            ents = [e for e in ents if has_letters(e)]
            self.stats['ents_without_letters'] += len(dropped)
            if ents and not has_letters(plain_text(tree)):
                # An <ent> typed in its own <p> belongs to the next
                # paragraph when that paragraph has its headword
                # ("Condorcet's method"). Otherwise it is all that is left
                # of a commented-out draft (shoo-in, worthwhile) or of an
                # editors' note (Salt), and the next paragraph is another
                # entry or continues the current one.
                pending_ents = pending_ents + ents
                continue
            flat, units = top_level_units(tree.kids)
            if pending_ents:
                hws = {_squash(TYPO_KEYS.get(clean_form(h), clean_form(h)))
                       for u in units for h in hw_text_of(flat[u])}
                if any(_squash(make_key(e)) in hws for e in pending_ents):
                    ents = pending_ents + ents
                    self.stats['carried_ents'] += len(pending_ents)
                else:
                    self._drop_pending(pending_ents)
                pending_ents = []
            para = Para(tree, letter, idx)

            if len(units) >= 2 and self._is_list(flat, units):
                current = self._load_list(para, ents, flat, units, current)
                continue
            if current is None and not ents and not units:
                # letter banners ("<centered>B.</centered>") and the motto
                self.stats['banner_paragraphs'] += 1
                continue

            if ents or units:
                hw_texts = [h for u in units for h in hw_text_of(flat[u])]
                if ents:
                    keys = [make_key(e, self._match_hw(e, hw_texts)) for e in ents]
                    # An <ent> spaced or hyphenated otherwise than its
                    # headword ("Snap shot" / Snap"shot`) is a spelling of
                    # its own: keep it as a key as well.
                    keys += [c for c in map(clean_form, ents)
                             if c not in TYPO_KEYS and c not in keys
                             and re.search(r'[\s-]', c) and _unspaced(c) in
                             {_unspaced(k) for k in keys}]
                else:
                    keys = [make_key(hw_texts[0])] if hw_texts else []
                    self.stats['hw_without_ent'] += 1
                keys = [k for k in keys if k]
                # The article key is the <ent> of the headword: upstream
                # sometimes lists first a word that only the body defines
                # ("Admittedly" before "Admitted", in a <def2>).
                hw_sq = {_squash(TYPO_KEYS.get(clean_form(h), clean_form(h))) for h in hw_texts}
                first = next((i for i, k in enumerate(keys) if _squash(k) in hw_sq), 0)
                if first:
                    keys.insert(0, keys.pop(first))
                    self.stats['ent_reordered'] += 1
                if not keys:
                    if current:
                        current.paras.append(para)
                    continue
                para.role = 'head'
                para.header, para.body = split_header(flat)
                g = self._new_group(keys[0], keys[1:], para)
                g.hw_forms = [TYPO_KEYS.get(clean_form(h), clean_form(h)) for h in hw_texts]
                current = g
            elif current is not None:
                current.paras.append(para)
            else:
                self.stats['orphan_paragraphs'] += 1
                print(f'  WARNING: CIDE.{letter} paragraph {idx} has no entry: '
                      f'{collapse_ws(plain_text(tree))[:80]!r}')
        self._drop_pending(pending_ents)

    @staticmethod
    def _is_list(flat, units) -> bool:
        """Several headwords, each with a definition of its own (the
        "Un-" lists, Kiteflying/Kiteflier). Several spellings sharing one
        definition (yoghurt, yoghourt, yogurt) are one entry, and so are
        forms whose "definition" is only a part of speech (Ronne and
        Ronnen, "<def><pos>obs. imp. pl.</pos></def>" ... "of Run")."""
        def real_def(x):
            return x.tag == 'def' and has_letters(''.join(
                plain_text(k) for k in x.kids if not (isinstance(k, El) and k.tag == 'pos')))

        bounds = units + [len(flat)]
        with_def = 0
        for a, b in zip(bounds, bounds[1:]):
            if any(isinstance(k, El) and any(real_def(x) for x in iter_nodes(k))
                   for k in flat[a + 1:b]):
                with_def += 1
        return with_def >= 2

    @staticmethod
    def _match_hw(ent, hw_texts):
        if not hw_texts:
            return ''
        fe = _squash(ent)
        for h in hw_texts:
            if _squash(clean_form(h)) == fe:
                return h
        return hw_texts[0] if len(hw_texts) == 1 else ''

    # A list this long is a list of examples of the article it belongs to
    # (the "Un-" lists); a short one (Kiteflying/Kiteflier) is an entry of
    # its own that happens to define two words.
    LIST_OWNED_MIN = 10

    @staticmethod
    def _split_list_tail(seg):
        """Strip the "--" separator that ends a list item ("...kiting.</def>
        -- <hw>Kite"fli`er</hw>"); return (seg, trailer) where trailer is
        the text that closes the whole list ("<br/ ----- and the like.")."""
        seg = list(seg)
        j = len(seg) - 1
        while j >= 0 and _is_ws(seg[j]):
            j -= 1
        trailer = ''
        if j >= 0 and isinstance(seg[j], str):
            m = re.fullmatch(r'(.*?)\s*-{2,}\s*(.*?)\s*', seg[j], re.S)
            if m and not has_letters(m.group(1)) and has_letters(m.group(2)) \
                    and j > 0 and _is_ws(seg[j - 1]):
                trailer = m.group(2)                       # "and the like."
                seg[j] = ''
            elif m and not m.group(2):
                seg[j] = m.group(1)                        # "... -- " item separator
        return [k for k in seg if k != ''], trailer

    def _load_list(self, para, ents, flat, units, current):
        """A paragraph listing several headwords, each with its own short
        definition (the "Un-" lists): one article per listed word. The
        article the list belongs to keeps the list itself, as links."""
        self.stats['list_paragraphs'] += 1
        owner = current if len(units) >= self.LIST_OWNED_MIN else None
        intro = flat[:units[0]]
        if intro and has_letters(''.join(plain_text(k) for k in intro)):
            ip = Para(El('#root', kids=intro), para.letter, para.idx)
            if owner is not None:
                owner.paras.append(ip)
            else:
                self.stats['orphan_list_intro'] += 1
        by_form = {}
        for e in ents:
            by_form.setdefault(_squash(e), e)
        bounds = units + [len(flat)]
        listed = []
        trailer = ''
        for a, b in zip(bounds, bounds[1:]):
            seg, tr = self._split_list_tail(flat[a:b])
            trailer = trailer or tr
            hw_texts = hw_text_of(seg[0])
            ent = by_form.pop(_squash(clean_form(hw_texts[0])), None) if hw_texts else None
            key = make_key(ent, hw_texts[0]) if ent else make_key(hw_texts[0]) if hw_texts else ''
            if not key:
                continue
            sp = Para(El('#root', kids=seg), para.letter, para.idx)
            sp.role = 'head'
            sp.header, sp.body = split_header(seg)
            g = self._new_group(key, (), sp)
            g.hw_forms = [TYPO_KEYS.get(clean_form(h), clean_form(h)) for h in hw_texts]
            g.from_list = True
            g.parent_key = owner.key if owner is not None else None
            listed.append(clean_form(hw_texts[0]))
            self.stats['list_items'] += 1
        if owner is not None:
            # "(a) To adjectives ...; as, --" is followed by the list itself
            kids = []
            for i, w in enumerate(listed):
                kids += ([', '] if i else []) + [El('er', kids=[w])]
            if trailer:
                kids.append(f' — {trailer}')
            owner.paras.append(Para(El('#root', kids=kids), para.letter, para.idx))
        elif trailer:                    # keep it with the last entry
            last = self.groups[-1].paras[-1]
            last.tree.kids.append(f' — {trailer}')
            if last.body is not last.tree.kids:
                last.body.append(f' — {trailer}')
        # <ent>s without a matching item: alternative keys of the owner
        if by_form:
            self.stats['list_unmatched_ents'] += len(by_form)
            if owner is not None:
                owner.alt_keys.extend(make_key(e) for e in by_form.values())
        # Paragraphs after the list continue the article the list belongs
        # to, or the last entry of a short list
        return owner if owner is not None else self.groups[-1]


# ══════════════════════════════════════════════════════════════
# 7. Keys and redirects
# ══════════════════════════════════════════════════════════════

# Fields whose text is another form of the headword (tagset.txt): alternate
# spellings, inflections ("should be indexed"), derived word forms.
FORM_TAGS = {'asp', 'conjf', 'plw', 'adjf', 'decf', 'singw', 'wf'}
_FORM_OK_RE = re.compile(r"[^\W\d_][^\W_]*(?:['\-. ][^\W_]+)*\.?")

_COL_LEAD_RE = re.compile(r'^to\s+(?=\S)', re.I)
_COL_OK_RE = re.compile(r"[^\W\d_][\w'\-.,!? ]*")


def form_variants(text: str) -> list[str]:
    """Usable lookup forms of a <conjf>/<asp>/... field, or []."""
    t = clean_form(text).strip(' ,;:')
    out = []
    for part in re.split(r'\s*(?:,|;|\bor\b|\band\b|&)\s*', t):
        part = part.strip(' .') if part.endswith('.') and not re.search(r'\.\w', part) else part.strip()
        part = re.sub(r'\s+\d+$', '', part)      # "Forewent 2" (homograph number)
        part = TYPO_KEYS.get(part, part)
        if not part or len(part) > 50 or len(part.split()) > 4:
            continue
        if _FORM_OK_RE.fullmatch(part):
            out.append(part)
    return out


_SUFFIX_HYPHEN_RE = re.compile(r'(?<![-‐])[-‐]$')


def iter_forms(node: El):
    """FORM_TAGS fields of a subtree, except suffixes written after a
    hyphen ("Keelman ... pl. -<plw>men</plw>"): those are no words."""
    prev = None
    for k in node.kids:
        if isinstance(k, El):
            if k.tag in FORM_TAGS:
                # "-<plw>men</plw>" (lowercase, right after a hyphen); not
                # "-- <wf>Awk"ward*ness</wf>" or "-<wf>Faith"ful*ness</wf>"
                if not (isinstance(prev, str) and _SUFFIX_HYPHEN_RE.search(prev)
                        and plain_text(k)[:1].islower()):
                    yield k
            else:
                yield from iter_forms(k)
        prev = k


# "To" that is a preposition, not the mark of an infinitive: "To and fro",
# "To the contrary", "To a T", "To one's beard", "To no effect"
_TO_PREP_NEXT = frozenset("""
    a an the and no all his her my thy its our your their one one's this that
    these those some any every each it me him us them nothing none both such
    what which whom fro
""".split())


def collocation_key(text: str, whole: bool = True) -> str:
    """'To let alone' -> 'let alone'; '' if unusable as a key.
    Placeholders are dropped: 'To see (one) through' -> 'see through',
    'to take a flight{9}' -> 'take a flight'.

    "To" stays where it is a preposition ('to and fro', 'to a T') and
    where only one word follows ('to boot', 'to wit', 'to blame'): such a
    phrase is not the verb. In a <col> that is only part of an item
    ("To eat, To eat in or To eat into"; "To be, go, or run, on all
    fours"), whole=False, a single word after "To" is the verb itself."""
    t = re.sub(r'\{[^{}]*\}', '', text)
    t = re.sub(r'\s*[(\[][^()\[\]]*[)\]]\s*', ' ', t)
    t = clean_form(t).strip(' ,;:.')
    t = TYPO_KEYS.get(t, t)
    m = _COL_LEAD_RE.match(t)
    if m:
        rest = t[m.end():]
        words = rest.split()
        if words[0].casefold().rstrip(',;:') in _TO_PREP_NEXT or (whole and len(words) == 1):
            t = 'to ' + rest
        else:
            t = rest
    if (not t or len(t) > 60 or len(t.split()) > 8 or not has_letters(t)
            or not _COL_OK_RE.fullmatch(t) or t.casefold().endswith(' etc')):
        return ''
    return t


# Hyphenated headwords that are written solid today ("Week-end" ->
# weekend, "To-day" -> today, "Good-bye" -> goodbye), where the solid
# form is no headword of its own. Selected once by checking each
# hyphenated GCIDE key whose solid form is not a key against the
# lemmas of Open English WordNet; kept here so the build does not
# depend on another data set.
SOLID_COMPOUNDS = frozenset(k.casefold() for k in """
    A-sea Aard-wolf Absent-minded Addle-pated After-glow After-image
    Air-tight Alto-cumulus Ant-eater Ant-hill Ant-lion Anti-Semitism
    Anti-trade Atharva-Veda Awe-stricken Awe-struck Black-jack Blue-bonnet
    Broken-hearted Broncho-pneumonia Butter-scotch By-law By-pass By-past
    By-product Cerebro-spinal Chick-pea Cirro-cumulus Cirro-stratus
    Clear-headed Cloud-burst Co-respondent Cross-tie Cut-off Cut-out
    Dare-devil Dare-deviltry Dead-eye Demi-tasse Dessert-spoonful
    Diamond-back Dumb-waiter E-la Ear-splitting Earth-tongue Egg-cup
    Electro-chemical Electro-chemistry Electro-dynamometer Electro-magnet
    Electro-magnetic Electro-magnetism Electro-motive Electro-positive
    En-lil Eye-spot Faint-hearted Fan-tan Fantastic-alness Fashion-monger
    Fault-finder Fault-finding Feather-edge Feather-edged Feather-foil
    Feeble-minded Fenes-tella Ferro-concrete First-hand Flat-footed
    Free-hand Free-handed Fresh-water Frost-bitten Galacta-gogue Golden-eye
    Good-bye Good-year Great-hearted Half-tone Hall-mark Hard-fisted
    Hard-hearted Head-cheese Head-hunter Hell-cat Hemi-demi-semiquaver
    Horse-leech Horse-radish Hot-head Hydro-electric Ibero-mesornis Kill-joy
    Kind-hearted Kind-heartedness Kohl-rabi Labor-saving Lamp-post Let-up
    Life-saving Light-headed Light-hearted Lion-hearted Lock-down
    Long-sighted Long-sightedness Love-making Love-sick Love-sickness
    Maxillo-mandibular Mealy-mouthed Medico-legal Mince-meat Money-maker
    Money-making Moon-eye News-writer Nitro-chloroform Non-feasance
    Nut-brown On-looker Open-handed Open-hearted Open-mouthed Out-patient
    Over-arm Over-busy Over-garment Pass-key Photo-electric Photo-electrical
    Photo-electricity Photo-engraving Pig-headed Pig-sticking Plain-spoken
    Plano-concave Plano-convex Poor-will Post-mortem Pot-bellied Pot-belly
    Printa-ble Prompt-book Pseudo-bulb Pseudo-peripteral Psycho-motor
    Pudding-headed Put-off Quarry-man Quarter-deck Rat-tail Rattle-brained
    Rattle-pated Reed-mace Rest-harrow Rig-Veda Round-up Ruta-baga
    Safe-keeping Saw-wort Scatter-brain Scatter-brained School-teacher
    Serio-comical Set-off Sheep-shearing Shilly-shally Side-slip
    Sight-seeing Sight-seer Snow-bound Soft-headed Soft-hearted Sol-fa
    Sound-board Spatter-dock Stand-by Start-up Stern-wheeler Stop-gap
    Story-teller Stout-hearted Strait-jacket Strait-laced Stud-horse
    Sub-base Sun-dried Sun-struck Supple-jack Sure-footed Sway-backed
    Sweet-sop Table-land Ten-pounder Tender-hearted Tenter-hook Tether-ball
    Text-book Thick-headed Three-score Tilt-yard Time-table To-day Top-dress
    Tow-head Trade-mark Tragi-comedy Tragi-comical Trencher-man Triple-tail
    True-hearted Turn-buckle Turn-out Under-arm Under-garment Vice-regal
    Walk-over Wall-eye Wall-eyed Warm-hearted Water-colorist Water-logged
    Water-tight Week-end Well-being Well-born Whip-poor-will White-face
    White-water Whitlow-wort Wild-cat Willow-herb Wind-break Wind-up
    Woad-waxen Woe-begone Wonder-working Wood-waxen World-wide Yajur-Veda
    all-over anti-aircraft baby-sit bell-bottom bull-necked call-back
    cold-heartedness counter-subversion day-star dilly-dally down-market
    earth-shaking egg-beater face-lift feeble-mindedness flat-bottomed
    follow-up gay-wings honky-tonk leaf-hopper long-standing loud-mouth
    low-set non-finite pak-choi part-time parti-colored space-time
    spread-eagle stick-tight tortoise-shell off-line
""".split())


def solid_form(key: str) -> str:
    """'Week-end' -> 'Weekend', 'Anti-Semitism' -> 'Antisemitism'."""
    head, _, rest = key.partition('-')
    return head + rest.replace('-', '').lower()


class Article:
    __slots__ = ('key', 'groups')

    def __init__(self, key):
        self.key = key
        self.groups = []


def build_index(corpus: Corpus):
    """Articles (one per key ignoring case) and redirects (src, target, kind)."""
    articles: dict[str, Article] = {}
    for g in corpus.groups:
        uni = g.key
        g.key = fold_ascii(uni)
        art = articles.get(g.key.casefold())
        if art is None:
            art = articles[g.key.casefold()] = Article(g.key)
        art.groups.append(g)
        g.key = art.key
        g.alt_keys = ([uni] if uni != g.key else []) + g.alt_keys
    redirects = []
    for g in corpus.groups:
        for k in g.alt_keys:
            redirects.append((k, g.key, 'ent'))
            if fold_ascii(k) != k:
                redirects.append((fold_ascii(k), g.key, 'ent'))
        for h in g.hw_forms:
            # every spelling shown as a headword (café, Theater, encyclopædia)
            if h:
                redirects.append((h, g.key, 'hw'))
                if fold_ascii(h) != h:
                    redirects.append((fold_ascii(h), g.key, 'hw'))
        if g.key.casefold() in SOLID_COMPOUNDS:
            redirects.append((solid_form(g.key), g.key, 'solid'))
        for p in g.paras:
            for n in iter_forms(p.tree):
                for f in form_variants(plain_text(n)):
                    redirects.append((f, g.key, 'form'))
                    if fold_ascii(f) != f:
                        redirects.append((fold_ascii(f), g.key, 'form'))
    return articles, redirects


# Redirect kinds. "ent" and "hw" are headwords of the target's own
# paragraphs; the others are derived (variant spellings, inflections,
# solid compounds, phrases named in running text).
DERIVED_KINDS = {'form', 'solid', 'col'}


def finalize_redirects(articles, redirects):
    """Drop self-redirects, junk and duplicates.

    A derived form that is a headword of its own ("but", written also for
    Butt; "made", an inflection of Make and a variant of Mad) gets no
    redirect: the word's own article is what a lookup of it should show,
    and it already says how the forms are related."""
    seen = set()
    out = []
    for src, target, kind in redirects:
        src = collapse_ws(src)
        if (not src or not has_letters(src) or len(src) > 80 or '<' in src
                or src.casefold() == target.casefold()):
            continue
        if target.casefold() not in articles:
            continue
        if kind in DERIVED_KINDS and src.casefold() in articles:
            continue
        sig = (src.casefold(), target.casefold())
        if sig in seen:
            continue
        seen.add(sig)
        out.append((src, target, kind))
    return out


# ══════════════════════════════════════════════════════════════
# 8. HTML rendering
# ══════════════════════════════════════════════════════════════

FLAG_EN = '\U0001F1EC\U0001F1E7\U0001F1FA\U0001F1F8'  # 🇬🇧🇺🇸
DIR_LABEL = f'{FLAG_EN} English'

_SEC, _SEC_D = TEXT_SECONDARY
_LINK, _LINK_D = TEXT_LINK
# CROSSREF is left out: its ".xr a" rule has the specificity of ".e a",
# which styles every link here, so with per-article style subsets (below)
# the winner would depend on the order of the articles on the page.
CSS = get_css(
    PRONUNCIATION, ETYMOLOGY, DEFINITION, SENSE_NUMBER, EXAMPLES, SYNONYMS,
    extra_light=(
        f'.e a{{color:{_LINK};text-decoration:none}}'
        '.gh{margin:2px 0 4px}'
        f'.mf{{font-size:0.9em;color:{_SEC}}}'
        '.sc{font-variant:small-caps}'
        f'.au{{font-style:italic;color:{_SEC}}}'
        '.ex-q i{font-style:normal}'
        '.nt{font-size:0.92em;margin:6px 0;padding:6px 10px;background:#f2f2f7;border-radius:6px}'
        '.cs{margin:8px 0 4px}.ci{margin:4px 0;padding-left:10px;text-indent:-10px}'
        '.cs .ex{text-indent:0}'
        f'.unk{{color:{_SEC};border:1px solid #c7c7cc;border-radius:3px;padding:0 2px;'
        'font-size:0.75em;font-style:normal}'
        '.pre{white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace;font-size:0.85em}'
        '.tb{margin:6px 0;font-size:0.9em}.tb .td{padding-right:10px}'
        f'.fig{{color:{_SEC}}}'
        '.syn.us{margin-top:6px}'
        f'.xr{{margin-top:4px;font-size:0.9em;color:{_SEC}}}'
    ),
    extra_dark=(
        f'.e a{{color:{_LINK_D}}}.mf,.au,.fig,.xr{{color:{_SEC_D}}}'
        '.nt{background:#2c2c2e}'
        f'.unk{{color:{_SEC_D};border-color:#48484a}}'
    ),
)


class StyleSubsetter:
    """Per-article <style>: only the rules whose classes the article uses.

    Every article carries its own style block (the app shows each record
    on its own), and with ~140k articles the full block was 13 MB of the
    compressed MDX. Rules come from one master sheet in its order, so the
    blocks of several articles shown together never conflict; the master
    has no two same-specificity rules that set the same property on the
    same element, so the order of the blocks does not matter either.
    """
    _RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}')
    _CLASS_ATTR_RE = re.compile(r'class="([^"]+)"')

    def __init__(self, css: str):
        body = css[len('<style>'):-len('</style>')]
        light, _, dark = body.partition('@media(prefers-color-scheme:dark){')
        if not dark.endswith('}'):
            raise ValueError('unexpected theme CSS layout')
        self.light = self._parse(light)
        self.dark = self._parse(dark[:-1])
        self.cache: dict[frozenset, str] = {}

    def _parse(self, text):
        rules = []
        pos = 0
        for m in self._RULE_RE.finditer(text):
            if text[pos:m.start()].strip():
                raise ValueError(f'unparsed CSS: {text[pos:m.start()]!r}')
            pos = m.end()
            sels = []
            for sel in m.group(1).split(','):
                sel = sel.strip()
                classes = frozenset(re.findall(r'\.([\w-]+)', sel))
                if not classes:
                    raise ValueError(f'selector without a class: {sel!r}')
                elems = frozenset(re.findall(r'(?:^|[\s>+~])([a-z][a-z0-9]*)\b', sel))
                sels.append((sel, classes, elems))
            rules.append((sels, m.group(2)))
        if text[pos:].strip():
            raise ValueError(f'unparsed CSS: {text[pos:]!r}')
        return rules

    @staticmethod
    def _pick(rules, used):
        out = []
        for sels, decl in rules:
            keep = [s for s, cl, el in sels if cl <= used and el <= used]
            if keep:
                out.append(f'{",".join(keep)}{{{decl}}}')
        return ''.join(out)

    def __call__(self, html: str) -> str:
        used = set()
        for m in self._CLASS_ATTR_RE.finditer(html):
            used.update(m.group(1).split())
        used.update(t for t in ('a', 'i', 'b') if f'<{t}' in html)
        used = frozenset(used)
        css = self.cache.get(used)
        if css is None:
            light, dark = self._pick(self.light, used), self._pick(self.dark, used)
            css = f'<style>{light}' + (
                f'@media(prefers-color-scheme:dark){{{dark}}}' if dark else '') + '</style>'
            self.cache[used] = css
        return css


STYLE = StyleSubsetter(CSS)
_NESTED_RE = re.compile(r'<(b|i)><\1>((?:(?!</?\1>).)*)</\1></\1>', re.S)
_EMPTY_INLINE_RE = re.compile(r'<(b|i|sup|sub)></\1>|<span class="(?:pos|mk|sc|au|df|mf)"></span>')


_TRAIL_WS_RE = re.compile(r'[ \t\n]+(</[bi]>)(?=[,;])')


def finish_html(body: str) -> str:
    """Collapse <b><b>x</b></b>, drop empty inline elements, and the space
    an italic run ends with before a comma ("<i>appendant </i>,")."""
    body = _TRAIL_WS_RE.sub(r'\1', body).replace(LITERAL_STAR, '*')
    while True:
        new = _EMPTY_INLINE_RE.sub('', _NESTED_RE.sub(r'<\1>\2</\1>', body))
        if new == body:
            return body
        body = new


def page(body_html: str) -> str:
    """A complete record: its style subset + the article."""
    body_html = finish_html(body_html)
    return STYLE(body_html) + body_html


ITALIC_TAGS = {
    'it', 'i', 'ex', 'xex', 'qex', 'uex', 'ets', 'etsep', 'spn', 'gen', 'fam',
    'ord', 'class', 'subclass', 'phylum', 'subphylum', 'kingdom', 'subord',
    'suborder', 'subfam', 'var', 'varn', 'booki', 'book', 'title', 'film', 'ship',
    'mathex', 'ratio', 'abbr', 'ant', 'contr', 'causes', 'causedby', 'cnvto',
    'compof', 'itran', 'itrans', 'tran', 'xlati', 'iref', 'figref', 'ptcl',
    'stype', 'styp', 'isa', 'part', 'parts', 'membof', 'memberof', 'member',
    'members', 'corr', 'inv', 'prod', 'prodmac', 'stage', 'methodfor', 'examp',
    'unit', 'uses', 'usedby', 'perf', 'recipr', 'sig', 'altname', 'altnpluf',
    'pluf', 'singf', 'fld', 'sd', 'song_title', 'qperson', 'qpers', 'hypen',
}
BOLD_TAGS = {'b', 'bold', 'col', 'colf', 'ct', 'stypec', 'hwf', 'h1', 'h2',
             'universbold', 'boldfacetype', 'headrow', 'coltitle', 'ttitle',
             'tabtitle', 'figtitle'}
SMALLCAP_TAGS = {'sc', 'conjf', 'plw', 'adjf', 'decf', 'singw'}
SUP_TAGS = {'sup', 'sups', 'supr', 'exp'}
SUB_TAGS = {'sub', 'subs'}
HW_LIKE = {'hw', 'wf', 'conjf', 'plw', 'adjf', 'decf', 'singw', 'asp', 'hwf'}
PRE_TAGS = {'pre', 'tt', 'song', 'text'}
TABLE_TAGS = {'table', 'mtable', 'matrix', 'matrix2x5'}
ROW_TAGS = {'row', 'str', 'headrow', 'colheads'}
CELL_TAGS = {'td', 'th', 'item', 'mitem', 'coltitle'}
BLOCK_TAGS = {'q', 'note', 'usage', 'syn', 'cs'} | TABLE_TAGS | {'pre', 'song'}

SYN_STOPWORDS = {'a', 'an', 'the', 'as', 'and', 'or', 'see', 'to', 'of', 'in',
                 'on', 'at', 'by', 'for', 'with', 'etc', 'also', 'not', 'be'}

_UNK_HTML = '<span class="unk">?</span>'
_RAW_HW_WORD_RE = re.compile(
    r'(?<![\w*"`])[^\W\d_]+(?:[*"`][^\W\d_]+)*\*[^\W\d_]+(?:[*"`][^\W\d_]+)*[*"`]?(?![\w*"`])')


def _raw_hw_word(m) -> str:
    w = m.group(0)
    # a marked-up word has lowercase letters ("Af*flict"ing*ly"), unlike
    # a name written with asterisks
    return stress_plain(w) if any(c.islower() for c in w) else w


_SENSE_REF_WORD_RE = re.compile(r'\bsense \{(\d{1,2}[a-z]?)\}')
_SENSE_REF_RE = re.compile(r' ?\{(\d{1,2}[a-z]?|n\d)\}')
_DASH_RE = re.compile(r'--+')
_WS_RE = re.compile(r'\s+')


class Ctx:
    """Rendering context flags (immutable; derive with .but())."""
    __slots__ = ('hw', 'pr', 'pre', 'quote', 'nested')

    def __init__(self, hw=False, pr=False, pre=False, quote=False, nested=False):
        self.hw, self.pr, self.pre, self.quote, self.nested = hw, pr, pre, quote, nested

    def but(self, **kw):
        c = Ctx(self.hw, self.pr, self.pre, self.quote, self.nested)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


TOP = Ctx()


def _is_ws(n) -> bool:
    return (isinstance(n, str) and not n.strip()) or (isinstance(n, El) and n.tag == BR)


def pr_is_placeholder(pr: El) -> bool:
    """'(?)', '()', '(#)', '(<?/)' and the like carry no pronunciation."""
    t = plain_text(pr)
    return not re.sub(r'[\s()?#,;.]', '', t)


def continues_cs(p: Para) -> bool:
    """Does the paragraph continue a collocation section of the previous
    <p>? It does when it closes a <cs> it did not open, and, where
    upstream left the tags out, when a continuation paragraph starts
    with a phrase ("<p>-- <col><b>Good Friday</b></col>, <cd>...")."""
    opened = {k.tag for k in iter_nodes(p.tree)}
    if 'cs' in opened:
        return False
    if any(k.tag == '/cs' for k in iter_nodes(p.tree)):
        return True
    if p.role != 'cont':
        return False
    body = [k for k in p.body if not _is_ws(k)]
    if body and isinstance(body[0], str) and re.fullmatch(r'\s*-{2,}\s*', body[0]):
        body = body[1:]
    return bool(body) and isinstance(body[0], El) and body[0].tag in ('col', 'mcol')


class Renderer:
    def __init__(self, keys: dict[str, str]):
        self.keys = keys            # casefold -> key, every key in the MDX
        self.stats = Counter()
        self.lost: list[int] = []   # letters lost per paragraph (build check)

    # ── links ────────────────────────────────────────────────
    def resolve(self, text: str) -> str | None:
        t = clean_form(re.sub(r'\{[^{}]*\}', '', text)).strip(' .,;:')
        if not t:
            return None
        for cand in (t, fold_ascii(t), re.sub(r'\s*\(.*?\)\s*', ' ', t).strip()):
            k = self.keys.get(cand.casefold())
            if k is not None:
                return k
        return None

    def resolve_phrase(self, text: str) -> str | None:
        """A <cref> target: the phrase key, or the words as written."""
        for cand in (collocation_key(text), collocation_key(text, whole=False), text):
            k = self.resolve(cand) if cand else None
            if k is not None:
                return k
        return None

    def link(self, target: str, label_html: str) -> str:
        self.stats['links'] += 1
        return f'<a href="entry://{esc_attr(target)}">{label_html}</a>'

    # ── text ─────────────────────────────────────────────────
    def text(self, s: str, ctx: Ctx) -> str:
        if ctx.pre:
            return esc(s.replace('\t', '    '))
        s = _WS_RE.sub(' ', s)
        s = _DASH_RE.sub('—', s)
        s = fix_display_typos(s)
        if ctx.hw:
            s = _SLASH_TYPO_RE.sub(lambda m: '*' if m.end() < len(m.string) else '', s)
        if ctx.hw or ctx.pr:
            return esc(_MARK_RUN_RE.sub(_collapse_marks, s).translate(_STRESS_DISPLAY))
        # a headword form typed into running text without <wf>:
        # "-- Af*flict"ing*ly, adv."
        s = _RAW_HW_WORD_RE.sub(_raw_hw_word, s)
        s = esc(s)
        # WordNet-era sense references: "same as activation{1}", "{1b}"
        s = _SENSE_REF_WORD_RE.sub(r'sense \1', s)
        return _SENSE_REF_RE.sub(r'<sup>\1</sup>', s)

    def kids(self, node: El, ctx: Ctx) -> str:
        return ''.join(self.inline(k, ctx) for k in node.kids)

    # ── inline content ───────────────────────────────────────
    def inline(self, n, ctx: Ctx) -> str:
        if isinstance(n, str):
            return self.text(n, ctx)
        tag = n.tag
        if tag in DROP_TAGS or tag.startswith('/'):
            return ''
        if tag == BR:
            if ctx.pre:
                return '\n'
            return '<br>' if ctx.nested else ' '
        if tag == UNK:
            return '?' if ctx.pr else _UNK_HTML
        sub = ctx.but(nested=True)
        if tag == 'grk':
            g = greek_from_translit(plain_text(n))
            return f'<span lang="grc">{esc(_WS_RE.sub(" ", g))}</span>'
        if tag == 'er':
            label = self.kids(n, sub)
            target = self.resolve(plain_text(n))
            if target:
                return self.link(target, label)
            self.stats['er_unresolved'] += 1
            return f'<span class="sc">{label}</span>'
        if tag == 'cref':
            label = self.kids(n, sub)
            target = self.resolve_phrase(plain_text(n))
            return self.link(target, f'<i>{label}</i>') if target else f'<i>{label}</i>'
        if tag in HW_LIKE:
            inner = place_stress(self.kids(n, sub.but(hw=True)))
            if tag in SMALLCAP_TAGS:
                return f'<span class="sc">{inner}</span>'
            if tag == 'asp':
                return f'<i>{inner}</i>'
            return f'<b>{inner}</b>'
        if tag == 'pr':
            if pr_is_placeholder(n):
                return ''
            return f'<span class="pri">{place_stress(self.kids(n, sub.but(pr=True)))}</span>'
        if tag == 'pos':
            return f'<span class="pos">{self.kids(n, sub)}</span>'
        if tag == 'mark':
            inner = self.kids(n, sub)
            t = collapse_ws(plain_text(n))
            if len(t) <= 40:
                m = re.fullmatch(r'\[\s*(.*?)\s*\]', inner.strip(), re.S)
                return f'<span class="mk">{m.group(1) if m else inner.strip()}</span>'
            return f'<i>{inner}</i>'
        if tag == 'sn':
            return f'<span class="sn-num">{self.kids(n, sub)}</span>'
        if tag in ('au', 'qau'):
            return f'<span class="au">{self.kids(n, sub)}</span>'
        if tag == 'def':
            return f'<span class="df">{self.kids(n, sub)}</span>'
        if tag == 'ety':
            return f'<span class="et">{self.kids(n, sub)}</span>'
        if tag in ('chform', 'chformi'):
            return self.formula(n, sub)
        if tag == 'q':
            return f'<i>{self.kids(n, sub.but(quote=True))}</i>'
        if tag == 'tr':
            if any(isinstance(k, El) and k.tag in CELL_TAGS | ROW_TAGS for k in n.kids):
                return self.kids(n, sub) + '<br>'
            return f'<i>{self.kids(n, sub)}</i>'
        if tag in ROW_TAGS:
            return self.kids(n, sub) + '<br>'
        if tag in CELL_TAGS:
            return f'<span class="td">{self.kids(n, sub)}</span> '
        if tag in TABLE_TAGS:
            return f'<span class="tb">{self.kids(n, sub.but(pre=False))}</span>'
        if tag in PRE_TAGS:
            return f'<span class="pre">{self.kids(n, sub.but(pre=True))}</span>'
        if tag in ('figure', 'illust', 'caption', 'figcap'):
            return f'<span class="fig">{self.kids(n, sub)}</span>'
        if tag in ITALIC_TAGS:
            return f'<i>{self.kids(n, sub)}</i>'
        if tag in BOLD_TAGS:
            return f'<b>{self.kids(n, sub)}</b>'
        if tag in SMALLCAP_TAGS:
            return f'<span class="sc">{self.kids(n, sub)}</span>'
        if tag in SUP_TAGS:
            return f'<sup>{self.kids(n, sub)}</sup>'
        if tag in SUB_TAGS:
            return f'<sub>{self.kids(n, sub)}</sub>'
        if tag in BLOCK_TAGS:            # block field nested inside inline text
            return self.kids(n, sub)
        return self.kids(n, sub)

    def formula(self, n: El, ctx: Ctx) -> str:
        """Chemical formula: digits after a letter or ')' are subscripts."""
        out = []
        for k in n.kids:
            if isinstance(k, str):
                s = esc(_WS_RE.sub(' ', k))
                out.append(re.sub(r'(?<=[A-Za-z)\]])(\d+)', r'<sub>\1</sub>', s))
            else:
                out.append(self.inline(k, ctx))
        return ''.join(out)

    # ── blocks ───────────────────────────────────────────────
    def quote_block(self, q: El, author_nodes) -> str:
        body = self.kids(q, TOP.but(nested=True, quote=True)).strip()
        body = re.sub(r'^(?:<br>\s*)+|(?:\s*<br>)+$', '', body)
        html = f'<div class="ex"><span class="ex-q">{body}</span>'
        if author_nodes:
            a = ''.join(self.inline(k, TOP.but(nested=True)) for k in author_nodes).strip()
            if a:
                html += f'<span class="ex-a">{a}</span>'
        return html + '</div>'

    def blocks(self, nodes, card_cls: str | None = 'sn', item_split=False) -> list[str]:
        """Render a run of nodes as blocks. Inline runs become cards
        (<div class="sn">) at paragraph level, or plain runs inside
        containers (card_cls=None)."""
        out: list[str] = []
        card: list[str] = []
        lead = ['']

        def flush():
            content = ''.join(card).strip()
            content = re.sub(r'^(?:<br>\s*)+|(?:\s*<br>)+$', '', content).strip()
            if has_letters(re.sub(r'<[^>]+>', '', content)) or lead[0]:
                if card_cls:
                    out.append(f'<div class="{card_cls}">{lead[0]}{content}</div>')
                else:
                    out.append(content)
            card.clear()
            lead[0] = ''

        def card_has_text():
            return has_letters(re.sub(r'<[^>]+>', '', ''.join(card))) or lead[0]

        i = 0
        while i < len(nodes):
            n = nodes[i]
            i += 1
            if isinstance(n, El) and n.tag in BLOCK_TAGS:
                flush()
                if n.tag == 'q':
                    author = []
                    j = i
                    while j < len(nodes) and _is_ws(nodes[j]):
                        j += 1
                    if j < len(nodes) and isinstance(nodes[j], El) and nodes[j].tag in ('rj', 'qau', 'au'):
                        author = [nodes[j]]
                        i = j + 1
                    out.append(self.quote_block(n, author))
                else:
                    out.append(self.block(n))
                continue
            if isinstance(n, El) and n.tag == 'sn' and card_cls:
                if card_has_text():
                    flush()
                lead[0] = f'<span class="sn-num">{self.kids(n, TOP.but(nested=True))}</span> '
                continue
            if isinstance(n, El) and n.tag == 'sd' and card_has_text():
                card.append('<br>')
            if isinstance(n, El) and n.tag == BR:
                card.append(' ')
                continue
            card.append(self.inline(n, TOP.but(nested=card_cls is None)))
        flush()
        return out

    def block(self, n: El) -> str:
        tag = n.tag
        if tag == 'note':
            return f'<div class="nt">{"".join(self.blocks(n.kids, None))}</div>'
        if tag == 'usage':
            return f'<div class="syn us">{"".join(self.blocks(n.kids, None))}</div>'
        if tag == 'syn':
            return f'<div class="syn">{self.syn(n)}</div>'
        if tag == 'cs':
            return self.cs(n)
        if tag in TABLE_TAGS:
            return f'<div class="tb">{self.kids(n, TOP.but(nested=True))}</div>'
        if tag in ('pre', 'song'):
            return f'<div class="pre">{self.kids(n, TOP.but(nested=True, pre=True)).strip()}</div>'
        return self.kids(n, TOP.but(nested=True))

    def syn(self, n: El) -> str:
        """Synonym list: links each listed word that is a key, keeping the
        original punctuation. Prose discussions are left as written."""
        text = collapse_ws(plain_text(n))
        text = re.sub(r'^\s*Syn\.\s*[-—]*\s*', '', text)
        items = [x for x in re.split(r'[;,.]', text) if x.strip()]
        listy = items and all(len(x.split()) <= 4 for x in items)
        parts = []
        for k in n.kids:
            if listy and isinstance(k, str):
                parts.append(self.syn_text(k))
            elif isinstance(k, El) and k.tag in BLOCK_TAGS:
                parts.append(self.block(k) if k.tag != 'q' else self.quote_block(k, []))
            else:
                parts.append(self.inline(k, TOP.but(nested=True)))
        html = ''.join(parts).strip()
        html = re.sub(r'^(<b>Syn\.\s*—</b>)(?=[^\s<])', r'\1 ', html)   # "Syn. —ant bear"
        return re.sub(r'(?:\s*<br>)+$', '', html)

    def syn_text(self, s: str) -> str:
        out = []
        for piece in re.split(r'([;,.]\s*|\s*[—]+\s*|\s*--+\s*)', s):
            if not piece:
                continue
            word = piece.strip()
            target = None
            if word and not re.fullmatch(r'[;,.\s—-]+', piece):
                low = word.casefold()
                if low not in SYN_STOPWORDS and len(word.split()) <= 3:
                    target = self.resolve(word) or (
                        self.resolve(_COL_LEAD_RE.sub('', word)) if low.startswith('to ') else None)
            if target:
                lead = piece[:len(piece) - len(piece.lstrip())]
                trail = piece[len(piece.rstrip()):]
                out.append(self.text(lead, TOP) + self.link(target, self.text(word, TOP)) + self.text(trail, TOP))
            else:
                out.append(self.text(piece, TOP))
        return ''.join(out)

    def cs_items(self, n: El) -> list[tuple[list, list]]:
        """Split a collocation section into items: (nodes, [col elements])."""
        items: list[tuple[list, list]] = []
        cur: list = []
        cols: list = []
        for k in n.kids:
            if isinstance(k, El) and k.tag in ('col', 'mcol') and has_letters(
                    ''.join(plain_text(x) for x in cur)):
                items.append((cur, cols))
                cur, cols = [], []
            if isinstance(k, El) and k.tag in ('col', 'mcol'):
                cols.extend([k] if k.tag == 'col' else
                            [x for x in iter_nodes(k) if x.tag == 'col'])
            cur.append(k)
        if cur:
            items.append((cur, cols))
        return items

    def cs_item_html(self, nodes) -> str:
        # drop the "--" separator that ends an item
        nodes = list(nodes)
        while nodes and (_is_ws(nodes[-1]) or (isinstance(nodes[-1], str)
                                               and re.fullmatch(r'[\s\-—]*', nodes[-1]))):
            nodes.pop()
        if nodes and isinstance(nodes[-1], str):
            nodes[-1] = re.sub(r'\s*--+\s*$', '', nodes[-1])
        if nodes and isinstance(nodes[0], str):
            nodes[0] = re.sub(r'^\s*--+\s*', '', nodes[0])
        return ''.join(self.blocks(nodes, None))

    def cs(self, n: El) -> str:
        rows = [self.cs_item_html(nodes) for nodes, _ in self.cs_items(n)]
        rows = [r for r in rows if r.strip()]
        return '<div class="cs">' + ''.join(f'<div class="ci">{r}</div>' for r in rows) + '</div>'

    # ── headers ──────────────────────────────────────────────
    def headword_html(self, header) -> str:
        """Headword line: every <hw> of the header in order. Inside <mhw>
        the text between the headwords ("or", "etc.", a part of speech)
        is kept; the braces and pronunciations (shown on their own line)
        are not."""
        parts: list[str] = []
        mark = ''
        for unit in header:
            if unit == '‖':
                mark = '‖'
                continue
            if not isinstance(unit, El) or unit.tag not in HEADER_UNITS:
                continue
            if unit.tag == 'hw':
                h = place_stress(self.kids(unit, TOP.but(hw=True, nested=True))).strip()
                if h:
                    parts.append((', ' if parts else '') + mark + h)
                    mark = ''
                continue
            line = []
            for k in unit.kids:
                if isinstance(k, El) and k.tag == 'hw':
                    line.append(place_stress(self.kids(k, TOP.but(hw=True, nested=True))))
                elif isinstance(k, El) and k.tag == 'pr':
                    line.append(' ')
                elif isinstance(k, str):
                    # free text between the headwords: "or", "etc.", and
                    # sometimes an untagged form ("god"fear`ing, <hw>...")
                    line.append(self.text(k.replace('{', ' ').replace('}', ' '),
                                          TOP.but(hw=True)))
                else:
                    line.append(self.inline(k, TOP.but(nested=True)))
            h = re.sub(r'\s+([,;.])', r'\1', _WS_RE.sub(' ', place_stress(''.join(line)))).strip(' ,;')
            if h:
                parts.append((', ' if parts else '') + mark + h)
                mark = ''
        return ''.join(parts)

    def header_html(self, header) -> str:
        hw = self.headword_html(header)
        prs = []
        rest = []
        ety = []
        for k in header:
            if not isinstance(k, El):
                continue
            if k.tag == 'pr' or (k.tag == 'mhw'):
                for p in ([k] if k.tag == 'pr' else [x for x in k.kids if isinstance(x, El) and x.tag == 'pr']):
                    if not pr_is_placeholder(p):
                        prs.append(place_stress(self.kids(p, TOP.but(pr=True, nested=True))).strip())
            elif k.tag == 'hw':
                continue
            elif k.tag == 'ety':
                ety.append(self.kids(k, TOP.but(nested=True)).strip())
            elif k.tag == 'pos':
                rest.append(f'<span class="pos">{self.kids(k, TOP.but(nested=True)).strip()}</span>')
            else:
                rest.append(f'<span class="mf">{self.kids(k, TOP.but(nested=True)).strip()}</span>')
        html = f'<span class="hw">{hw}</span>' if hw else ''
        if prs:
            html += f'<span class="pr">{" ".join(prs)}</span>'
        if rest:
            html += f'<div class="gh">{" ".join(rest)}</div>'
        for e in ety:
            html += f'<span class="et">{e}</span>'
        return html

    # ── paragraphs, groups, articles ─────────────────────────
    def para_html(self, p: Para) -> str:
        html = self._para_html(p)
        # Build check: every letter of the source paragraph is rendered.
        src_letters = letter_count(plain_text(p.tree))
        out_letters = letter_count(html_text(html))
        if out_letters < src_letters:
            self.lost.append(src_letters - out_letters)
        return html

    def _para_html(self, p: Para) -> str:
        nodes = p.body
        stray = {k.tag[1:] for k in iter_nodes(p.tree) if k.tag.startswith('/')}
        opened = {k.tag for k in iter_nodes(p.tree)}
        html = self.header_html(p.header) if p.role == 'head' else ''
        if continues_cs(p):
            # continuation of a collocation section from the previous <p>
            html += self.cs(El('cs', kids=list(nodes)))
        elif 'note' in stray and 'note' not in opened:
            html += f'<div class="nt">{"".join(self.blocks(nodes, None))}</div>'
        else:
            html += ''.join(self.blocks(nodes))
        return html

    def group_html(self, g: Group) -> str:
        html = ''.join(self.para_html(p) for p in g.paras)
        if g.from_list and g.parent_key:
            html += (f'<div class="xr">↑ '
                     f'{self.link(g.parent_key, esc(g.parent_key))}</div>')
        return html

    def article_html(self, art: Article) -> str:
        body = '<hr class="sep">'.join(self.group_html(g) for g in art.groups)
        return page(f'<div class="e">{dir_label(DIR_LABEL)}{body}</div>')


# ══════════════════════════════════════════════════════════════
# 9. Collocation articles
# ══════════════════════════════════════════════════════════════

def iter_cs(p: Para):
    """Collocation sections of a paragraph (incl. a continued one)."""
    if continues_cs(p):
        yield El('cs', kids=list(p.body))
        return
    for n in p.body:
        if isinstance(n, El):
            for x in iter_nodes(n):
                if x.tag == 'cs':
                    yield x


_FUNCTION_WORDS = {'a', 'an', 'the', 'and', 'or', 'nor', 'of', 'to', 'in', 'on', 'at',
                   'by', 'for', 'with', 'as', 'from', 'into', 'upon', 'off', 'out', 'up'}


def _words(s: str) -> list[str]:
    return s.split()


def _span_text(nodes, first: El, last: El) -> str:
    """Plain text of a collocation item from the top-level node that holds
    the <col> `first` to the one that holds `last`."""
    def holds(k, col):
        return isinstance(k, El) and any(x is col for x in iter_nodes(k))
    a = next(i for i, k in enumerate(nodes) if holds(k, first))
    b = next(i for i, k in enumerate(nodes) if holds(k, last))
    return collapse_ws(''.join(plain_text(k) for k in nodes[a:b + 1]))


_FRAGMENT_RE = re.compile(r'[a-z]+(?: [a-z]+)?')


def _substitute_last(head: str, frag: str) -> str:
    """'Sidereal clock' + 'day' -> 'Sidereal day'; 'Acception of persons'
    + 'faces' -> 'Acception of faces'; 'pocket an insult' + 'affront' ->
    'pocket an affront'. '' where the pattern is not that simple."""
    w = _words(head)
    if not w or w[0].casefold() in _FUNCTION_WORDS or not has_letters(w[0]):
        return ''
    if len(w) == 2 and ' ' not in frag:
        return f'{w[0]} {frag}'
    if len(w) == 3 and w[1] in ('of', 'a', 'an', 'the', 'to'):
        mid = w[1]
        if mid in ('a', 'an'):
            mid = 'an' if frag[:1] in 'aeiou' else 'a'
        return f'{w[0]} {mid} {frag}'
    return ''


def item_keys(cols, nodes) -> list[tuple[str, str]]:
    """Lookup keys of one collocation item, as (key, display) pairs.

    Most items name one phrase or a few complete alternatives ("Mosquito
    bar, Mosquito net"). Some abbreviate a list ("Sidereal clock, day,
    month, year"; "Shoulder, Pectoral, Pelvic, girdle") or split one
    phrase over several <col>s ("To curse by bell, book, and candle";
    "A son (or man) of Belial"): their fragments ("day", "book", "of
    Belial") are no phrases, so the full phrases are keyed instead."""
    disp = [collapse_ws(plain_text(c)) for c in cols]
    if len(cols) == 1:
        return [(collocation_key(disp[0]), disp[0])]
    # "Circular note, or Circular letter": "or" marks an alternative
    disp = [d if i == 0 else re.sub(r'^or\s+', '', d) for i, d in enumerate(disp)]
    words = [_words(d) for d in disp]
    single = [len(w) == 1 for w in words]
    out: list[tuple[str, str]] = []

    # Prefix list: "Shoulder, Pectoral, Pelvic, girdle",
    # "Ultimate, Proximate, ..., Volumetric analysis"
    last = words[-1]
    if (len(cols) >= 3 and all(single[:-1]) and disp[0][:1].isupper()
            and not disp[0].casefold().startswith('to ')
            and (len(last) == 1 and disp[-1][:1].islower()
                 or len(last) > 1 and disp[-1][:1].isupper())):
        tail = disp[-1] if len(last) == 1 else ' '.join(last[1:])
        out = [(collocation_key(f'{d} {tail}'), f'{d} {tail}') for d in disp[:-1]]
        out.append((collocation_key(disp[-1]), disp[-1]))
        return out

    # A <col> led by a lowercase function word continues the one before
    for i in range(1, len(disp)):
        lead = words[i][0] if words[i] else ''
        if lead == 'and' or (lead in ('a', 'an', 'the') and disp[0].startswith('To ')):
            # "To curse by bell, book, and candle"; "To give (one) a wide berth"
            text = _span_text(nodes, cols[0], cols[-1])
            return [(collocation_key(text), text)]
        if lead == 'of':
            if ' of ' in disp[0]:            # "Rolls of court, of parliament"
                alt = disp[0][:disp[0].index(' of ')] + ' ' + disp[i]
                out = [(collocation_key(disp[0]), disp[0]), (collocation_key(alt), alt)]
            else:                            # "A power, letter, warrant, of attorney"
                heads = [disp[0]] + [h for j, d in enumerate(disp[1:i], 1)
                                     if single[j] for h in [_substitute_last(disp[0], d)] if h]
                out = [(collocation_key(f'{h} {disp[i]}'), f'{h} {disp[i]}') for h in heads]
            return out

    for i, d in enumerate(disp):
        lead = words[i][0] if words[i] else ''
        if i == 0:
            out.append((collocation_key(d, whole=False), d))
        elif lead in _FUNCTION_WORDS and d[:1].islower():
            # "a spirit level" (an alternative); "on all fours" (the shared
            # tail of "To be, go, or run, on all fours"); "with", "a" dropped
            if len(words[i]) >= 3:
                d = re.sub(r'^(?:a|an)\s+', '', d)
                out.append((collocation_key(d, whole=False), d))
        elif (_FRAGMENT_RE.fullmatch(d)
              and _unspaced(d) not in {_unspaced(x) for x in disp[:i]}):
            # a fragment: "Sidereal clock, day" -> "Sidereal day" (but
            # "per cent, percent" and "beta-lactam, β-lactam" are spellings)
            h = _substitute_last(collocation_key(disp[0]) if disp[0].startswith('To ')
                                 else disp[0], d)
            if h:
                out.append((collocation_key(h), h))
            elif not single[i]:
                out.append((collocation_key(d, whole=False), d))
        else:
            out.append((collocation_key(d, whole=False), d))
    return out


def collect_collocations(articles, taken: set[str]):
    """[(key, display, item_nodes, parent_key)] for every collocation of a
    collocation section, and [(key, article_key, 'col')] redirects for the
    phrases that are only mentioned in running text ("used mostly in the
    phrase <col>go ballistic</col>").

    `taken` holds the casefolded article and redirect keys. A one-word
    "phrase" that is already a key ("Sidereal clock, day, ..." under
    Sidereal; "So ... as") is not keyed: a lookup of the word must show
    the word's own article, and the phrase stays in its parent article."""
    out = []
    inline = []
    r = Renderer({})

    def usable(key: str) -> bool:
        if not key or key.casefold() in ('syn', 'syn.'):
            return False
        return len(key.split()) > 1 or key.casefold() not in taken

    for art in articles.values():
        for g in art.groups:
            for p in g.paras:
                in_cs = set()
                for cs in iter_cs(p):
                    for nodes, cols in r.cs_items(cs):
                        in_cs.update(id(c) for c in cols)
                        if not cols:
                            continue
                        seen = set()
                        for key, disp in item_keys(cols, nodes):
                            if usable(key) and key.casefold() not in seen:
                                seen.add(key.casefold())
                                out.append((key, disp, nodes, art.key))
                for n in iter_nodes(p.tree):
                    if n.tag == 'col' and id(n) not in in_cs:
                        key = collocation_key(plain_text(n))
                        if usable(key):
                            inline.append((key, art.key, 'col'))
    return out, inline


def to_alias(key: str) -> str:
    """'to sheet-pile' -> 'sheet-pile': the bare word of a "To" phrase
    keyed with its "to" (collocation_key), or ''."""
    m = re.fullmatch(r'to (\S+)', key, re.I)
    return m.group(1) if m and m.group(1).casefold() not in _TO_PREP_NEXT else ''


# ══════════════════════════════════════════════════════════════
# 10. Checks
# ══════════════════════════════════════════════════════════════

def letter_count(s: str) -> int:
    return sum(1 for c in s if unicodedata.category(c) in ('Lu', 'Ll', 'Lt', 'Lo'))


def html_text(html: str) -> str:
    return _stdlib_html.unescape(re.sub(r'<[^>]+>', '', html))


_HREF_RE = re.compile(r'href="entry://([^"]*)"')

# <ent>s that upstream gives an entry without using them in it: variant
# spellings (leukemia / leukæmia, yogurt / yoghurt), a name in another
# order, and Commonty (whose own entry is missing upstream; it is listed
# with Common sense).
KEYS_NOT_IN_ENTRY = frozenset(k.casefold() for k in (
    'Alexipharmac', 'amygdalic acid', 'Commonty', 'frenulum', 'leukemia',
    'Sir Isaac Newton', 'Volt-amp', 'yogurt'))
_GERMAN_TRANSLIT = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'Ä': 'Ae', 'Ö': 'Oe',
                                  'Ü': 'Ue', 'ñ': 'ny', 'Ñ': 'Ny'})


def _key_forms(s: str) -> set[str]:
    """Comparable forms of a key or headword: 'Aufklärung' matches
    'Aufklaerung' and 'Aufklarung', 'Cañon' 'canyon', 'Coöperate'
    'Cooperate', 'rearm' 're-arm'; a parenthesis ("Abidjan
    (administrative)") is ignored."""
    s = re.sub(r'\s*\([^()]*\)', '', s)
    forms = {_squash(s), _squash(s.translate(_GERMAN_TRANSLIT))}
    return forms | {f.replace('-', '') for f in forms}


def _word_forms(s: str, key: bool = False) -> set[str]:
    """' word word ' forms of a text for whole-word search ("Morning
    glory" is named in "Morning-glory"; "shoo-in" is not in "Shooi, n.")."""
    if key:
        s = re.sub(r'\s*\([^()]*\)', '', s)
    s = re.sub(r"['’]", '', s)
    return {' ' + ' '.join(re.findall(r'[^\W_]+', fold_ascii(v).casefold())) + ' '
            for v in (s, s.translate(_GERMAN_TRANSLIT))}


def stray_keys(corpus: 'Corpus') -> list[str]:
    """Keys that are neither a headword of their entry nor words its
    text names: an <ent> attached to the wrong entry (the commented-out
    drafts of shoo-in and worthwhile once keyed Shooi and Worthy)."""
    out = []
    for g in corpus.groups:
        if not g.hw_forms:
            continue
        hws = set().union(*(_key_forms(h) for h in g.hw_forms if h))
        text = None
        for k in [g.key] + g.alt_keys:
            if _key_forms(k) & hws or k.casefold() in KEYS_NOT_IN_ENTRY:
                continue
            if text is None:
                text = ''.join(_word_forms(clean_form(' '.join(
                    plain_text(p.tree) for p in g.paras))))
            if not any(f in text for f in _word_forms(k, key=True)):
                out.append(k)
    return out


# ══════════════════════════════════════════════════════════════
# 11. Preview, metadata, main
# ══════════════════════════════════════════════════════════════

SAMPLE_WORDS = ['abandon', 'apron', 'take', 'alone', 'theater', 'eclat', 'myth',
                'aerobic', 'let alone', 'unabashed']

TITLE = "GCIDE — GNU Collaborative International Dictionary of English"
MDX_NAME = "GCIDE-EN.mdx"
MODIFIED_DATE = '2026-10-21'
# The header description shows the conversion month (strftime's %B in the
# default C locale gives the English month name).
MODIFIED_MONTH = datetime.strptime(MODIFIED_DATE, '%Y-%m-%d').strftime('%B %Y')

# The WordNet 1.5 notice: Princeton asks for its notice and disclaimer on
# ALL copies.
WORDNET_15_NOTICE = (
    "WordNet 1.5 Copyright 1995 by Princeton University. All rights reserved. "
    "Permission to use, copy, modify and distribute this software and database and its "
    "documentation for any purpose and without fee or royalty is hereby granted, provided "
    "that you agree to comply with the following copyright notice and statements, including "
    "the disclaimer, and that the same appear on ALL copies of the software, database and "
    "documentation, including modifications that you make for internal use or for "
    "distribution. THIS SOFTWARE AND DATABASE IS PROVIDED \"AS IS\" AND PRINCETON "
    "UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED. BY WAY OF "
    "EXAMPLE, BUT NOT LIMITATION, PRINCETON UNIVERSITY MAKES NO REPRESENTATIONS OR "
    "WARRANTIES OF MERCHANTABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE "
    "OF THE LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT INFRINGE ANY THIRD PARTY "
    "PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS. The name of Princeton University or "
    "Princeton may not be used in advertising or publicity pertaining to distribution of "
    "the software and/or database. Title to copyright in this software, database and any "
    "associated documentation shall at all times remain with Princeton University and "
    "LICENSEE agrees to preserve same."
)


def header_description() -> str:
    """The MDX header description, in the shared preset layout."""
    return description_html(
        title=TITLE,
        about=[
            "Based on Webster’s Revised Unabridged Dictionary (G. &amp; C. Merriam Co., 1913) "
            "with additions from WordNet 1.5, proof-read and supplemented by volunteers. "
            "Definitions, etymologies, literary quotations, phrases, and pronunciation "
            "where available.",
            "‖ marks a foreign word not naturalized in English; "
            '<span style="border:1px solid #c7c7cc;border-radius:3px;padding:0 2px">?</span> '
            "marks a character not legible in the source.",
        ],
        source=(
            f"GCIDE {GCIDE_VERSION}, Copyright © 1992–2021 Patrick J. Cassidy; "
            '<a href="https://www.gnu.org/software/gcide/">www.gnu.org/software/gcide</a> '
            f'(<a href="https://ftp.gnu.org/gnu/gcide/">gcide-{GCIDE_VERSION}.tar.xz</a>). '
            "WordNet 1.5 Copyright © 1995 Princeton University "
            '(<a href="https://wordnet.princeton.edu/license-and-commercial-use">license</a>).'
        ),
        license=(
            "GCIDE is free; you can redistribute it and/or modify it under the terms "
            "of the GNU General Public License as published by the Free Software Foundation; "
            "either version 3, or (at your option) any later version "
            '(<a href="https://www.gnu.org/licenses/gpl-3.0.html">GPL-3.0-or-later</a>). '
            "It is distributed WITHOUT ANY WARRANTY; without even the implied warranty of "
            "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE."
        ),
        changes=(
            "reformatted as HTML, Greek transliterations converted to Greek script, "
            "stress marks shown as ˈ (primary) and ˌ (secondary) before the stressed "
            "syllable, cross-references linked, and lookup keys added for variant "
            "spellings, inflected forms and phrases"
        ),
        source_code_url=DICTIONARY_SOURCES_URL,
        date=MODIFIED_MONTH,
        notices=[WORDNET_15_NOTICE],
    )


def descriptions() -> dict[str, str]:
    """Header description of every MDX this script builds, without building."""
    return {MDX_NAME: header_description()}


STRICT = True      # content checks fail the build


# Order of the records that share a key (ignoring case): the app shows
# every one of them, exact-case matches first and then in MDX order. All
# records of a key are given the spelling of the best-ranked one, so the
# order is this rank: the word's own article, then the articles of the
# paragraphs that list it as a headword, phrase articles, derived forms.
RANK = {'article': 0, 'ent': 1, 'hw': 1, 'phrase': 2, 'form': 3, 'solid': 3,
        'phrasefold': 3, 'phraseto': 3, 'draft': 3, 'col': 4}

# <ent>s of entries that upstream commented out (Corpus._drop_pending), and
# what their drafts refer to: "worthwhile ... See worth while"; "sopping,
# more recent version of soppy".
DRAFT_REDIRECTS = {'worthwhile': 'Worth while', 'sopping': 'Soppy'}


def app_lookup_first(groups, query):
    """The first record the app shows for `query` (emulation)."""
    hits = groups.get(query.casefold(), [])
    exact = [h for h in hits if h[0] == query]
    return (exact + [h for h in hits if h[0] != query])[0] if hits else None


def build_records():
    """Parse, index and render everything; run the content checks.
    Returns the records (key, body) in MDX order."""
    print(f"Parsing GCIDE {GCIDE_VERSION} (A-Z)...")
    corpus = Corpus()
    corpus.load(DATA_DIR)
    if UNKNOWN_ENTITIES:
        raise SystemExit(f'unknown GCIDE entities: {dict(UNKNOWN_ENTITIES)}')
    print(f"  {len(corpus.groups):,} homograph groups; {dict(corpus.stats)}")

    articles, raw_redirects = build_index(corpus)
    redirects = finalize_redirects(articles, raw_redirects)
    taken = set(articles) | {src.casefold() for src, _t, _k in redirects}
    collocs, inline_cols = collect_collocations(articles, taken)
    phrase_keys = {k.casefold() for k, _d, _n, _p in collocs}
    redirects = finalize_redirects(
        articles, raw_redirects + [r for r in inline_cols if r[0].casefold() not in phrase_keys])
    # ASCII spellings of accented phrases: "coup d'etat" -> "Coup d'état"
    known = set(articles) | phrase_keys | {src.casefold() for src, _t, _k in redirects}
    for key, _d, _n, _p in collocs:
        f = fold_ascii(key)
        if f != key and f.casefold() not in known:
            known.add(f.casefold())
            redirects.append((f, key, 'phrasefold'))
        # "to sheet-pile" is found as "sheet-pile" too, where that is no key
        bare = to_alias(key)
        if bare and bare.casefold() not in known:
            known.add(bare.casefold())
            redirects.append((bare, key, 'phraseto'))
        # ... and "To let alone", keyed "let alone", as "to let alone"
        if re.match(r'to\s', _d, re.I) and not re.match(r'to\s', key, re.I):
            full = f'to {key}'
            if full.casefold() not in known:
                known.add(full.casefold())
                redirects.append((full, key, 'phraseto'))
    # words whose entry upstream commented out, where the draft names
    # the entry to see (CIDE.W "should add separate worthwhile")
    for src in corpus.dropped_ents:
        target = DRAFT_REDIRECTS.get(src)
        if target and target.casefold() in (set(articles) | phrase_keys) and \
                (src.casefold(), target.casefold()) not in {
                    (s.casefold(), t.casefold()) for s, t, _k in redirects}:
            redirects.append((src, target, 'draft'))

    # Every key in creation order with its rank; the best-ranked spelling
    # of each key becomes the spelling of all its records.
    entries = ([(art.key, 'article') for art in articles.values()]
               + [(key, 'phrase') for key, _d, _n, _p in collocs]
               + [(src, kind) for src, _t, kind in redirects])
    canon: dict[str, tuple[int, str]] = {}
    for i, (key, kind) in enumerate(entries):
        cf = key.casefold()
        if cf not in canon or RANK[kind] < canon[cf][0]:
            canon[cf] = (RANK[kind], key)
    keys = {cf: spelling for cf, (_r, spelling) in canon.items()}
    renderer = Renderer(keys)

    records: list[tuple[str, str, int]] = []
    # 1. articles, with the per-paragraph letter check
    lost = []
    for art in articles.values():
        renderer.lost = []
        records.append((art.key, renderer.article_html(art), RANK['article']))
        lost.extend((art.key, n) for n in renderer.lost)
    n_articles = len(records)
    # 2. collocations
    for key, disp, nodes, parent in collocs:
        item = renderer.cs_item_html(nodes)
        hw = esc(clean_form(fix_display_typos(re.sub(r'\{[^{}]*\}', '', disp))))
        body = page(f'<div class="e">{dir_label(DIR_LABEL)}<span class="hw">{hw}</span>'
                    f'<div class="sn">{item}</div>'
                    f'<div class="xr">↑ {renderer.link(keys[parent.casefold()], esc(parent))}'
                    '</div></div>')
        records.append((key, body, RANK['phrase']))
    n_colloc = len(records) - n_articles
    # 3. redirects
    for src, target, kind in redirects:
        records.append((src, f'@@@LINK={keys[target.casefold()]}', RANK[kind]))
    n_redirects = len(redirects)

    # One spelling per key; MDX order = (key, rank, creation order). The
    # packer keeps the order of records whose keys compare equal.
    order = sorted(range(len(records)), key=lambda i: (
        keys[records[i][0].casefold()].casefold(), keys[records[i][0].casefold()],
        records[i][2], i))
    out = [(keys[records[i][0].casefold()], records[i][1]) for i in order]

    # ── checks ──
    problems = []
    if lost:
        problems.append(f'{len(lost)} paragraphs lose letters, e.g. {lost[:10]}')
    all_keys = {k.casefold() for k, _ in out}
    broken = Counter()
    for _k, body in out:
        if body.startswith('@@@LINK='):
            if body[8:].casefold() not in all_keys:
                broken['@@@LINK=' + body[8:]] += 1
        for t in _HREF_RE.findall(body):
            if _stdlib_html.unescape(t).casefold() not in all_keys:
                broken[t] += 1
    if broken:
        problems.append(f'{sum(broken.values())} broken links: {broken.most_common(20)}')
    bad_keys = [k for k, _ in out if k != k.strip() or '\n' in k or not k]
    if bad_keys:
        problems.append(f'bad keys: {bad_keys[:20]}')
    # A lookup of an article's word, as typed (lowercase or as printed),
    # must show that article first.
    groups = defaultdict(list)
    for k, body in out:
        groups[k.casefold()].append((k, body))
    article_body = {art.key.casefold(): records[i][1]
                    for i, art in enumerate(articles.values())}
    shadowed = []
    for cf, body in article_body.items():
        spelling = keys[cf]
        for q in {spelling, spelling.lower(), spelling[:1].upper() + spelling[1:].lower()}:
            first = app_lookup_first(groups, q)
            if first is None or first[1] is not body:
                shadowed.append(q)
    if shadowed:
        problems.append(f'{len(shadowed)} lookups do not show their own article first: '
                        f'{shadowed[:20]}')
    one_word_phrases = [k for k, _d, _n, _p in collocs
                        if len(k.split()) == 1 and k.casefold() in taken]
    if one_word_phrases:
        problems.append(f'one-word phrase keys that are words: {one_word_phrases[:20]}')
    strays = stray_keys(corpus)
    if strays:
        problems.append(f'{len(strays)} keys are neither a headword of their entry nor '
                        f'named in it (an <ent> attached to the wrong entry?): {strays[:20]}')
    if SOURCE_FIXES_UNUSED:
        problems.append(f'source fixes that no longer apply: {SOURCE_FIXES_UNUSED}')
    for msg in problems:
        print(f'  ERROR: {msg}')
    if problems and STRICT:
        raise SystemExit(1)

    stats = dict(articles=n_articles, phrases=n_colloc, redirects=n_redirects,
                 links=renderer.stats['links'], er_unresolved=renderer.stats['er_unresolved'],
                 lost=lost, broken=broken)
    return out, stats, corpus, articles, redirects, collocs, renderer


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records, stats, *_ = build_records()
    n_articles, n_colloc, n_redirects = stats['articles'], stats['phrases'], stats['redirects']
    txt_path = OUTPUT_DIR / "gcide_entries.txt"
    print(f"Writing → {txt_path}...")
    with open(txt_path, 'w', encoding='utf-8') as f:
        for key, body in records:
            f.write(f"{key}\n{body}\n</>\n")
    count = len(records)
    print(f"  {count:,} records: {n_articles:,} articles, {n_colloc:,} phrase articles, "
          f"{n_redirects:,} redirects; {stats['links']:,} links, "
          f"{stats['er_unresolved']:,} unresolved cross-references")

    mdx_path = OUTPUT_DIR / MDX_NAME
    success, size_mb, _ = pack_mdx(txt_path, TITLE, descriptions()[MDX_NAME],
                                   mdx_path, inputs=[DATA_DIR])
    if not success:
        raise SystemExit(1)
    print(f"✓ {mdx_path.name}: {size_mb:.1f} MB, {count:,} entries")

    write_catalog_metadata(
        mdx_path=mdx_path,
        display_name="GCIDE — English Dictionary",
        description=("GNU Collaborative International Dictionary of English: Webster's 1913 "
                     "with WordNet additions — definitions, etymologies, quotations, phrases, "
                     "and pronunciation where available."),
        direction={"src": "EN", "dest": ["EN"]},
        entries=count,
        license="GPL-3.0-or-later",
        data_source=f"GCIDE {GCIDE_VERSION} (GNU Project)",
        tier=1,
    )

    by_key = defaultdict(list)
    for k, body in records:
        by_key[k.casefold()].append(body)
    samples = []
    for w in SAMPLE_WORDS:
        for body in by_key.get(w.casefold(), []):
            if body.startswith('@@@LINK='):
                body = next((b for b in by_key.get(body[8:].casefold(), [])
                             if not b.startswith('@@@LINK=')), '')
            if body:
                samples.append(body)
    generate_preview(samples, OUTPUT_DIR / "gcide_preview.html", 'GCIDE — preview')


if __name__ == '__main__':
    main()
