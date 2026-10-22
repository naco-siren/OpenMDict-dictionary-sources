#!/usr/bin/env python3
"""
Build German-English MDX dictionaries from FreeDict TEI P5 XML data.

Produces TWO separate unidirectional MDX files:
  - FreeDict-DE-EN.mdx  (German headwords → English translations)
  - FreeDict-EN-DE.mdx  (English headwords → German translations)

Usage:
    python build_freedict_mdx.py

Input:  ../data/freedict/deu-eng/deu-eng.tei   (FreeDict deu-eng 1.9-fd1)
        ../data/freedict/eng-deu/eng-deu.tei   (FreeDict eng-deu 1.9-fd1)
Output: ../output/FreeDict-DE-EN/FreeDict-DE-EN.mdx
        ../output/FreeDict-EN-DE/FreeDict-EN-DE.mdx

What is rendered (everything the TEI carries for an entry):
  - headword grammar (POS, every gender, plural, verb class, case
    government); cards are grouped by their grammar, which heads each group;
  - translations with their own grammar (gender / plural / verb class),
    usage labels (domain, register, region, style, time), notes and
    abbreviations;
  - all examples with all their translations (the first few visible, the
    rest collapsed); example sentences that ding2tei split off into entries
    of their own and linked with xr type="see" are shown again as examples
    of the entries that refer to them;
  - abbreviations and irregular inflected forms of the headword;
  - xr type="syn" / "see" cross-references as entry:// links (in very
    large Ding lines, members link to the line's head for the rest).

Lookup keys: every TEI headword, plus @@@LINK redirects (or a small index
article when more than a few headwords share one) for the forms a user
would type but that are not headwords: the bare verb or phrase without
the Ding placeholders (consider, erinnern, take care of for
'consider sth.', 'sich an etw. erinnern', 'take care of sth.'),
irregular inflected forms (went, ate), abbreviations (Abf., z.B.),
headwords without quotes or '!' / '?' (Tschüss, Die Zauberflöte),
slash, ' / ' and optional-part alternatives (look forward), the Swiss
'ss' spelling of 'ß', pre-1996 triple-consonant spellings, and ASCII
spellings of accented loanwords (cafe, naive). A headword also shows the
phrases built on it ('remind' -> 'remind sb. of sth.', 'Kot' -> 'Kot…'):
the first few as whole articles, the rest as a list of links.
"""

import itertools
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from sys import intern

# ── Paths ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from shared.theme import (get_css, GLOSS, ORDERED_LIST, DOMAIN_BADGE, TAG_BADGE,
                          EXAMPLES_BILINGUAL, TEXT_SECONDARY, TEXT_LINK)
from shared.html import esc, entry_link
from shared.build import pack_mdx, generate_preview, write_catalog_metadata

DATA_DIR = SCRIPT_DIR.parent / "data" / "freedict"
OUTPUT_BASE = SCRIPT_DIR.parent / "output"

DEU_ENG_FILE = DATA_DIR / "deu-eng" / "deu-eng.tei"
ENG_DEU_FILE = DATA_DIR / "eng-deu" / "eng-deu.tei"

# Upstream release (teiHeader editionStmt / publicationStmt date).
EDITION = "1.9-fd1"
EDITION_DATE = "2022-04-15"
SOURCE_URL = ("https://download.freedict.org/dictionaries/{pair}/" + EDITION +
              "/freedict-{pair}-" + EDITION + ".src.tar.xz")
# GPLv3 section 5(a): "prominent notices stating that you modified it, and
# giving a relevant date". Update when the conversion changes.
MODIFIED = "October 2026"
# The teiHeader <availability> licenses the combined work under "both the
# GPLv3 and the AGPLv3, where each of these licenses applies to different
# parts" (Ding data GPLv2+, ding2tei-haskell converter AGPLv3+).
LICENSE = "GPL-3.0-or-later AND AGPL-3.0-or-later"

NS = '{http://www.tei-c.org/ns/1.0}'
T_ENTRY, T_FORM, T_ORTH, T_GRAM = NS + 'entry', NS + 'form', NS + 'orth', NS + 'gramGrp'
T_SENSE, T_CIT, T_QUOTE = NS + 'sense', NS + 'cit', NS + 'quote'
T_USG, T_NOTE, T_XR, T_REF = NS + 'usg', NS + 'note', NS + 'xr', NS + 'ref'

FLAG_DE = '\U0001F1E9\U0001F1EA'  # 🇩🇪
FLAG_EN = '\U0001F1EC\U0001F1E7\U0001F1FA\U0001F1F8'  # 🇬🇧🇺🇸

DIR_DE_EN = f'{FLAG_DE} Deutsch → English {FLAG_EN}'
DIR_EN_DE = f'{FLAG_EN} English → Deutsch {FLAG_DE}'

# ── Labels ─────────────────────────────────────────────────────

POS_MAP = {
    'n': 'n.', 'v': 'v.', 'adj': 'adj.', 'adv': 'adv.',
    'prep': 'prep.', 'conj': 'conj.', 'interj': 'interj.', 'int': 'interj.',
    'pron': 'pron.', 'art': 'art.', 'num': 'num.',
    'part': 'part.', 'ptcl': 'part.', 'abbr': 'abbr.',
}

GENDER_MAP = {'masc': 'm', 'fem': 'f', 'neut': 'nt'}

SUBC_MAP = {
    'intr': 'intr.', 'trans': 'tr.', 'refl': 'refl.',
    'sg only': 'sg only', 'pl only': 'pl only',
    'pers': 'pers.', 'rel': 'rel.', 'inter': 'interrog.',
}

TENSE_MAP = {'past': 'past', 'pstp': 'p.p.'}

# usg types rendered as amber register tags; the rest (dom, geo, hint,
# lang) as grey domain badges, collocations as italic text.
REGISTER_USG = {'reg', 'style', 'time'}

# Visible items before the rest is collapsed into <details>.
EXAMPLES_VISIBLE = 6
LINKS_VISIBLE = 8
# Members of a Ding line with more 'see' refs than this link to the line's
# head for the rest (see Renderer.group_head).
SEE_MAX = 20
# A new lookup key redirects (@@@LINK) to at most this many headwords;
# beyond that it gets an index article that lists them.
MAX_LINKS_NEW = 4
# An existing headword gets @@@LINK records to at most this many phrase
# headwords that reduce to it (house -> 'house sb./sth.', remind -> 'remind
# sb. of sth.'); any further ones are listed in a "Phrases" block of its
# own article.
MAX_LINKS_EXISTING = 3

# ── CSS ────────────────────────────────────────────────────────

_S_LIGHT, _S_DARK = TEXT_SECONDARY
_L_LIGHT, _L_DARK = TEXT_LINK

