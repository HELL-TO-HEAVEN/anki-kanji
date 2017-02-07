# coding=utf-8
import sys
import argparse
import os
import os.path
import shutil
import logging
import codecs
import re
import anki
import util
from kanjidamage import KanjiDamage
from tangorin import Tangorin as tg

# constants
DEFAULT_KD_FILE = 'Official_KanjiDamage_deck_REORDERED.apkg'
DEFAULT_ANKI_DIR = os.path.join('Documents', 'Anki')
DEFAULT_ANKI_PROFILE = 'Teste'
DEFAULT_ANKI_COL = 'collection.anki2'
TG_FILE = 'tangorin.json'
KDW_DECK = 'KanjiDamage Words'
KDW_MODEL = 'KanjiDamageWords'


# parse command line arguments
opt_parser = argparse.ArgumentParser(description='Generates Anki collections based on KanjiDamage deck.')
opt_group_path = opt_parser.add_mutually_exclusive_group();
opt_group_path.add_argument('-f', '--file', help='path to collection file (overwrites -p)', metavar="COLLECTION")
opt_group_path.add_argument('-p', '--profile', help='the Anki profile to be used (will use default Anki path)')
opt_group_verb = opt_parser.add_mutually_exclusive_group();
opt_group_verb.add_argument('-v', '--verbose', action='store_true', help='show detailed messages')
opt_group_verb.add_argument('-q', '--quiet', action='store_true', help='show no message')
opt_parser.add_argument('-r', '--reset-kd', action='store_true', help='reimports Kanji Damage deck into the collection')
opt_parser.add_argument('-k', '--kd-file', default=DEFAULT_KD_FILE, help='Kanji Damage deck file', metavar="APKG")
opt_parser.add_argument('-u', '--update-kd', action='store_true', help='updates Kanji Damage data from web')
opt_parser.add_argument('-d', '--force-download', action='store_true', help='forces to download all images again')
options = opt_parser.parse_args()
if not options.file:
    options.profile = options.profile or DEFAULT_ANKI_PROFILE
    options.file = os.path.expanduser(os.path.join('~', DEFAULT_ANKI_DIR, options.profile, DEFAULT_ANKI_COL))

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG if options.verbose else logging.INFO)
log.addHandler(logging.NullHandler() if options.quiet else logging.StreamHandler(sys.stdout))


##########################################
# Functions.
##########################################

# reads the word frequency file and returns a map that, for each word (that contains at least a kanji),
# gives its frequency, normalized in [0,1] interval
def load_word_freq(path):
    log.info('loading word frequency file: %s', path)
    try:
        with codecs.open(path, 'r', 'utf-8') as f:
            word_freq = {}
            max_freq = float('-inf')
            line = f.readline()
            while line:
                fields = line.split()
                freq = abs(float(fields[1]))
                word = fields[2]
                if word in word_freq:
                    log.debug("duplicate word '%s'", word)
                elif util.KANJI_REGEX.search(word):
                    word_freq[word] = freq
                    max_freq = max(freq, max_freq)
                line = f.readline()
            for word in word_freq.keys():
                word_freq[word] /= max_freq
            return word_freq
    except (FileNotFoundError, IOError):
        log.info("couldn't load word frequency file: %s", path)
    return {}


# removes the previous 'kanji damage words' deck and model if it exists
# then creates and return a referende to them (deck, model)
def kdw_reset_model_and_deck(col):
    util.remove_model_and_deck(col, KDW_MODEL, KDW_DECK, log)
    deck_id = col.decks.id(KDW_DECK)
    model = col.models.new(KDW_MODEL)
    model['did'] = deck_id
    model['css'] = util.load_file('kdw.css', log) or model['css']
    col.models.add(model)

    col.models.addField(model, col.models.newField('Kanji'))
    col.models.addField(model, col.models.newField('Furigana'))
    col.models.addField(model, col.models.newField('Meaning'))
    col.models.addField(model, col.models.newField('Examples'))

    tmpl_read = col.models.newTemplate('Read')
    tmpl_read['qfmt'] = util.load_template_file('kdw', 'read', 'front', log) or ''
    tmpl_read['afmt'] = util.load_template_file('kdw', 'read', 'back', log) or ''
    tmpl_read['bqfmt'] = tmpl_read['qfmt']
    tmpl_read['bafmt'] = tmpl_read['afmt']
    col.models.addTemplate(model, tmpl_read)

    tmpl_meaning = col.models.newTemplate('Meaning')
    tmpl_meaning['qfmt'] = util.load_template_file('kdw', 'read', 'front', log) or ''
    tmpl_meaning['afmt'] = util.load_template_file('kdw', 'meaning', 'back', log) or ''
    tmpl_meaning['bqfmt'] = tmpl_meaning['qfmt']
    tmpl_meaning['bafmt'] = tmpl_meaning['afmt']
    col.models.addTemplate(model, tmpl_meaning)
    col.save()

    return model, col.decks.get(deck_id)


