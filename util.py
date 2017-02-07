import re
import codecs
from urllib.parse import urljoin
import requests
import lxml.html
import json


KANJI_REGEX_STR = '([一-龯])'
KANJI_REGEX = re.compile(KANJI_REGEX_STR)
KATAKANA_REGEX_STR = '([ァ-ン])'
HIRAGANA_REGEX_STR = '([ぁ-ん])'
JAPANESE_REGEX_STR = '([ぁ-んァ-ン一-龯])'
NON_JAPANESE_REGEX_STR = '([^ぁ-んァ-ン一-龯])'


def load_file(path, log):
    try:
        with codecs.open(path, 'r', 'utf-8') as f:
            content = f.read()
            log.info('loaded file %s', path)
            return content
    except (FileNotFoundError, IOError):
        pass
    return None


def load_template_file(prefix, tmpl_name, side, log):
    return load_file('_'.join([prefix, tmpl_name, side]).lower() + '.html', log)


def get_html(url, log):
    r = requests.get(url)
    log.debug('%s - %d', r.url, r.status_code)
    if r.status_code != 200:
        log.error('failed to reach %s, status code: %d', r.url, r.status_code)
        return None
    return lxml.html.fromstring(str(r.content, 'utf-8'))


def add_base_url(doc, base_url):
    for a in doc.xpath('descendant-or-self::a'):
        a.set('href', urljoin(base_url, a.get('href')))


def html_to_string(e):
    html = lxml.html.tostring(e, encoding='unicode')
    positions = []
    src_re = re.compile('src="([^"])*"')
    blank_re = re.compile('%20')
    for match in src_re.finditer(html):
        for blank in blank_re.finditer(html, match.start(), match.end()):
            positions.append(blank.span())
    components = []
    first = 0
    for pos in positions:
        components.append(html[first:pos[0]])
        components.append(' ')
        first = pos[1]
    components.append(html[first:])
    return ''.join(components)


def note_to_json(note):
    aux = {}
    for k, v in note.items():
        aux[k] = v
    return json.dumps(aux)


# removes a deck from an Anki collection
def remove_model_and_deck(col, model, deck, log):
    m = col.models.byName(model)
    if m:
        col.models.rem(m)
        log.info('removed model %s', model)
    d = col.decks.byName(deck)
    if d:
        col.decks.rem(d['id'], True, True)
        log.info('removed deck %s', deck)