# Every article carries this block, and each further 258 bytes of CSS cost
# roughly 3 MB per direction after compression (measured), so the builder's own rules
# are folded into as few selectors as possible (instead of the INFLECTION
# and CROSSREF extensions). .xr links are not underlined: a list of a dozen
# underlined names is hard to read, and the app styles links the same way.
CSS = get_css(
    GLOSS, ORDERED_LIST, DOMAIN_BADGE, TAG_BADGE, EXAMPLES_BILINGUAL,
    extra_light=(
        f'.g,.nt,.ab,.cl,.xr,.inf{{color:{_S_LIGHT}}}'
        '.nt,.ab,.cl,.xr,summary{font-size:.85em}.g{font-size:.8em}'
        '.g,.cl{font-style:italic}.inf{display:block}.xr{margin-top:4px}'
        '.xl{font-weight:600}.xr a{text-decoration:none}'
        f'summary,.xr a{{color:{_L_LIGHT}}}'
    ),
    extra_dark=(
        f'.g,.nt,.ab,.cl,.xr,.inf{{color:{_S_DARK}}}'
        f'summary,.xr a{{color:{_L_DARK}}}'
    ),
)

# ── TEI parsing ────────────────────────────────────────────────

# Upstream writes '&#150;' (meant as the cp1252 en dash), which XML
# decodes to the C1 control U+0096. Map every C1 control through cp1252.
_C1_TABLE = {}
for _c in range(0x80, 0xA0):
    try:
        _C1_TABLE[_c] = bytes([_c]).decode('cp1252')
    except UnicodeDecodeError:
        _C1_TABLE[_c] = None
del _c


def _clean(text):
    if not text:
        return ''
    return ' '.join(text.translate(_C1_TABLE).split())


def _gram(elem):
    """gramGrp -> {'pos': [...], 'gen': [...], 'number': [...], ...} or None."""
    if elem is None:
        return None
    gram = {}
    for child in elem:
        value = _clean(child.text)
        if value:
            gram.setdefault(intern(child.tag[len(NS):]), []).append(intern(value))
    return gram or None


def _usg(elem):
    return tuple((intern(u.get('type', '')), intern(_clean(u.text)))
                 for u in elem.findall(T_USG) if _clean(u.text))


def _notes(elem):
    return tuple(_clean(n.text) for n in elem.findall(T_NOTE) if _clean(n.text))


def parse_tei_file(filepath: Path):
    """Parse a FreeDict TEI P5 file (iterparse).

    Yields one tuple per <entry>:
        (orth, gram, abbrevs, inflected, senses)
      gram      {'pos': [...], 'gen': [...], 'number': [...], 'subc': [...],
                 'colloc': [...]} or None
      abbrevs   [(orth, usg)]                     form/form[@type=abbrev]
      inflected [(orth, tenses, usg)]             form/form[@type=infl]
      senses    [(usg, notes, translations, examples, xrs)]
        usg          ((type, label), ...)
        translations [(quote, gram, usg, notes, abbrevs)]
        examples     [(source, (translation, ...))]
        xrs          [(type, (ref text, ...))]   ref text == target headword
    """
    for _event, elem in ET.iterparse(str(filepath), events=('end',)):
        if elem.tag != T_ENTRY:
            continue
        form = elem.find(T_FORM)
        orth = _clean(form.findtext(T_ORTH)) if form is not None else ''
        if not orth:
            elem.clear()
            continue

        abbrevs, inflected = [], []
        for sub in form.findall(T_FORM):
            sub_orth = _clean(sub.findtext(T_ORTH))
            if not sub_orth:
                continue
            if sub.get('type') == 'abbrev':
                abbrevs.append((sub_orth, _usg(sub)))
            elif sub.get('type') == 'infl':
                g = _gram(sub.find(T_GRAM)) or {}
                # upstream typo 'awoken. awaked' holds two forms
                pieces = (sub_orth.split('. ') if re.fullmatch(r'\w+\. \w+', sub_orth)
                          else [sub_orth])
                for piece in pieces:
                    inflected.append((piece, tuple(g.get('tns', ())), _usg(sub)))

        senses = []
        for sense in elem.findall(T_SENSE):
            translations, examples, xrs = [], [], []
            for cit in sense.findall(T_CIT):
                quote = _clean(cit.findtext(T_QUOTE))
                if not quote:
                    continue
                cit_type = cit.get('type', '')
                if cit_type == 'trans':
                    abbr = tuple(_clean(c.findtext(T_ORTH)) for c in cit.findall(T_CIT)
                                 if c.get('type') == 'abbrev' and _clean(c.findtext(T_ORTH)))
                    translations.append((quote, _gram(cit.find(T_GRAM)), _usg(cit),
                                         _notes(cit), abbr))
                elif cit_type == 'example':
                    ex_tr = tuple(_clean(c.findtext(T_QUOTE)) for c in cit.findall(T_CIT)
                                  if c.get('type') == 'trans' and _clean(c.findtext(T_QUOTE)))
                    examples.append((quote, ex_tr))
            for xr in sense.findall(T_XR):
                refs = tuple(_clean(r.text) for r in xr.findall(T_REF) if _clean(r.text))
                if refs:
                    xrs.append((intern(xr.get('type', '')), refs))
            senses.append((_usg(sense), _notes(sense), translations, examples, xrs))

        yield orth, _gram(elem.find(T_GRAM)), abbrevs, inflected, senses
        elem.clear()


def load_direction(filepath: Path) -> dict:
    """headword -> [entry, ...] in TEI order (first appearance order)."""
    by_key: dict = {}
    count = 0
    for entry in parse_tei_file(filepath):
        by_key.setdefault(entry[0], []).append(entry)
        count += 1
    print(f"  {count} TEI entries, {len(by_key)} distinct headwords")
    return by_key


def has_translation(entries) -> bool:
    return any(s[2] for e in entries for s in e[4])


def has_content(entries) -> bool:
    """Translations or examples (something to show on its own)."""
    return any(s[2] or s[3] for e in entries for s in e[4])


def select_headwords(by_key: dict) -> dict:
    """Drop headwords that have nothing to show.

    Kept: headwords with a translation or an example, and translation-less
    ones whose cross-references lead to a headword with content (they are
    rendered as their links). Dropped: upstream entries without any sense,
    and circular link-only pairs ('fox coral' <-> 'jasmine coral').
    """
    content = {k for k, es in by_key.items() if has_content(es)}
    kept, dropped = {}, []
    for key, entries in by_key.items():
        if key in content or any(r in content and r != key
                                 for e in entries for s in e[4]
                                 for _t, refs in s[4] for r in refs):
            kept[key] = entries
        else:
            dropped.append(key)
    if dropped:
        print(f"  dropped {len(dropped)} headwords with nothing to show: "
              + ', '.join(dropped[:8]) + (' …' if len(dropped) > 8 else ''))
    return kept


# ── Sentence detection ────────────────────────────────────────

_SENT_END = re.compile(r'[.!?…]["\'»«“”‘’)]*$')
_PLACEHOLDER_END = re.compile(r"(?:^|\s)(?:sth|sb|etw|jdn|jdm|jds|jd|etc|usw)\.$")


def is_sentence(key: str) -> bool:
    """Example-like headword: 3+ words, starts with a capital (or quote /
    digit / inverted mark), ends with sentence punctuation that is not the
    dot of a placeholder ('sth.', 'etw.')."""
    if not _SENT_END.search(key) or len(key.split()) < 3:
        return False
    if _PLACEHOLDER_END.search(key.rstrip('"\'»«“”‘’)')):
        return False
    first = key[0]
    return first.isupper() or first.isdigit() or first in '"\'„“‚‘»«¿¡(…'


