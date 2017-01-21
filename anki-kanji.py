# coding=utf-8
import argparse
import os
import os.path as path
import anki
import anki.importing
import codecs
import lxml.html
import re
import requests
import traceback
from operator import itemgetter

# constants
DEFAULT_ANKI_DIR = path.join('Documents', 'Anki')
DEFAULT_ANKI_PROFILE = 'Teste'
DEFAULT_ANKI_COL = 'collection.anki2'
KD_FILE = 'Official_KanjiDamage_deck_REORDERED.apkg'
KD_DECK_NAME = 'KanjiDamage'
KDR_DECK_NAME = 'KanjiDamage Reordered'
NEW_DECK_NAME = 'KanjiDamage Words'
KANJI_REGEX = '[㐀-䶵一-鿋豈-頻]'


# parse command line arguments
opt_parser = argparse.ArgumentParser(description='Generates Anki collections based on KanjiDamage deck.')
opt_group_path = opt_parser.add_mutually_exclusive_group();
opt_group_path.add_argument('-f', '--file', help='path to collection file (overwrites -p)', metavar="COLLECTION")
opt_group_path.add_argument('-p', '--profile', help='the Anki profile to be used (will use default Anki path)')
opt_parser.add_argument('-u', '--update-kd', action='store_true', help='reimports Kanji Damage deck into the collection')
opt_parser.add_argument('-k', '--kd-file', default=KD_FILE, help='Kanji Damage deck file', metavar="APKG")
options = opt_parser.parse_args()
if not options.file:
    options.profile = options.profile or DEFAULT_ANKI_PROFILE
    options.file = path.expanduser(path.join('~', DEFAULT_ANKI_DIR, options.profile, DEFAULT_ANKI_COL))



##########################################
# Functions.
##########################################

# removes a deck from an Anki collection
def remove_deck(col, name):
    deck_id = col.decks.id(name, create=False)
    if deck_id:
        col.decks.rem(deck_id, True, True)
        print('removed ' + name)


def load_word_freq(path):
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
                if word in word_freq:
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
                    hit_count = hit_count + 1
                    sort_order = word_freq[example]
                examples.append({'word': example, 'furigana': furigana, 'meaning': meaning, 'order': sort_order})
        i = i + 1
    return reading, examples


# given one kanji, uses tangorin to find example words
def get_kanji_words(kanji):
    words = {}
    try:
        r = requests.get('http://tangorin.com/kanji/' + kanji)
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
                print('invalid kanji: ' + kanji)
                return None
            words[reading] = examples
    except:
        traceback.print_exc()
    return words


##########################################
# The script.
##########################################

# opens the collection
print('open collection: ' + options.file)
cwd = os.getcwd()
col = anki.Collection(path=options.file)
os.chdir(cwd)

# should update kanji damage deck?
if options.update_kd:
    print('removing previous Kanji Damage decks')
    remove_deck(col, KD_DECK_NAME)
    remove_deck(col, KDR_DECK_NAME)
    print('importing ' + options.kd_file)
    importer = anki.importing.AnkiPackageImporter(col, options.kd_file)
    importer.run()


# word frequency handling
word_freq = load_word_freq('word-freq.txt') # {word : frequency}
seen_words = {} # {word : kanji where it was seen before }
hit_count = 0 # times a word was seen

# extracts the kanji in order from KanjiDamage html cards
ids = col.findCards('"deck:KanjiDamage Reordered" card:read')
cards = sorted(col.renderQA(ids), key=lambda card: col.getCard(card['id']).due)
kanjis = []
kanji_regex = re.compile(r'[㐀-䶵一-鿋豈-頻]')
for card in cards:
    doc = lxml.html.fromstring(card['q'])
    node = next(iter(doc.xpath('//p[@class="kanji_character question"]')), None)
    if node is not None:
        kanji = (node.text or '').strip()
        if kanji_regex.match(kanji):
            kanjis.append(kanji)
print(kanjis)

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
for kanji in kanjis:
    n = n + 1
    if (n > max):
        break
    print('[' + str(n) + '/' + str(len(kanjis)) + '] ' + kanji)
    words = get_kanji_words(kanji)
    if not words:
        continue
    for reading, examples in words.items():
        example = next(iter(sorted(examples, key=itemgetter('order'), reverse=True)), None)
        if example:
            card = col.newNote()
            card['Front'] = u'<h3>' + example['word'] + '</h3>'
            card['Back'] = u'<h3>' + example['furigana'] + '</h3><br>' + example['meaning']
            col.addNote(card)
            col.save()
col.close()
print('hit rate: ' + str(hit_count) + '/' + str(len(seen_words)))
print(str(len(word_freq)))