# removes the previous 'kanji damage words' deck if it exists and creates
# a new one
def kdw_create(col, kd):
    # word frequency handling
    word_freq = load_word_freq('word-freq.txt')  # {word : frequency in [0,1]}
    kanjis = kd.get_kanjis()  # [kanji characters, ordered by due date]
    tg_kanji_to_words = tg.get_kanji_to_words(TG_FILE, kanjis, log)  # {kanji : {reading : [words sorted by appearance]}}
    kd_kanji_to_words = kd.get_kanji_to_words()
    print(kd_kanji_to_words)
    nj_re = re.compile(util.NON_JAPANESE_REGEX_STR)
    nj_chars = {}
    for words in kd_kanji_to_words.values():
        for word in words:
            for c in nj_re.findall(word['word']):
                nj_chars[c] = nj_chars.get(c, 0) + 1
    print(nj_chars)

    word_to_kanjis = {}  # {word : [kanjis used in order of due date]}

    log.info("creating '%s' deck", KDW_DECK)
    kdw_model, kdw_deck = kdw_reset_model_and_deck(col)
    col.models.setCurrent(kdw_model)
    col.decks.select(kdw_deck['id'])
    col.conf['nextPos'] = 1
    exit()

    # loads the examples from tangorin
    n = 0
    max = float('inf')
    for key in kanjis:
        n += 1
        if (n > max):
            break
        log.debug('[%d/%d] %s', n, len(kanjis), key)
        words = tg_get_words_for_kanji(key)
        if not words:
            continue
        for reading, examples in words.items():
            example = next(iter(sorted(examples, key=itemgetter('order'), reverse=True)), None)
            if example:
                note = anki.notes.Note(col, kd.get_model())
                note['Front'] = u'<h3>' + example['word'] + '</h3>'
                note['Back'] = u'<h3>' + example['furigana'] + '</h3><br>' + example['meaning']
                col.addNote(note)
    col.save()
    col.close()
    log.info('hit rate: ' + str(hit_count) + '/' + str(len(seen_words)))
    log.info(str(len(word_freq)))


##########################################
# The script.
##########################################
def main():
    # opens the collection
    log.info('open collection: %s', options.file)
    cwd = os.getcwd()
    col = anki.Collection(path=options.file)
    os.chdir(cwd)

    kd = KanjiDamage(col, log)

    # should update kanji damage deck?
    if options.reset_kd:
        kd.reset(options.kd_file)

    # finds the kanji damage deck and model
    if not kd.get_deck():
        sys.exit(
            '{0}: error: {1}'.format(
                sys.argv[0],
                'couldn\'t find KanjiDamage[ Reordered] deck in the collection, try using option -r to import it'
            )
        )
    kd_model = kd.get_model()
    if not kd_model:
        sys.exit(
            '{0}: error: {1}'.format(
                sys.argv[0],
                'couldn\'t find KanjiDamage model in the collection, try using option -r to import it'
            )
        )

    # updates kanji damage deck
    if options.update_kd:
        log.info('updating %s...', kd_model['name'])
        # removes media files
        if options.force_download:
            shutil.rmtree(os.path.join(col.media.dir(), 'assets'), ignore_errors=True)
            shutil.rmtree(os.path.join(col.media.dir(), 'visualaids'), ignore_errors=True)
        kd.update()

    # recreates the kanji damage words deck
    kdw_create(col, kd)


if __name__ == '__main__':
    main()