# ── Rendering ──────────────────────────────────────────────────

def _labels(values, mapping) -> str:
    return '/'.join(mapping.get(v, v) for v in dict.fromkeys(values))


def _number_label(values) -> str:
    vals = set(values)
    if 'pl' in vals:
        return 'sg/pl' if 'sg' in vals else 'pl'
    return ''


def gram_extras(gram) -> list:
    """Gender / number / verb class / case government labels (no POS)."""
    if not gram:
        return []
    out = []
    gen = _labels(gram.get('gen', ()), GENDER_MAP)
    if gen:
        out.append(gen)
    num = _number_label(gram.get('number', ()))
    if num:
        out.append(num)
    subc = _labels(gram.get('subc', ()), SUBC_MAP)
    if subc:
        out.append(subc)
    for colloc in dict.fromkeys(gram.get('colloc', ())):
        out.append(colloc)
    return out


def usg_badge(usg_type: str, label: str) -> str:
    if usg_type == 'colloc':
        return f'<span class="cl">({esc(label)})</span>'
    cls = 'tg' if usg_type in REGISTER_USG else 'dm'
    return f'<span class="{cls}">{esc(label)}</span>'


def link_html(target: str) -> str:
    # The app splits entry:// targets at '#', so such keys are shown as text.
    if '#' in target:
        return f'<span>{esc(target)}</span>'
    return entry_link(target)


def links_block(label: str, targets: list) -> str:
    if not targets:
        return ''
    shown = ' · '.join(link_html(t) for t in targets[:LINKS_VISIBLE])
    html = f'<div class="xr"><span class="xl">{esc(label)}</span> {shown}'
    rest = targets[LINKS_VISIBLE:]
    if rest:
        html += (f'<details><summary>{len(rest)} more</summary>'
                 + ' · '.join(link_html(t) for t in rest) + '</details>')
    return html + '</div>'


def examples_block(examples: list) -> str:
    def one(src, trs):
        tr = f'<span class="ex-tr">{esc("; ".join(trs))}</span>' if trs else ''
        return f'<div class="ex"><span class="ex-src">{esc(src)}</span>{tr}</div>'
    html = ''.join(one(s, t) for s, t in examples[:EXAMPLES_VISIBLE])
    rest = examples[EXAMPLES_VISIBLE:]
    if rest:
        word = 'example' if len(rest) == 1 else 'examples'
        html += (f'<details><summary>{len(rest)} more {word}</summary>'
                 + ''.join(one(s, t) for s, t in rest) + '</details>')
    return html


def translation_html(trans, card_pos: tuple) -> str:
    quote, gram, usg, notes, abbr = trans
    parts = [esc(quote)]
    labels = []
    if gram:
        pos = tuple(gram.get('pos', ()))
        if pos and pos != card_pos:
            labels.append(_labels(pos, POS_MAP))
        labels += gram_extras(gram)
    if labels:
        parts.append(f' <span class="g">{esc(" ".join(labels))}</span>')
    for usg_type, label in usg:
        parts.append(' ' + usg_badge(usg_type, label))
    if abbr:
        parts.append(f' <span class="ab">(abbr. {esc(", ".join(dict.fromkeys(abbr)))})</span>')
    if notes:
        parts.append(f' <span class="nt">({esc("; ".join(dict.fromkeys(notes)))})</span>')
    return ''.join(parts)


class Renderer:
    """Renders the articles of one direction."""

    def __init__(self, by_key: dict, dir_label: str):
        self.by_key = by_key
        self.dir_label = dir_label
        self._sentence_cache: dict = {}

    # Example sentences that live in entries of their own (M1).
    def sentence_example(self, key: str):
        cached = self._sentence_cache.get(key)
        if cached is None:
            cached = False
            entries = self.by_key.get(key)
            if entries and is_sentence(key) and has_translation(entries):
                trs = tuple(dict.fromkeys(t[0] for e in entries for s in e[4] for t in s[2]))
                cached = (key, trs)
            self._sentence_cache[key] = cached
        return cached or None

    def translations_summary(self, key: str, limit: int = 3) -> str:
        trs = list(dict.fromkeys(t[0] for e in self.by_key.get(key, ())
                                 for s in e[4] for t in s[2]))
        text = '; '.join(trs[:limit])
        return text + ('; …' if len(trs) > limit else '')

    def _card(self, entry, sense, state) -> tuple:
        """-> (signature, header_gram_html, card_body_parts)."""
        _orth, gram, _abbr, _infl, _senses = entry
        usg, notes, translations, examples, xrs = sense
        gram = gram or {}

        card_pos = tuple(gram.get('pos', ()))
        if not card_pos and translations:
            tpos = {tuple((t[1] or {}).get('pos', ())) for t in translations}
            if len(tpos) == 1:
                card_pos = next(iter(tpos))
        pos_label = _labels(card_pos, POS_MAP)
        extras = tuple(gram_extras(gram))
        signature = (pos_label, extras)

        gram_html = ''
        if pos_label:
            gram_html += f'<span class="pos">{esc(pos_label)}</span>'
        if extras:
            gram_html += f'<span class="g">{esc(" ".join(extras))}</span>'

        hdr = ''.join(usg_badge(t, l) for t, l in usg)
        if notes:
            hdr += f'<span class="nt">({esc("; ".join(dict.fromkeys(notes)))})</span>'

        items = list(dict.fromkeys(translation_html(t, card_pos) for t in translations))
        if len(items) == 1:
            trans_html = f'<span class="gl">{items[0]}</span>'
        elif items:
            trans_html = '<ol class="d">' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>'
        else:
            trans_html = ''

        # Examples: own ones, then example sentences referenced via xr see.
        # Ding repeats an example under several senses, each with the
        # translation that fits that sense ('ace of hearts' -> Herzass /
        # Herzdaus), so a repeated example is skipped only when it adds no
        # translation that the article does not show yet.
        exs = []
        shown = state['examples']
        for src, trs in examples:
            trs = tuple(dict.fromkeys(trs))
            if src in shown and shown[src].issuperset(trs):
                continue
            shown.setdefault(src, set()).update(trs)
            exs.append((src, trs))
        syn, see, more = [], [], ''
        for xr_type, refs in xrs:
            head = (self.group_head(entry[0], refs)
                    if xr_type == 'see' and len(refs) > SEE_MAX else None)
            n_see = hidden = 0
            for ref in refs:
                if ref in state['linked'] or ref not in self.by_key:
                    continue
                if xr_type == 'see':
                    sent = self.sentence_example(ref)
                    if sent:
                        state['linked'].add(ref)
                        if ref not in shown:
                            shown[ref] = set(sent[1])
                            exs.append(sent)
                        continue
                    if head is not None and n_see >= SEE_MAX:
                        hidden += 1
                        continue
                    n_see += 1
                state['linked'].add(ref)
                (syn if xr_type == 'syn' else see).append(ref)
            if hidden:
                more += (f'<div class="xr">{hidden} more under {link_html(head)}</div>')

        body = (hdr, trans_html, examples_block(exs),
                links_block('Syn.', syn) + links_block('See also', see) + more)
        return signature, gram_html, body

    def group_head(self, key: str, refs: tuple):
        """Head of the Ding line behind a large xr 'see' group, or None.

        ding2tei links every unit of a Ding line to all the others, in line
        order, so the first ref of a member's list is the line's first unit.
        That head (and the unit after it, whose list the head opens) keeps
        the full list; every other member shows the first SEE_MAX links and
        points to the head for the rest, which turns the n*n links of a
        200-unit line (all tanagers, all statistical tests) into n.
        """
        head = refs[0]
        for entry in self.by_key.get(head, ()):
            for sense in entry[4]:
                for xr_type, head_refs in sense[4]:
                    if xr_type == 'see' and head_refs and head_refs[0] == key:
                        return None
        return head if head in self.by_key else None

    def forms_line(self, entries) -> str:
        abbrevs = {}
        inflected = {}
        for _orth, _gram, abbr, infl, _senses in entries:
            for form, usg in abbr:
                abbrevs.setdefault(form, usg)
            for form, tenses, usg in infl:
                label = '/'.join(TENSE_MAP.get(t, t) for t in tenses) or 'form'
                inflected.setdefault((label, form), usg)
        parts = []
        if abbrevs:
            parts.append('abbr. ' + ', '.join(
                esc(f) + ''.join(' ' + usg_badge(t, l) for t, l in u) for f, u in abbrevs.items()))
        by_label = {}
        for (label, form), usg in inflected.items():
            by_label.setdefault(label, []).append(
                esc(form) + ''.join(' ' + usg_badge(t, l) for t, l in usg))
        for label, forms in by_label.items():
            parts.append(f'{esc(label)} ' + ', '.join(forms))
        if not parts:
            return ''
        return f'<span class="inf">{" · ".join(parts)}</span>'

    def article(self, key: str, phrases: list = ()) -> str:
        entries = self.by_key[key]
        # examples: example source -> translations shown with it so far
        state = {'examples': {}, 'linked': {key}}
        groups: dict = {}
        for entry in entries:
            for sense in entry[4]:
                signature, gram_html, body = self._card(entry, sense, state)
                groups.setdefault(signature, (gram_html, []))[1].append(body)

        cards = []
        seen = set()
        for _sig, (gram_html, bodies) in groups.items():
            first = True
            for hdr, trans_html, ex_html, xr_html in bodies:
                ident = (hdr, trans_html)
                if (ident in seen and not ex_html and not xr_html
                        and not (first and gram_html)):
                    continue        # identical reading repeated upstream
                seen.add(ident)
                head = (gram_html if first else '') + hdr
                first = False
                card = '<div class="sn">'
                if head:
                    card += f'<div class="sn-hd">{head}</div>'
                card += trans_html + ex_html + xr_html + '</div>'
                cards.append(card)

        extra = [p for p in phrases if p not in state['linked']]
        inner = (f'<span class="dir">{esc(self.dir_label)}</span>'
                 f'<span class="hw">{esc(key)}</span>'
                 + self.forms_line(entries) + ''.join(cards)
                 + links_block('Phrases', extra))
        return f'{CSS}<div class="e">{inner}</div>'

    def index_article(self, alias: str, targets: list) -> str:
        items = []
        for t in targets:
            summary = self.translations_summary(t)
            hint = f' <span class="nt">{esc(summary)}</span>' if summary else ''
            items.append(f'<li>{link_html(t)}{hint}</li>')
        inner = (f'<span class="dir">{esc(self.dir_label)}</span>'
                 f'<span class="hw">{esc(alias)}</span>'
                 f'<div class="sn"><ol class="d">{"".join(items)}</ol></div>')
        return f'{CSS}<div class="e">{inner}</div>'


