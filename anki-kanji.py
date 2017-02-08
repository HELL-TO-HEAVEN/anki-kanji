# coding=utf-8
import sys
import argparse
import os
import os.path
from operator import itemgetter
import shutil
import logging
import codecs
import copy
import json
import anki
import util
from kanjidamage import KanjiDamage
from tangorin import Tangorin as tg
from anki.exporting import AnkiPackageExporter


# constants
DEFAULT_KD_FILE = 'Official_KanjiDamage_deck_REORDERED.apkg'
DEFAULT_OUT_FILE = 'KanjiDamageWords.apkg'
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
opt_parser.add_argument('-o', '--output', default=DEFAULT_OUT_FILE, help='KanjiDamage Words output file', metavar="PATH")
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
    tmpl_meaning['qfmt'] = util.load_template_file('kdw', 'meaning', 'front', log) or ''
    tmpl_meaning['afmt'] = util.load_template_file('kdw', 'meaning', 'back', log) or ''
    tmpl_meaning['bqfmt'] = tmpl_meaning['qfmt']
    tmpl_meaning['bafmt'] = tmpl_meaning['afmt']
    col.models.addTemplate(model, tmpl_meaning)
    col.save()

    return model, col.decks.get(deck_id)


# mergest the word databases created from kd and tangorin
# the result will be a list of tuples (kanji, [word entries])
# each word entry will be {
#      'word': <in kanji>,
#      'prefix': <prefix like wo or ga or empty>,
#      'suffix': <suffix, like 'xxxx', de, ni or empty>,
#      'furigana': <reading>,
#      'meaning': <meaning>,
#      'sort': <precedence of this word for this kanji, float, always present>,
#      'sort2': <second level of precedence of this word for this kanji (sort is the same), float, may be absent>,
# }
def kdw_merge_kd_tg(kanjis_ordered, kd_kanji_to_words, tg_kanji_to_words, word_freq):
    result = []

    # makes a deep copy of a word entry from either database
    def copy_entry(e):
        new_e = copy.deepcopy(e)
        new_e['prefix'] = e.get('prefix', '')
        new_e['suffix'] = e.get('suffix', '')
        return new_e

    # checks if a tangorin entry's word matches the word in a kanji damage entry
    # if it's the special case of 'お' prefix, updates the kanji_damage word
    def word_match(kde, tge):
        if kde['word'] == tge['word']:
            return True
        if kde['prefix'] == 'お':
            honorific = 'お' + kde['word']
            if honorific == tge:
                kde['prefix'] = ''
                kde['word'] = honorific
                return True
        return False

    for kanji in kanjis_ordered:
        entries = []

        # add all kd entries using negative numbers for the sorting order
        kd_entries = kd_kanji_to_words[kanji]
        sort1 = -len(kd_entries)
        while sort1 < 0:
            entry = copy_entry(kd_entries[sort1])
            entry['sort'] = sort1
            entries.append(entry)
            sort1 += 1

        # now adds tangorin words
        for reading, tg_entries in tg_kanji_to_words[kanji].items():
            sort2 = 2
            for tg_entry in tg_entries:
                entry = next((e for e in entries if word_match(e, tg_entry)), None)
                if entry:  # repeated?
                    if 'sort2' not in entry:
                        entry['meaning'] = '<p>' + tg_entry['meaning'] + '</p>' + entry['meaning']
                        entry['sort2'] = sort2
                else:
                    entry = copy_entry(tg_entry)
                    entry['sort'] = sort1
                    entry['sort2'] = (1 - word_freq[entry['word']]) if entry['word'] in word_freq else sort2
                    entries.append(entry)
                sort2 += 1
            sort1 += 1
        result.append((kanji, entries))

    return result


# removes the previous 'kanji damage words' deck if it exists and creates
# a new one
def kdw_create(col, kd):
    # word frequency handling
    word_freq = load_word_freq('word-freq.txt')  # {word : frequency in [0,1]}
    kd_kanji_to_words = kd.get_kanji_to_words()
    kanjis_ordered = kd.get_kanjis_ordered()  # [kanji characters, ordered by due date]
    tg_kanji_to_words = tg.get_kanji_to_words(TG_FILE, kanjis_ordered, log)  # {kanji : {reading : [words sorted by appearance]}}
    kanji_words = kdw_merge_kd_tg(kanjis_ordered, kd_kanji_to_words, tg_kanji_to_words, word_freq)

    final_entries = []
    take_n = 1
    for (kanji, words) in kanji_words:
        # all required words have negative 'sort' values
        main_words = sorted([w for w in words if w['sort'] < 0], key=itemgetter('sort'))
        # now takes the ones with higher precedence from the other words
        sort1_keys = set(map(lambda x: x['sort'], words))
        word_groups = [
            sorted([word for word in words if word['sort'] == key], key=itemgetter('sort2'))
            for key in sort1_keys if key >= 0
        ]
        # puts all of them together
        final_entries += main_words
        for group in word_groups:
            final_entries += [group[i] for i in range(0, take_n) if i < len(group)]  # take up to take_n first words

    with codecs.open('entries.json', 'wb', encoding='utf-8') as f:
        json.dump(final_entries, f, ensure_ascii=False, indent=4, sort_keys=True)

    log.info("creating '%s' deck", KDW_DECK)
    kdw_model, kdw_deck = kdw_reset_model_and_deck(col)
    col.models.setCurrent(kdw_model)
    col.decks.select(kdw_deck['id'])
    col.conf['nextPos'] = 1

    def create_affix_tag(affix):
        return '(<span class="particles">' + affix + '</span>)' if affix else ''

    log.info('%d word candidates will be processed', len(final_entries))
    notes = {}
    for entry in final_entries:
        if entry['word'] in notes:
            continue
        note = col.newNote()
        prefix = create_affix_tag(entry['prefix'])
        suffix = create_affix_tag(entry['suffix'])
        note['Kanji'] = prefix + entry['word'] + suffix
        note['Furigana'] = prefix + entry['furigana'] + suffix
        note['Meaning'] = entry['meaning']
        note['Examples'] = ''
        col.addNote(note)
        notes[entry['word']] = note
    col.save()
    log.info('%d notes were created', len(notes))
    return kdw_model, kdw_deck


##########################################
# The script.
##########################################
def main():
    # opens the collection
    log.info('open collection: %s', options.file)
    cwd = os.getcwd()
    col = anki.Collection(path=options.file)
    work_dir = os.getcwd()
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
    _, kdw_deck = kdw_create(col, kd)

    log.info('writing output file %s...', options.output)
    exporter = AnkiPackageExporter(col)
    exporter.includeSched = False
    exporter.includeMedia = True
    exporter.includeTags = True
    exporter.did = kdw_deck['id']

    out_path = os.path.join(os.getcwd(), options.output)
    os.chdir(work_dir)
    exporter.exportInto(out_path)
    log.info('all is well!')
    col.close()


if __name__ == '__main__':
    main()
