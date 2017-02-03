# coding=utf-8
import sys
import argparse
import os
import os.path
import shutil
import anki
import anki.importing
import codecs
from urllib.parse import urljoin
import lxml.html
import json
import re
import requests
import traceback
from operator import itemgetter

# constants
DEFAULT_ANKI_DIR = os.path.join('Documents', 'Anki')
DEFAULT_ANKI_PROFILE = 'Teste'
DEFAULT_ANKI_COL = 'collection.anki2'
KD_FILE = 'Official_KanjiDamage_deck_REORDERED.apkg'
KD_DECK_NAME = 'KanjiDamage'
KDR_DECK_NAME = 'KanjiDamage Reordered'
KD_MODEL = 'KanjiDamage'
KD_READ_TMPL = 'Read'
NEW_DECK_NAME = 'KanjiDamage Words'
KANJI_REGEX = '([一-龯])'
KATAKANA_REGEX = '([ァ-ン])'
KD_VALID_KANJI = re.compile(KANJI_REGEX + '|L|￥|(<<<)|丶|' + KATAKANA_REGEX)
KD_NUMBER_STRIP_REGEX = re.compile('([a-zA-Z]|\s)+')
KD_DAMAGE_BASE_URL = 'http://www.kanjidamage.com'
KD_KANJI_PATH = '/kanji'
TG_BASE_URL = 'http://tangorin.com'
TG_KANJI_PATH = '/kanji'


# parse command line arguments
opt_parser = argparse.ArgumentParser(description='Generates Anki collections based on KanjiDamage deck.')
opt_group_path = opt_parser.add_mutually_exclusive_group();
opt_group_path.add_argument('-f', '--file', help='path to collection file (overwrites -p)', metavar="COLLECTION")
opt_group_path.add_argument('-p', '--profile', help='the Anki profile to be used (will use default Anki path)')
opt_group_verb = opt_parser.add_mutually_exclusive_group();
opt_group_verb.add_argument('-v', '--verbose', action='store_true', help='show detailed messages')
opt_group_verb.add_argument('-q', '--quiet', action='store_true', help='show no message')
opt_parser.add_argument('-r', '--reset-kd', action='store_true', help='reimports Kanji Damage deck into the collection')
opt_parser.add_argument('-k', '--kd-file', default=KD_FILE, help='Kanji Damage deck file', metavar="APKG")
opt_parser.add_argument('-u', '--update-kd', action='store_true', help='updates Kanji Damage data from web')
opt_parser.add_argument('-d', '--force-download', action='store_true', help='forces to download all images again')
options = opt_parser.parse_args()
if not options.file:
    options.profile = options.profile or DEFAULT_ANKI_PROFILE
    options.file = os.path.expanduser(os.path.join('~', DEFAULT_ANKI_DIR, options.profile, DEFAULT_ANKI_COL))



##########################################
# Functions.
##########################################

# removes a deck from an Anki collection
def remove_deck(col, name):
    deck = col.decks.byName(name)
    if deck:
        col.decks.rem(deck['id'], True, True)
        if not options.quiet:
            print('removed ' + name)


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