# ── Lookup aliases ─────────────────────────────────────────────
#
# Each alias maps to the headwords it should find. Rank decides the order
# of several targets and how an alias that is already a headword is used:
# aliases derived from a phrase headword (CORE, VERB, BARE) and the bare form
# of a combining form ('Kot…' -> 'Kot') also attach to an existing headword
# of that spelling; every other kind is added only where it is new.

RANK_INFL, RANK_ABBR, RANK_VARIANT, RANK_CORE, RANK_VERB, RANK_BARE = range(6)
ATTACH_RANKS = {RANK_CORE, RANK_VERB, RANK_BARE}

PLACEHOLDERS = {
    'en': {"sth.", "sb.", "sb.'s", "sth.'s", "one's", "oneself", "someone",
           "something", "somebody", "someone's", "somebody's"},
    'de': {"etw.", "sich", "jdn.", "jdm.", "jds.", "jd."},
}
REFLEXIVES = {"oneself", "sich"}
# Placeholders that can only be dropped as the first word: a possessive
# ("sb.'s better half" -> 'better half', but "having one's fling" must not
# become 'having fling'). The German subject 'jd.' is dropped only as the
# last word ('mehr ausgeben als jd.' -> 'mehr ausgeben'; 'jd. ist am Ende'
# gives no lookup form).
LEADING_ONLY = {
    'en': {"sb.'s", "sth.'s", "one's", "someone's", "somebody's"},
    'de': {"jds."},
}
# Longest derived lookup key, in words. Users type a verb or a short
# phrase; longer derived keys only add noise to the index.
MAX_ALIAS_WORDS = 4
# Two-word placeholders ('einer Sache'), also as last part of 'jdm./einer Sache'.
PLACEHOLDERS_2 = {'de': {("einer", "Sache"), ("eine", "Sache"), ("einer", "Person")},
                  'en': set()}
PREPOSITIONS_DE = set(
    "an auf aus bei bis durch für gegen gegenüber hinter in im ins am ans beim vom "
    "zum zur mit nach neben ohne seit über um unter von vor wegen wider zu zwischen "
    "als wie entlang".split())
PARTICLES_EN = set(
    "about above across after against along among around as at away back before "
    "behind below beneath beside between beyond by down for forth from in into of "
    "off on onto out over past round through to toward towards under up upon with "
    "within without apart aside together".split())
PRONOUNS = {"ich", "du", "er", "sie", "es", "wir", "ihr", "I", "you", "he", "she",
            "it", "we", "they", "Sie"}
FUNCTION_WORDS = ({w.lower() for w in PRONOUNS} | PREPOSITIONS_DE | PARTICLES_EN | set(
    "a an the my your his her its our their one's der die das den dem des ein eine "
    "einen einem einer eines mein dein sein unser euer and or und oder not nicht".split()))
# A placeholder removed after one of these leaves a phrase without its noun
# ('remit your sth.' -> 'remit your', "into a person's sth.").
DETERMINERS = {
    'en': set("a an the my your his her its our their every each".split()),
    'de': set("der die das den dem des ein eine einen einem eines mein meine meinen "
              "meinem meiner dein deine deinen deinem deiner seine seinen seinem seiner "
              "ihre ihren ihrem ihrer unsere unseren unserem unserer".split()),
}
CONJUNCTIONS = {"and", "or", "but", "und", "oder", "aber"}
# Grammar labels left at the end of a few upstream headwords ('mäandern vi',
# 'etw. umrunden vt', 'ginormous adj', German 'Drucköl n').
GRAMMAR_TAILS = {"vi", "vt", "vr", "vtr", "adj", "adv"}

_PAREN = re.compile(r'\(([^()]*)\)')
_HAS_LETTER = re.compile(r'[^\W\d_]')
_QUOTES = {'"': '"', "'": "'", '„': '“', '“': '”', '‚': '‘', '‘': '’', '»': '«', '«': '»'}
_TRIPLE = re.compile(r'([bcdfghklmnprstz])\1\1')
_DASHES = str.maketrans({'–': '-', '—': '-', '‑': '-', '‐': '-'})


def _is_placeholder(token: str, lang: str) -> bool:
    t = token.rstrip(',;:')
    return bool(t) and all(p in PLACEHOLDERS[lang] for p in t.split('/'))


def _is_preposition_de(token: str) -> bool:
    """'an', also a slashed group such as 'in/bei' ('in/bei etw. mitschwingen')."""
    return all(p in PREPOSITIONS_DE for p in token.split('/'))


def _is_determiner(token: str, lang: str) -> bool:
    t = token.lower()
    return (all(p in DETERMINERS[lang] for p in t.split('/'))
            or (lang == 'en' and t.endswith(("'s", "’s")) and t not in PLACEHOLDERS['en']))


def _is_conjugation_row(text: str) -> bool:
    """'I/he/she sunned myself/himself/herself', 'er/sie hat/hatte abgenommen'."""
    return any('/' in t and all(p in PRONOUNS for p in t.split('/')) for t in text.split())


def _strip_grammar_tail(tokens: list, lang: str) -> list:
    if len(tokens) > 1 and (tokens[-1] in GRAMMAR_TAILS or (
            lang == 'de' and tokens[-1] == 'n' and tokens[-2][:1].isupper())):
        return tokens[:-1]
    return tokens


def _paren_variants(text: str) -> tuple:
    """Optional parts in parentheses -> ([variants], simple).

    The first variant drops every '(...)' group ('jdn. (aus einer Lage)
    retten' -> 'jdn. retten'). When all groups are short lower-case word
    material ('(sich) drehen', 'to(wards)') the groups are "simple" and a
    second variant keeps their text ('sich drehen', 'towards'); '(III)' in
    'Eisen(III)-oxid' or a gloss like '(Unerwünschten)' is not simple.
    """
    if '(' not in text:
        return [text], True
    groups = _PAREN.findall(text)
    simple = bool(groups) and all(
        g and re.fullmatch(r"[a-zäöüß'.\- ]+", g) and len(g.split()) <= 3 for g in groups)
    drop = ' '.join(_PAREN.sub(' ', text).split())
    drop = re.sub(r'\s+([,;.!?])', r'\1', drop)
    variants = [drop]
    if simple:
        variants.append(' '.join(_PAREN.sub(r'\1', text).split()))
    return [v for v in dict.fromkeys(variants) if v and '(' not in v and ')' not in v], simple


def _common_prefix(parts) -> int:
    n = 0
    for chars in zip(*parts):
        if len(set(chars)) != 1:
            break
        n += 1
    return n


def _slash_options(token: str, spelling_only: bool, lang: str = 'en'):
    """Alternatives of an 'a/b' token, or None when it must stay as it is.

    spelling_only: accept only spelling variants that share their first
    letters ('smidge/smidgen', 'Thuja/Thuje', 'fibre/fiber'), not pairs
    such as 'AC/DC' or 'input/output' that name one thing together.
    """
    if '/' not in token:
        return None
    parts = token.split('/')
    if any(len(p) < 2 or not _HAS_LETTER.search(p) or p[0] == '-' or p[-1] == '-'
           or re.search(r'\d', p) or p in PRONOUNS for p in parts):
        return None
    # 'Strichkode/Strichcode-Beleg' means 'Strichkode-Beleg/Strichcode-Beleg':
    # a compound tail on one alternative only belongs to all of them.
    if len({'-' in p for p in parts}) > 1:
        return None
    if spelling_only and _common_prefix(parts) < 2:
        return None
    # 'Harnröhre/des Harnleiters', 'eine Sendung/einen Sender': a slash between
    # a word and the start of a longer phrase. Alternatives are words of one
    # kind: all function words ('on my/your behalf') or none, and in German
    # all nouns or none.
    if len({p.lower() in FUNCTION_WORDS for p in parts}) > 1:
        return None
    if lang == 'de' and len({p[0].isupper() for p in parts}) > 1:
        return None
    return parts[:4]


def _spaced_word_level(segments: list) -> bool:
    """Whether ' / ' separates single words or whole phrases.

    Next to a single word it is a word alternative like an unspaced slash
    ('individual trait / feature / characteristic', 'spoiled / spoilt',
    'fictionalized / fictionalised a true story', 'etw. rückhaltlos /
    schonungslos / ungeschönt tun'); between longer parts it separates
    whole phrases ('ring round sb. / call round sb.', 'look forward / to be
    looking forward to sth.').
    """
    return (all(' ' not in s for s in segments[1:-1])
            and (len(segments) > 2 or ' ' not in segments[0] or ' ' not in segments[-1]))