# downloads a file inside collection's media path
def kd_download_file(col, path, base_url, cached=True):
    sub_dir, fn = os.path.split(path)
    sub_dir = col.media.dir() + sub_dir
    if not os.path.exists(sub_dir):
        os.makedirs(sub_dir)
    local_path = os.path.join(sub_dir, fn)
    if cached and os.path.exists(local_path) and options.verbose:
        print('[cached]: ' + local_path)
    else:
        r = requests.get(base_url + path, stream=True)
        with open(local_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:  # filter out keep-alive new chunks
                    f.write(chunk)
        if options.verbose:
            print('[download]: ' + local_path)
    return col.media.addFile(local_path)


# downloads the img nodes' src values inside collection's media path
def kd_fix_links(doc, base_url):
    for a in doc.xpath('descendant-or-self::a'):
        a.set('href', urljoin(base_url, a.get('href')))


# downloads the img nodes' src values inside collection's media path
def kd_download_images(col, doc, base_url):
    paths = []
    for img in doc.xpath('descendant-or-self::img'):
        src = img.get('src')
        if src.startswith('/'):
            src = kd_download_file(col, src, base_url)
            img.set('src', src)
            paths.append(src)
    return os.pathsep.join(paths)


def kd_nodes_to_string(col, node_list, base_url):
    nodes = []
    for e in node_list:
        if type(e) is lxml.html.HtmlElement:
            kd_download_images(col, e, base_url)
            nodes.append(html_to_string(e))
        else:
            nodes.append(str(e))
    return ''.join(nodes).strip()


# tries to extract the kd kanji, otherwise raises
def kd_get_kanji(doc):
    kanji = next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/img[1]')), None)
    if kanji is not None:
        return kanji
    else:
        return str(next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/text()')))).strip()


# tries to extract the kd kanji meaning, otherwise raises
def kd_get_meaning(doc):
    return str(next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="translation"]/text()')))).strip()


# tries to extract the kd kanji description, otherwise returns the empty string
def kd_get_usefulness(doc):
    return str(
        next(iter(doc.xpath('//div[@class="span4 text-righted"]/span[@class="usefulness-stars"]/text()')), '')
    ).strip()


# tries to extract the kd kanji description, otherwise returns the empty string
def kd_get_description(col, doc, base_url):
    path = '(//div[@class="description"])[1]/node()'
    return kd_nodes_to_string(col, doc.xpath(path), base_url)


# tries to extract the kd kanji 'used in field', otherwise returns the empty string
def kd_get_used_in(col, doc, base_url):
    path = '(//ul[@class="lacidar"])[1]'
    return kd_nodes_to_string(col, doc.xpath(path), base_url)


# tries to extract the kd kanji onyomi, otherwise returns the empty string
def kd_get_onyomi(col, doc, base_url):
    table = next(iter(doc.xpath('(//h2[text()="Onyomi"]/following-sibling::table[@class="definition"])[1]')), None)
    if table is None:
        return '', ''
    content = str(next(iter(table.xpath('./tr[1]/td[1]/span[@class="onyomi"]/text()')), ''))
    full = kd_nodes_to_string(col, [table], base_url)
    return full, content


# tries to extract the kd kanji kunyomi, otherwise returns the empty string
def kd_get_kunyomi(col, doc, base_url):
    table = next(iter(doc.xpath('(//h2[text()="Kunyomi"]/following-sibling::table[@class="definition"])[1]')), None)
    if table is None:
        return '', '', '', ''
    full = kd_nodes_to_string(col, [table], base_url)
    kun = kd_nodes_to_string(col, table.xpath('./tr[1]/td[1]/node()'), base_url)
    kun_meaning = str(next(iter(table.xpath('./tr[1]/td[2]/text()[1]')), '')).strip()
    kun_use = str(next(iter(table.xpath('./tr[1]/td[2]/span[@class="usefulness-stars"]/text()')), '')).strip()
    return full, kun, kun_meaning, kun_use


# tries to extract the kd kanji mnemonic, otherwise returns the empty string
def kd_get_mnemonic(col, doc, base_url):
    table = next(iter(doc.xpath('(//h2[text()="Mnemonic"]/following-sibling::table[@class="definition"])[1]')), None)
    if table is None:
        return '', ''
    content = kd_nodes_to_string(col, table.xpath('./tr[1]/td[2]/p/node()'), base_url)
    full = kd_nodes_to_string(col, [table], base_url)
    return full, content


# tries to extract the kd kanji components, otherwise returns the empty string
def kd_get_components(col, doc, base_url):
    h1 = next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/..')), None)
    if h1 is not None:
        return kd_nodes_to_string(col, [h1.tail] + h1.xpath('./following-sibling::*'), base_url)
    return ''


# tries to extract the kd kanji number, otherwise raises
def kd_get_number(doc):
    number = next(iter(doc.xpath('//div[@class="span8 text-centered"]/img[@alt="Flag"]')), None)
    number = number.tail if (number is not None) \
        else next(iter(doc.xpath('//div[@class="span8 text-centered"]/text()')), None)
    if number:
        number = KD_NUMBER_STRIP_REGEX.sub("", number)
        int(number)
    else:
        raise ValueError('couldn\'t extract number')
    return number


# tries to extract the kd kanji jukugo, otherwise returns the empty string
def kd_get_jukugo(col, doc, base_url):
    table = next(iter(doc.xpath('(//h2[text()="Jukugo"]/following-sibling::table[@class="definition"])[1]')), None)
    if table is None:
        return '', '', '', ''
    full = kd_nodes_to_string(col, [table], base_url)
    jk = kd_nodes_to_string(col, table.xpath('./tr[1]/td[1]/node()'), base_url)
    jk_meaning = str(next(iter(table.xpath('./tr[1]/td[2]/p/text()[1]')), '')).strip()
    jk_use = str(next(iter(table.xpath('./tr[1]/td[2]/p/span[@class="usefulness-stars"]/text()')), '')).strip()
    return full, jk, jk_meaning, jk_use


# tries to extract the kd kanji full header, otherwise returns the empty string
def kd_get_header(col, doc, base_url):
    path = '(//div[@class="span8"])[1]'
    return kd_nodes_to_string(col, doc.xpath(path), base_url)


# tries to extract the kd kanji lookalikes table, otherwise returns the empty string
def kd_get_lookalikes(col, doc, base_url):
    nodes = []
    n = next(iter(doc.xpath('//h2[text()="Lookalikes"]')), None)
    if n is not None:
        nodes.append(n.tail)
        n = n.getnext()
        while (n is not None) and (n.tag != 'h2'):
            nodes.append(n)
            n = n.getnext()
    return kd_nodes_to_string(col, nodes, base_url)


def kd_note_to_json(note):
    aux = {}
    for k, v in note.items():
        aux[k] = v
    return json.dumps(aux)


def tg_get_stroke_order(doc):
    try:
        div = doc.xpath('//div[@class="k-sod"]')[0]
        i = 0
        for svg in div.xpath('./svg'):
            # add stroke counter
            i += 1
            circle = svg.xpath('./circle')[0]
            x = float(circle.get('cx')) + 0
            y = float(circle.get('cy')) + 20
            order = lxml.html.fromstring(
                '<text style="'
                'fill:red;'
                'stroke:lightcoral;'
                'stroke-width:0.5;'
                'stroke-linecap:butt;'
                'stroke-linejoin:miter;'
                'stroke-opacity:1;'
                'font: bold 18px Helvetica sans-serif;">'
                + str(i) + '</text>'
            )
            order.set('x', str(x))
            order.set('y', str(y))
            svg.append(order)
        return html_to_string(div)
    except:
        pass
    return None


def kd_load_template_file(prefix, tmpl_name, side, default=''):
    path = '_'.join([prefix, tmpl_name, side]).lower() + '.html'
    try:
        with codecs.open(path, 'r', 'utf-8') as f:
            if not options.quiet:
                print('updating template from: ' + path)
            default = f.read()
    except:
        pass
    return default


def kd_update_templates(col, kd_model):
    for tmpl in kd_model['tmpls']:
        for key, side in [('q', 'front'), ('a', 'back')]:
            field = key + 'fmt'
            tmpl[field] = kd_load_template_file('kd', tmpl['name'], side, tmpl[field])
            tmpl['b' + field] = tmpl[field]

    # adds used tangorin css classes
    if not [l for l in kd_model['css'].splitlines() if l.strip().startswith('.k-sod')]:
        kd_model['css'] += '\n.k-sod {\n' \
                           '  line-height: 0;\n' \
                           '  padding: 4px 0;\n' \
                           '  margin: 5px 0;\n' \
                           '  zoom: 1.5;\n}'
    col.models.save(kd_model)


def kd_update(col, kanjis_by_text, kd_model, kd_deck):
    kd_model['did'] = kd_deck['id']

    kd_update_templates(col, kd_model)

    # processes data from kd website
    if not options.quiet:
        print('loading data from kanji damage website (this will take quite a while)...')
    col.models.setCurrent(kd_model)
    col.decks.select(kd_deck['id'])
    url = KD_DAMAGE_BASE_URL + KD_KANJI_PATH + '/1'
    kanji_re = re.compile(KANJI_REGEX)
    while url:
        try:
            r = requests.get(url)
            if options.verbose:
                print(r.url + ' - ' + str(r.status_code))
            if r.status_code != 200:
                return

            # parses the html tree
            doc = lxml.html.fromstring(r.content)
            kd_fix_links(doc, KD_DAMAGE_BASE_URL)

            # retrieves the data
            kanji = kd_get_kanji(doc)
            meaning = kd_get_meaning(doc)

            # get map key
            key = None
            if type(kanji) is lxml.html.HtmlElement:
                # kanji is an image
                kd_download_images(col, kanji, KD_DAMAGE_BASE_URL)
                kanji = html_to_string(kanji)
                key = meaning
            elif KD_VALID_KANJI.match(kanji):
                key = kanji

            # update/create note
            if key:
                note = kanjis_by_text[key] if key in kanjis_by_text else col.newNote()
                note['Kanji'] = kanji
                note['Meaning'] = meaning
                note['Number'] = kd_get_number(doc)
                note['Description'] = kd_get_description(col, doc, KD_DAMAGE_BASE_URL)
                note['Usefulness'] = kd_get_usefulness(doc)
                note['Full used In'] = kd_get_used_in(col, doc, KD_DAMAGE_BASE_URL)
                onyomi_full, onyomi = kd_get_onyomi(col, doc, KD_DAMAGE_BASE_URL)
                note['Full onyomi'] = onyomi_full
                note['Onyomi'] = onyomi
                kun_full, kun, kun_meaning, kun_use = kd_get_kunyomi(col, doc, KD_DAMAGE_BASE_URL)
                note['Full kunyomi'] = kun_full
                note['First kunyomi'] = kun
                note['First kunyomi meaning'] = kun_meaning
                note['First kunyomi usefulness'] = kun_use
                mnemonic_full, mnemonic = kd_get_mnemonic(col, doc, KD_DAMAGE_BASE_URL)
                note['Full mnemonic'] = mnemonic_full
                note['Mnemonic'] = mnemonic
                note['Components'] = kd_get_components(col, doc, KD_DAMAGE_BASE_URL)
                jk_full, jk, jk_meaning, jk_use = kd_get_jukugo(col, doc, KD_DAMAGE_BASE_URL)
                note['Full jukugo'] = jk_full
                note['First jukugo'] = jk
                note['First jukugo meaning'] = jk_meaning
                note['First jukugo usefulness'] = jk_use
                note['Full header'] = kd_get_header(col, doc, KD_DAMAGE_BASE_URL)
                note['Full lookalikes'] = kd_get_lookalikes(col, doc, KD_DAMAGE_BASE_URL)
                # stroke order from tangorin
                # if kanji_re.match(kanji):
                #     r = requests.get(TG_BASE_URL + TG_KANJI_PATH + '/' + kanji)
                #     stroke_order = tg_get_stroke_order(lxml.html.fromstring(r.content)) \
                #         if (r.status_code == 200) else None
                #     if stroke_order is not None:
                #         note['Stroke order'] = stroke_order

                if key not in kanjis_by_text:
                    col.addNote(note)
                    kanjis_by_text[key] = note
                else:
                    note.flush()
                if options.verbose:
                    print(kd_note_to_json(note))
            else:
                if not options.quiet:
                    print('ignored kanji: ' + kanji)

            # finds the link to the next kanji
            url = next(iter(doc.xpath('//div[@class="span2 text-righted"]/a[1]/@href')), None)
        except:
            traceback.print_exc()
            url = None
    col.save()


def load_word_freq(path):
    if not options.quiet:
        print('loading word frequency file: ' + path)
    word_freq = {}  # {word : frequency}
    try:
        f = codecs.open(path, 'r', 'utf-8')
        line = f.readline()
        while line:
            fields = line.split()
            try:
                freq = float(fields[1])
                word = fields[2]
                if word in word_freq and options.verbose:
                    print('duplicate word ' + word)
                else:
                    word_freq[word] = freq
            except:
                traceback.print_exc()
            line = f.readline()
    except:
        traceback.print_exc()
    return word_freq


# processes one row from the tangorin page main table
def process_tangorin_row(kanji, tr):
    global word_freq, seen_words, hit_count
    reading = tr[0].xpath('.//b[1]')[0].text
    examples = []
    i = 0
    while (i < len(tr[1])):
        node = tr[1][i]
        if node.tag == 'a':
            example = node.text_content()
            if not kanji in example:
                return None, None
            furigana = None
            meaning = None
            if node.tail and u'【' in node.tail.strip():
                i = i + 1
                while (i < len(tr[1])) and (tr[1][i].tag != 'br'):
                    node = tr[1][i]
                    if (node.tag == 'span') and (node.get('class') == 'kana'):
                        furigana = node.text
                    elif u'】' in node.tail:
                        meaning = node.tail.replace(u'】', '').strip()
                    i = i + 1
            if example not in seen_words:
                seen_words[example] = kanji
                sort_order = -1.0 * i
                if example in word_freq:
                    hit_count += 1
                    sort_order = word_freq[example]
                examples.append({'word': example, 'furigana': furigana, 'meaning': meaning, 'order': sort_order})
        i += 1
    return reading, examples


# given one kanji, uses tangorin to find example words
def tg_get_kanji_words(kanji):
    words = {}
    try:
        r = requests.get('http://tangorin.com/kanji/' + kanji)
        if options.verbose:
            print(r.url + ' - ' + str(r.status_code))
        if r.status_code != 200:
            return None
        doc = lxml.html.fromstring(r.content)
        table = next(iter(doc.xpath('//table[@class="k-compounds-table"]')), None)
        if table is None:
            return None

        for child in table.xpath('.//tr'):
            reading, examples = process_tangorin_row(kanji, child)
            if not reading:
                if options.verbose:
                    print('invalid kanji: ' + kanji)
                return None
            words[reading] = examples
    except:
        traceback.print_exc()
    return words


##########################################
# The script.
##########################################
def main():
    # opens the collection
    if not options.quiet:
        print('open collection: ' + options.file)
    cwd = os.getcwd()
    col = anki.Collection(path=options.file)
    os.chdir(cwd)

    # should update kanji damage deck?
    if options.reset_kd:
        if not options.quiet:
            print('removing previous Kanji Damage decks')
        remove_deck(col, KD_DECK_NAME)
        remove_deck(col, KDR_DECK_NAME)
        if not options.quiet:
            print('importing ' + options.kd_file)
        importer = anki.importing.AnkiPackageImporter(col, options.kd_file)
        importer.run()
        col.save()

    # finds the kanji damage deck
    kd_deck = col.decks.byName(KD_DECK_NAME)
    if not kd_deck:
        kd_deck = col.decks.byName(KDR_DECK_NAME)
    if not kd_deck:
        sys.exit('{0}: error: {1}'.format(sys.argv[0], 'couldn\'t find KanjiDamage[ Reordered] deck in the collection, try using option -u to import it'))

    # finds the kanji damage order and 'Read' template
    kd_model = col.models.byName(KD_MODEL)
    kd_read_tmpl_id = next((x['ord'] for x in kd_model['tmpls'] if x['name'] == KD_READ_TMPL))

    # gets the notes ids ordered by 'Read' card due date
    kd_notes = col.db.list('select nid from cards where did={0} and ord={1} order by due'.format(kd_deck['id'], kd_read_tmpl_id))

    # retrieves the ordered list of kanji
    kanjis = []
    kanjis_by_text = {}
    for note_id in kd_notes:
        note = col.getNote(note_id)
        kanjis.append(note)
        key = note['Kanji'] if KD_VALID_KANJI.match(note['Kanji']) else note['Meaning']
        if key in kanjis_by_text:
            sys.exit('duplicate kanji: {0}'.format(key))
        kanjis_by_text[key] = note

    # updates kanji damage deck
    if options.update_kd:
        if not options.quiet:
            print('updating ' + kd_model['name'] + '...')
        # removes media files
        if options.force_download:
            shutil.rmtree(os.path.join(col.media.dir(), 'assets'), ignore_errors=True)
            shutil.rmtree(os.path.join(col.media.dir(), 'visualaids'), ignore_errors=True)
        kd_update(col, kanjis_by_text, kd_model, kd_deck)
    exit()

    # word frequency handling
    word_freq = load_word_freq('word-freq.txt')  # {word : frequency}
    seen_words = {}  # {word : kanji where it was seen before }
    hit_count = 0  # times a word was seen

    # loads the examples from tangorin
    remove_deck(col, NEW_DECK_NAME)
    deck_id = col.decks.id(NEW_DECK_NAME)
    col.decks.select(deck_id)
    col.conf['nextPos'] = 1
    col.save()
    model = col.models.byName('Basic (and reversed card)')
    model['did'] = deck_id
    col.models.save(model)
    col.models.setCurrent(model)
    n = 0
    max = float('inf')
    for key in kanjis:
        n += 1
        if (n > max):
            break
        if options.verbose:
            print('[' + str(n) + '/' + str(len(kanjis)) + '] ' + key)
        words = tg_get_kanji_words(key)
        if not words:
            continue
        for reading, examples in words.items():
            example = next(iter(sorted(examples, key=itemgetter('order'), reverse=True)), None)
            if example:
                note = anki.notes.Note(col, kd_model)
                note['Front'] = u'<h3>' + example['word'] + '</h3>'
                note['Back'] = u'<h3>' + example['furigana'] + '</h3><br>' + example['meaning']
                col.addNote(note)
    col.save()
    col.close()
    if not options.quiet:
        print('hit rate: ' + str(hit_count) + '/' + str(len(seen_words)))
        print(str(len(word_freq)))

main()