def phrase_aliases(key: str, lang: str):
    """Yield (alias, rank) derived from the words of a headword.

    RANK_CORE: the headword without its placeholders ('consider sth.' ->
    'consider', 'take care of sth.' -> 'take care of', 'sich an etw.
    erinnern' -> 'erinnern' and 'sich erinnern'; in German the preposition
    that governs a removed placeholder goes with it).
    RANK_VERB: the verb of a phrase: an English word directly followed by an
    object ('remind sb. of sth.', 'thank sb. for sth.' -> 'remind', 'thank'),
    the last word of a German phrase that starts with one ('jdn. auf die
    Palme bringen' -> 'bringen').
    RANK_BARE: other shortened forms ('look forward to sth. excitedly' ->
    'look forward to', 'remind of' -> 'remind').
    RANK_VARIANT: slash / optional-part alternatives of a headword without
    placeholders ('a smidge/smidgen' -> 'a smidgen').
    A ' / ' between longer parts separates whole-phrase alternatives, each
    of which is a source of aliases of its own ('look forward / to be
    looking forward to sth.' -> 'look forward', 'look forward to').
    """
    if is_sentence(key) or '…' in key or '...' in key or ',' in key or ';' in key:
        return
    if _is_conjugation_row(key):
        return
    if ' / ' not in key:
        yield from _phrase_aliases(key, lang)
        return
    # a ' / ' inside an optional part belongs to that part, which is dropped
    # ('jdn./etw. (auf seine Eignung / auf Unbedenklichkeit hin) überprüfen')
    text = _PAREN.sub(lambda m: m.group(0).replace(' / ', '/'), key)
    if ' / ' not in text:
        yield from _phrase_aliases(text, lang)
        return
    segments = [s.strip() for s in text.split(' / ') if s.strip()]
    if not all(_HAS_LETTER.search(s) for s in segments):
        return      # 'Prozent / % /', 'Rückschrägstrich / \\ /'
    if len(segments) < 2 or _spaced_word_level(segments):
        yield from _phrase_aliases('/'.join(segments), lang)
        return
    for i, seg in enumerate(segments):
        words = seg.split()
        has_ph = any(_is_placeholder(w, lang) for w in words)
        if lang == 'en' and i:
            # 'predispose sb. to sth. / to do sth.', 'squander a chance / an
            # opportunity', 'catch out sb. in sth. / doing sth.': a later
            # part that starts with a function word or a placeholder, or
            # has a single word besides them, continues the first one.
            if (words[0].lower() in FUNCTION_WORDS or _is_placeholder(words[0], lang)
                    or (has_ph and sum(not _is_placeholder(w, lang) for w in words) < 2)):
                continue
        elif lang == 'de' and i < len(segments) - 1:
            # German puts the verb last: an earlier part is a phrase of its
            # own only when it ends in one ('jdn. verunglimpfen / etw. in den
            # Schmutz ziehen'), not in 'sich einseitig / unausgewogen
            # ernähren'; from such a part only a new one-word key is taken
            # ('nachrichtlich jd. / an jdn.' -> 'nachrichtlich').
            last = _PAREN.sub(' ', seg).split()
            if not last or not re.fullmatch(r'[a-zäöüß]{2,}n', last[-1]):
                for alias, _rank in _phrase_aliases(seg, lang):
                    if ' ' not in alias:
                        yield alias, RANK_VARIANT
                continue
        yield from _phrase_aliases(seg, lang)
        if not has_ph and '/' not in seg:
            for v in _paren_variants(seg)[0]:
                vw = _strip_grammar_tail(v.split(), lang)
                if (len(vw) <= MAX_ALIAS_WORDS and _HAS_LETTER.search(v)
                        and vw[-1].lower() not in DETERMINERS[lang] | CONJUNCTIONS):
                    yield ' '.join(vw), RANK_VARIANT
    # 'look forward / to be looking forward to sth.' -> 'look forward to':
    # the first part with the particle that takes the object in a later one.
    if lang == 'en':
        first = segments[0].split()
        if 1 <= len(first) <= 3 and not any(_is_placeholder(w, lang) for w in first):
            for seg in segments[1:]:
                toks = seg.split()
                for j in range(len(toks) - 2):
                    if (toks[j] == first[-1] and toks[j + 1] in PARTICLES_EN
                            and any(p in PLACEHOLDERS['en'] for p in toks[j + 2].split('/'))):
                        yield ' '.join(first + [toks[j + 1]]), RANK_CORE


def _phrase_aliases(text: str, lang: str):
    """phrase_aliases() of one phrase (no ' / ' between whole phrases)."""
    variants, simple = _paren_variants(text)
    if not variants:
        return      # unbalanced '(' in one part of a ' / ' headword
    if any(_is_placeholder(t, lang) for t in variants[0].split()):
        # optional parts of a phrase are glosses ('sich (schon) auf etw.
        # freuen'); only the variant without them makes a useful core
        variants = variants[:1]
    paren_changed = simple and variants != [text]
    for variant in variants:
        tokens = _strip_grammar_tail(variant.split(), lang)
        head = tokens[1:] if lang == 'en' and tokens[:1] == ['to'] else tokens
        if lang == 'en':
            # 'persuade sb. to do sth.', 'let/rent sth. to sb.' -> the verb;
            # an idiom with a possessive ("attract sb.'s attention") ranks lower
            if (len(head) >= 2 and _is_placeholder(head[1], lang)
                    and re.fullmatch(r"[a-z][a-z'\-]*(?:/[a-z][a-z'\-]*)*", head[0])):
                rank = RANK_BARE if head[1] in LEADING_ONLY[lang] else RANK_VERB
                for verb in head[0].split('/')[:4]:
                    if verb not in FUNCTION_WORDS:
                        yield verb, rank
        elif (len(tokens) >= 3 and _is_placeholder(tokens[0], lang)
              and re.fullmatch(r'[a-zäöüß]{2,}n', tokens[-1])
              and (tokens[-1] == 'sein' or tokens[-1] not in FUNCTION_WORDS)):
            # 'jdn. auf die Palme bringen' -> 'bringen'
            yield tokens[-1], RANK_VERB
        if any(t.rstrip(',;:') in LEADING_ONLY[lang] for t in head[1:]):
            continue
        if lang == 'de' and 'jd.' in head[:-1]:
            continue    # 'jd. ist am Ende' -> 'ist am Ende' is no lookup form
        if lang == 'en':
            # 'look forward to sth. excitedly' -> 'look forward to': the
            # words before the first object placeholder.
            first_ph = next((i for i, t in enumerate(head) if _is_placeholder(t, lang)), 0)
            if (2 <= first_ph <= MAX_ALIAS_WORDS and first_ph < len(head) - 1
                    and all(re.fullmatch(r"[a-z][a-z'\-]*", t) for t in head[:first_ph])
                    and not _is_determiner(head[first_ph - 1], lang)):
                yield ' '.join(head[:first_ph]), RANK_BARE
        for keep_sich in ((False, True) if lang == 'de' else (False,)):
            # objects: a non-reflexive placeholder was removed
            kept, had_ph, objects, dangling = [], False, False, False
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                bare = tok.rstrip(',;:')
                removed = 0
                if i + 1 < len(tokens):
                    parts = tok.split('/')
                    pair = (parts[-1], tokens[i + 1].rstrip(',;:'))
                    if pair in PLACEHOLDERS_2[lang] and all(
                            p in PLACEHOLDERS[lang] for p in parts[:-1]):
                        removed, objects = 2, True
                if not removed and _is_placeholder(tok, lang) and not (keep_sich and bare == 'sich'):
                    removed = 1
                    objects |= not all(p in REFLEXIVES for p in bare.split('/'))
                if removed:
                    had_ph = True
                    # German: the preposition a placeholder depends on goes
                    # too ('sich an etw. erinnern' -> 'erinnern').
                    if lang == 'de' and kept and _is_preposition_de(kept[-1]):
                        kept.pop()
                    # 'remit your sth.', 'der/die etw. belegt' lose their noun;
                    # 'das jdm. Zustehende' keeps it
                    nxt = tokens[i + removed] if i + removed < len(tokens) else ''
                    dangling |= (bool(kept) and _is_determiner(kept[-1], lang)
                                 and not (lang == 'de' and nxt[:1].isupper()))
                    i += removed
                    continue
                kept.append(tok)
                i += 1
            if not kept or dangling:
                continue
            if keep_sich and (not had_ph or 'sich' not in kept):
                continue
            if not had_ph and not paren_changed and not any('/' in t for t in kept):
                continue
            # In a phrase a slash separates word alternatives ('clock/watch
            # maker'); a lone 'AC/DC' or 'input/output' is one thing, so a
            # single token is split only into spelling variants.
            options = []
            plain = {t for t in kept if '/' not in t}
            for tok in kept:
                opts = (_slash_options(tok, not had_ph and len(kept) == 1, lang)
                        if len(kept) <= 4 else None)
                if opts and any(p in plain for p in opts):
                    opts = None     # 'alternating current/direct current'
                options.append(opts or [tok])
            rank = RANK_CORE if had_ph else RANK_VARIANT
            for combo in itertools.islice(itertools.product(*options), 8):
                words = list(combo)
                if (lang == 'en' and rank == RANK_VARIANT and len(words) == 2
                        and words[0] in ('a', 'an', 'the') and len(options[1]) > 1):
                    yield words[1], RANK_VARIANT    # 'a smidge/smidgen' -> 'smidgen'
                if lang == 'en' and had_ph and len(words) > 1 and words[0] == 'to':
                    words = words[1:]
                if len(words) > MAX_ALIAS_WORDS or words[-1].lower() in CONJUNCTIONS:
                    continue    # 'an und für sich' -> 'an und'
                # A lone function word is the core only of '<word> <object>'
                # ('across sb./sth.', 'off sth.', 'bis auf etw.'), not of
                # 'by oneself' or 'for/by oneself'.
                if (had_ph and len(words) == 1 and words[0].lower() in FUNCTION_WORDS
                        and words[0] != 'sein' and not (objects and len(kept) == 1)):
                    continue
                yield ' '.join(words).strip(' ,;:'), rank
                if (lang == 'en' and had_ph and len(words) > 1
                        and all(w in PARTICLES_EN for w in words[1:])
                        and words[0] not in FUNCTION_WORDS):
                    yield words[0], RANK_BARE


def punctuation_aliases(key: str, lang: str):
    """-> [(alias, rank)]: the headword without wrapping quotes, without a
    final '!' / '?' or '…', or without a grammar label left in it."""
    out = []
    first = key[:1]
    if first in _QUOTES:
        # the last closing quote, so that "'A Midsummer Night's Dream'" keeps
        # its apostrophe; an optional '(...)' gloss may follow
        m = re.match(r'(.*)' + re.escape(_QUOTES[first]) + r'\s*(?:\([^()]*\))?$', key[1:])
        inner = m.group(1).strip().strip('\'"') if m else ''
        # not a quoted dialogue ("'It's Father's Day.' 'It is indeed!'")
        if inner and not re.search(r'[.!?,…][\'"“”‘’«»„]', inner):
            out.append((inner, RANK_VARIANT))
    for cand in [key] + [a for a, _r in out]:
        stripped = cand.lstrip('¡¿').rstrip()
        while stripped and stripped[-1] in '!?':
            stripped = stripped[:-1].rstrip()
        if stripped != cand and stripped and len(stripped.split()) <= 3:
            out.append((stripped, RANK_VARIANT))
    # A trailing ellipsis marks a combining form or an open phrase ('Kot…',
    # 'Fernseh…', 'für den Fall, dass …'); the form without it is what users
    # type, and it is shown under a headword of that spelling too ('Kot').
    if key.endswith(('…', '...')) and not is_sentence(key):
        stripped = (key[:-1] if key.endswith('…') else key[:-3]).rstrip()
        words = stripped.split()
        if (words and len(words) <= MAX_ALIAS_WORDS and _HAS_LETTER.search(stripped)
                and not all(_is_placeholder(w, 'en') or _is_placeholder(w, 'de')
                            for w in words)):
            out.append((stripped, RANK_CORE))
    tokens = key.split()
    if ' / ' not in key and _strip_grammar_tail(tokens, lang) != tokens:
        out.append((' '.join(tokens[:-1]), RANK_VARIANT))   # 'mäandern vi'
    return out


def spelling_aliases(key: str, lang: str):
    """Typable spellings: dashes, ASCII loanwords, Swiss ss, old triple consonants."""
    out = []
    dashed = key.translate(_DASHES)
    if dashed != key:
        out.append(dashed)
    base = dashed
    # Accents an English user types without (cafe, naive); German keeps
    # its umlauts, which the iOS keyboard types natively.
    decomposed = unicodedata.normalize('NFD', base)
    if any(unicodedata.combining(c) for c in decomposed):
        if lang == 'en':
            folded = ''.join(c for c in decomposed if not unicodedata.combining(c))
        else:
            folded = []
            for c in decomposed:
                if unicodedata.combining(c) and not (c == '̈' and folded and folded[-1] in 'aouAOU'):
                    continue
                folded.append(c)
            folded = ''.join(folded)
        folded = unicodedata.normalize('NFC', folded)
        if folded != base:
            out.append(folded)
    if lang == 'de':
        if 'ß' in base:
            out.append(base.replace('ß', 'ss'))
        if _TRIPLE.search(base):
            out.append(_TRIPLE.sub(r'\1\1', base))
    return out


def _valid_alias(alias: str) -> bool:
    return (bool(alias) and alias == alias.strip() and '\n' not in alias
            and alias != '</>' and not alias.startswith('@@@')
            and len(alias) <= 120 and bool(_HAS_LETTER.search(alias)))


def build_aliases(by_key: dict, lang: str):
    """-> (links, index_articles, phrases)

    links    {key: [target, ...]}  @@@LINK records written after key's own
                                   record (if any)
    index    {alias: [target, ...]} new keys with an index article
    phrases  {key: [target, ...]}  "Phrases" link block added to key's article
    """
    candidates: dict = {}
    attach_pairs = set()        # (alias, target) that may attach to a headword
    order = {k: i for i, k in enumerate(by_key)}
    lower_keys = {k.lower() for k in by_key}

    def add(alias, target, rank):
        if alias == target or not _valid_alias(alias):
            return
        bucket = candidates.setdefault(alias, {})
        if rank < bucket.get(target, 99):
            bucket[target] = rank
        if rank in ATTACH_RANKS:
            attach_pairs.add((alias, target))

    for key, entries in by_key.items():
        for alias, rank in phrase_aliases(key, lang):
            add(alias, key, rank)
        punct = punctuation_aliases(key, lang)
        for alias, rank in punct:
            add(alias, key, rank)
        for form in [key] + [a for a, _r in punct]:
            # Typable spellings only for words and short phrases: nobody
            # types a whole example sentence in Swiss spelling.
            if (len(form.split()) <= MAX_ALIAS_WORDS - 1 and '(' not in form
                    and not is_sentence(form)):
                for alias in spelling_aliases(form, lang):
                    # 'russen' (for 'rußen') would also turn up under 'Russen'
                    if lang == 'de' and alias not in by_key and alias.lower() in lower_keys:
                        continue
                    add(alias, key, RANK_VARIANT)
        for _orth, _gram, abbr, infl, _senses in entries:
            for form, _usg in abbr:
                add(form, key, RANK_ABBR)
                compact = re.sub(r'\.\s+(?=\w)', '.', form)     # 'z. B.' -> 'z.B.'
                if compact != form:
                    add(compact, key, RANK_ABBR)
            for form, _tenses, _usg in infl:
                add(form, key, RANK_INFL)
                for alias in spelling_aliases(form, lang):
                    add(alias, key, RANK_INFL)

    links, index, phrases = {}, {}, {}
    stats = defaultdict(int)
    for alias in sorted(candidates):
        targets = sorted(candidates[alias].items(),
                         key=lambda tr: (tr[1], len(tr[0].split()), order[tr[0]]))
        if alias in by_key:
            # The most direct phrase readings are shown in full under the
            # headword ('remind' + 'remind sb. of sth.'); any further ones
            # are listed in a "Phrases" block of its article.
            attach = [t for t, _r in targets if (alias, t) in attach_pairs]
            if not attach:
                stats['skipped (already a headword)'] += 1
                continue
            links[alias] = attach[:MAX_LINKS_EXISTING]
            stats['headwords with phrase redirects'] += 1
            if len(attach) > MAX_LINKS_EXISTING:
                phrases[alias] = attach[MAX_LINKS_EXISTING:]
                stats['headwords with a Phrases block'] += 1
            continue
        ts = [t for t, _r in targets]
        # The app also shows every key that differs only in case, so an
        # abbreviation such as 'CAT' or 'WEG' turns up under 'cat' and 'Weg'
        # too; there it gets a one-line index article, not whole articles.
        abbr_only = all(r == RANK_ABBR for _t, r in targets)
        if len(ts) <= MAX_LINKS_NEW and not (abbr_only and alias.lower() in lower_keys):
            links[alias] = ts
            stats['new redirect keys'] += 1
        else:
            index[alias] = ts
            stats['new index articles'] += 1
    for name, n in stats.items():
        print(f"  aliases: {n} {name}")
    return links, index, phrases


# ── Main build logic ──────────────────────────────────────────

SAMPLE_WORDS_DE = ['Wasser', 'Buch', 'Haus', 'Abfahrt', 'laufen', 'schön', 'erinnern',
                   'schade', 'Tschüss', 'Morgen']
SAMPLE_WORDS_EN = ['water', 'book', 'house', 'go', 'went', 'consider', 'bank', 'hello']


def write_direction(name: str, tei: Path, pair: str, txt_name: str, dir_label: str,
                    lang: str, title: str, desc: str, meta_desc: str,
                    direction: dict, data_source: str, samples: list,
                    preview_name: str):
    print(f"\n--- {title} ---")
    print(f"Parsing {tei}...")
    by_key = select_headwords(load_direction(tei))
    renderer = Renderer(by_key, dir_label)
    links, index, phrases = build_aliases(by_key, lang)

    out_dir = OUTPUT_BASE / name
    out_dir.mkdir(parents=True, exist_ok=True)
    txt = out_dir / txt_name

    all_keys = list(by_key) + [k for k in links if k not in by_key] + list(index)
    all_keys = list(dict.fromkeys(all_keys))
    all_keys.sort(key=str.lower)

    n_articles = n_links = n_index = 0
    with open(txt, 'w', encoding='utf-8') as f:
        for key in all_keys:
            if key in by_key:
                f.write(f"{key}\n{renderer.article(key, phrases.get(key, ()))}\n</>\n")
                n_articles += 1
            elif key in index:
                f.write(f"{key}\n{renderer.index_article(key, index[key])}\n</>\n")
                n_index += 1
            for target in links.get(key, ()):
                f.write(f"{key}\n@@@LINK={target}\n</>\n")
                n_links += 1
    count = n_articles + n_links + n_index
    print(f"  {count} records: {n_articles} articles, {n_index} index articles, "
          f"{n_links} @@@LINK redirects")

    mdx = out_dir / f"{name}.mdx"
    ok, size, _ = pack_mdx(txt, title, desc, mdx, inputs=[tei])
    if not ok:
        raise SystemExit(f"packing {mdx.name} failed")
    print(f"  {mdx.name}: {size:.1f} MB ({count} entries)")
    write_catalog_metadata(
        mdx_path=mdx,
        display_name=title,
        description=meta_desc,
        direction=direction,
        entries=count,
        license=LICENSE,
        data_source=data_source,
        tier=3,
    )

    previews = [renderer.article(w, phrases.get(w, ())) for w in samples if w in by_key]
    previews += [renderer.index_article(w, index[w]) for w in samples if w in index]
    generate_preview(previews, out_dir / preview_name, f"{title} — preview")


def description(title: str, pair: str, src_lang: str, dst_lang: str) -> str:
    """MDX header description: attribution, licenses, modification notice."""
    return (
        f"<p><b>{title}</b></p>"
        f"<p>{src_lang} → {dst_lang} dictionary: FreeDict <i>{pair}</i> {EDITION} "
        f"({EDITION_DATE}), generated by FreeDict from the "
        "<a href='https://dict.tu-chemnitz.de/'>Ding</a> dictionary 1.9 (TU Chemnitz) "
        "with ding2tei-haskell. <a href='https://freedict.org/'>freedict.org</a></p>"
        "<p>Copyright (C) Frank Richter 1995–2020, Einhard Leichtfuß 2020–2022, "
        "Sebastian Humenda 2021.</p>"
        "<p>Free software: you can redistribute it and/or modify it under the terms of "
        "both the <a href='https://www.gnu.org/licenses/gpl-3.0.html'>GNU GPL v3</a> and "
        "the <a href='https://www.gnu.org/licenses/agpl-3.0.html'>GNU AGPL v3</a>, each "
        "applying to different parts of this combined work (source form: the Ding "
        "dictionary, GPLv2+, and the ding2tei-haskell program, AGPLv3+). Distributed "
        "WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or "
        "FITNESS FOR A PARTICULAR PURPOSE.</p>"
        f"<p>Converted to MDict and modified by OpenMDict ({MODIFIED}): new layout, "
        "lookup keys for phrase, inflected and abbreviated forms, example sentences "
        "merged from linked entries. Source of the dictionary: "
        f"<a href='{SOURCE_URL.format(pair=pair)}'>freedict-{pair}-{EDITION}.src.tar.xz</a>.</p>"
    )


def main():
    print("=" * 60)
    print("Building FreeDict German↔English (2 MDX files)")
    print("=" * 60)

    write_direction(
        name="FreeDict-DE-EN", tei=DEU_ENG_FILE, pair="deu-eng",
        txt_name="freedict_de_en_entries.txt", dir_label=DIR_DE_EN, lang='de',
        title="FreeDict German-English",
        desc=description("FreeDict German-English", "deu-eng", "German", "English"),
        meta_desc=("German-English dictionary from FreeDict / Ding with genders, "
                   "usage and domain labels, examples and cross-references."),
        direction={"src": "DE", "dest": ["EN"]},
        data_source=f"FreeDict deu-eng {EDITION} (from the Ding dictionary, TU Chemnitz)",
        samples=SAMPLE_WORDS_DE, preview_name="freedict_de_preview.html",
    )
    write_direction(
        name="FreeDict-EN-DE", tei=ENG_DEU_FILE, pair="eng-deu",
        txt_name="freedict_en_de_entries.txt", dir_label=DIR_EN_DE, lang='en',
        title="FreeDict English-German",
        desc=description("FreeDict English-German", "eng-deu", "English", "German"),
        meta_desc=("English-German dictionary from FreeDict / Ding with German genders, "
                   "usage and domain labels, examples and cross-references."),
        direction={"src": "EN", "dest": ["DE"]},
        data_source=f"FreeDict eng-deu {EDITION} (from the Ding dictionary, TU Chemnitz)",
        samples=SAMPLE_WORDS_EN, preview_name="freedict_en_de_preview.html",
    )
    print("\nDone!")


if __name__ == '__main__':
    main()
