import re
import os.path
import requests
import lxml
import lxml.html
import anki.importing
import util


KD_DECK_NAME = 'KanjiDamage'
KDR_DECK_NAME = 'KanjiDamage Reordered'
KD_MODEL = 'KanjiDamage'
KD_READ_TMPL = 'Read'
KD_VALID_KANJI = re.compile(util.KANJI_REGEX_STR + '|L|￥|(<<<)|丶|' + util.KATAKANA_REGEX_STR)
KD_NUMBER_STRIP_REGEX = re.compile('([a-zA-Z]|\s)+')
KD_DAMAGE_BASE_URL = 'http://www.kanjidamage.com'
KD_KANJI_PATH = '/kanji'
# don't touch these strings!
KD_KUN_BLANK = re.compile(r'\s+')
ORD_JAP_BASE = ord('！')
ORD_JAP_TOP = ord('～')
ORD_ASCII_BASE = ord('!')
ORD_JAP_SP = ord('　')


class KanjiDamage:
    def __init__(self, col, log):
        self.col = col
        self.log = log
        self.model = None
        self.deck = None

    def reset(self, path):
        self.log.info('removing previous Kanji Damage decks')
        util.remove_model_and_deck(self.col, KD_MODEL, KD_DECK_NAME, self.log)
        util.remove_model_and_deck(self.col, KD_MODEL, KDR_DECK_NAME, self.log)
        self.log.info('importing %s', path)
        importer = anki.importing.AnkiPackageImporter(self.col, path)
        importer.run()
        self.col.save()

    def get_model(self):
        if not self.model:
            self.model = self.col.models.byName(KD_MODEL)
        return self.model

    def get_deck(self):
        if not self.deck:
            self.deck = self.col.decks.byName(KD_DECK_NAME) or self.col.decks.byName(KDR_DECK_NAME)
        return self.deck

    # gets the kanjis ordered by 'Read' card due date
    def get_kanjis(self):
        kd_model = self.get_model()
        kd_deck = self.get_deck()
        nids = self.col.db.list(
            'select nid from cards where did={0} and ord={1} order by due'.format(
                kd_deck['id'],
                next((x['ord'] for x in kd_model['tmpls'] if x['name'] == KD_READ_TMPL))
            )
        )
        kanjis = []  # kanjis in order of due date
        for nid in nids:
            kanji = self.col.getNote(nid)['Kanji']
            if not util.KANJI_REGEX.match(kanji):
                continue
            kanjis.append(kanji)
        return kanjis

    def update(self):
        model = self.get_model()
        deck = self.get_deck()
        note_map = self.get_notes()

        model['did'] = deck['id']
        self._update_templates(model)

        # processes data from kd website
        self.log.info('loading data from kanji damage website (this will take quite a while)...')
        self.col.models.setCurrent(model)
        self.col.decks.select(deck['id'])
        url = KD_DAMAGE_BASE_URL + KD_KANJI_PATH + '/1'
        tries = 0
        while url:
            try:
                doc = util.get_html(url, self.log)
                if doc is None:
                    return
                util.add_base_url(doc, KD_DAMAGE_BASE_URL)

                # retrieves the data
                kanji = self._get_kanji(doc)
                meaning = self._get_meaning(doc)

                # get map key
                key = None
                if type(kanji) is lxml.html.HtmlElement:
                    # kanji is an image
                    self._download_images(kanji, KD_DAMAGE_BASE_URL)
                    kanji = util.html_to_string(kanji)
                    key = meaning
                elif KD_VALID_KANJI.match(kanji):
                    key = kanji

                # update/create note
                if key:
                    note = note_map[key] if key in note_map else self.col.newNote()
                    note['Kanji'] = kanji
                    note['Meaning'] = meaning
                    note['Number'] = self._get_number(doc)
                    note['Description'] = self._get_description(doc, KD_DAMAGE_BASE_URL)
                    note['Usefulness'] = self._get_usefulness(doc)
                    note['Full used In'] = self._get_used_in(doc, KD_DAMAGE_BASE_URL)
                    onyomi_full, onyomi = self._get_onyomi(doc, KD_DAMAGE_BASE_URL)
                    note['Full onyomi'] = onyomi_full
                    note['Onyomi'] = onyomi
                    kun_full, kun, kun_meaning, kun_use = self._get_kunyomi(doc, KD_DAMAGE_BASE_URL)
                    note['Full kunyomi'] = kun_full
                    note['First kunyomi'] = kun
                    note['First kunyomi meaning'] = kun_meaning
                    note['First kunyomi usefulness'] = kun_use
                    mnemonic_full, mnemonic = self._get_mnemonic(doc, KD_DAMAGE_BASE_URL)
                    note['Full mnemonic'] = mnemonic_full
                    note['Mnemonic'] = mnemonic
                    note['Components'] = self._get_components(doc, KD_DAMAGE_BASE_URL)
                    jk_full, jk, jk_meaning, jk_use = self._get_jukugo(doc, KD_DAMAGE_BASE_URL)
                    note['Full jukugo'] = jk_full
                    note['First jukugo'] = jk
                    note['First jukugo meaning'] = jk_meaning
                    note['First jukugo usefulness'] = jk_use
                    note['Full header'] = self._get_header(doc, KD_DAMAGE_BASE_URL)
                    note['Full lookalikes'] = self._get_lookalikes(doc, KD_DAMAGE_BASE_URL)

                    if key not in note_map:
                        self.col.addNote(note)
                        note_map[key] = note
                    else:
                        note.flush()
                    self.log.debug(util.note_to_json(note))
                else:
                    self.log.info('ignored kanji: %s', kanji)

                # finds the link to the next kanji
                url = next(iter(doc.xpath('//div[@class="span2 text-righted"]/a[1]/@href')), None)
                tries = 0
            except OSError as e:
                if (e.errno == 101) and (tries < 3):
                    tries += 1
                else:
                    self.log.exception('failed to retrieve from %s', url)
                    url = None
        self.col.save()

    # retrieves all notes in a map where the key is either the kanji character (if a valid KD kanji) or the meaning
    def get_notes(self, expr=KD_VALID_KANJI):
        kanjis_by_text = {}
        for note_id in self.col.models.nids(self.get_model()):
            note = self.col.getNote(note_id)
            key = note['Kanji'] if expr.match(note['Kanji']) else note['Meaning']
            if key in kanjis_by_text:
                raise KeyError('duplicate note key: {0}'.format(key))
            kanjis_by_text[key] = note
        return kanjis_by_text

    def _update_templates(self, kd_model):
        for tmpl in kd_model['tmpls']:
            for key, side in [('q', 'front'), ('a', 'back')]:
                field = key + 'fmt'
                tmpl[field] = util.load_template_file('kd', tmpl['name'], side, self.log) or ''
                tmpl['b' + field] = tmpl[field]

        # adds used tangorin css classes
        if not [l for l in kd_model['css'].splitlines() if l.strip().startswith('.k-sod')]:
            kd_model['css'] += '\n.k-sod {\n' \
                               '  line-height: 0;\n' \
                               '  padding: 4px 0;\n' \
                               '  margin: 5px 0;\n' \
                               '  zoom: 1.5;\n}'
        self.col.models.save(kd_model)

    # downloads a file inside collection's media path
    def _download_file(self, path, base_url, cached=True):
        sub_dir, fn = os.path.split(path)
        sub_dir = self.col.media.dir() + sub_dir
        if not os.path.exists(sub_dir):
            os.makedirs(sub_dir)
        local_path = os.path.join(sub_dir, fn)
        if cached and os.path.exists(local_path):
            self.log.debug('[cached]: %s', local_path)
        else:
            r = requests.get(base_url + path, stream=True)
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
            self.log.debug('[download]: %s', local_path)
        return self.col.media.addFile(local_path)

    def _download_images(self, doc, base_url):
        paths = []
        for img in doc.xpath('descendant-or-self::img'):
            src = img.get('src')
            if src.startswith('/'):
                src = self._download_file(src, base_url)
                img.set('src', src)
                paths.append(src)
        return os.pathsep.join(paths)

    def _nodes_to_string(self, node_list, base_url):
        nodes = []
        for e in node_list:
            if type(e) is lxml.html.HtmlElement:
                self._download_images(e, base_url)
                nodes.append(util.html_to_string(e))
            else:
                nodes.append(str(e))
        return ''.join(nodes).strip()

    # tries to extract the kd kanji number, otherwise raises
    @staticmethod
    def _get_number(doc):
        number = next(iter(doc.xpath('//div[@class="span8 text-centered"]/img[@alt="Flag"]')), None)
        number = number.tail if (number is not None) \
            else next(iter(doc.xpath('//div[@class="span8 text-centered"]/text()')), None)
        if number:
            number = KD_NUMBER_STRIP_REGEX.sub("", number)
            int(number)
        else:
            raise ValueError('couldn\'t extract number')
        return number

    # tries to extract the kd kanji, otherwise raises
    @staticmethod
    def _get_kanji(doc):
        kanji = next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/img[1]')), None)
        if kanji is not None:
            return kanji
        else:
            return str(next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/text()')))).strip()

    # tries to extract the kd kanji meaning, otherwise raises
    @staticmethod
    def _get_meaning(doc):
        return str(next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="translation"]/text()')))).strip()

    # tries to extract the kd kanji description, otherwise returns the empty string
    @staticmethod
    def _get_usefulness(doc):
        return str(
            next(iter(doc.xpath('//div[@class="span4 text-righted"]/span[@class="usefulness-stars"]/text()')), '')
        ).strip()

    # tries to extract the kd kanji description, otherwise returns the empty string
    def _get_description(self, doc, base_url):
        path = '(//div[@class="description"])[1]/node()'
        return self._nodes_to_string(doc.xpath(path), base_url)

    # tries to extract the kd kanji 'used in field', otherwise returns the empty string
    def _get_used_in(self, doc, base_url):
        path = '(//ul[@class="lacidar"])[1]'
        return self._nodes_to_string(doc.xpath(path), base_url)

    # tries to extract the kd kanji onyomi, otherwise returns the empty string
    def _get_onyomi(self, doc, base_url):
        table = next(iter(doc.xpath('(//h2[text()="Onyomi"]/following-sibling::table[@class="definition"])[1]')), None)
        if table is None:
            return '', ''
        content = str(next(iter(table.xpath('./tr[1]/td[1]/span[@class="onyomi"]/text()')), ''))
        full = self._nodes_to_string([table], base_url)
        return full, content

    # tries to extract the kd kanji kunyomi, otherwise returns the empty string
    def _get_kunyomi(self, doc, base_url):
        table = next(iter(doc.xpath('(//h2[text()="Kunyomi"]/following-sibling::table[@class="definition"])[1]')), None)
        if table is None:
            return '', '', '', ''
        full = self._nodes_to_string([table], base_url)
        kun = self._nodes_to_string(table.xpath('./tr[1]/td[1]/node()'), base_url)
        kun_meaning = str(next(iter(table.xpath('./tr[1]/td[2]/text()[1]')), '')).strip()
        kun_use = str(next(iter(table.xpath('./tr[1]/td[2]/span[@class="usefulness-stars"]/text()')), '')).strip()
        return full, kun, kun_meaning, kun_use

    # tries to extract the kd kanji mnemonic, otherwise returns the empty string
    def _get_mnemonic(self, doc, base_url):
        table = next(
            iter(doc.xpath('(//h2[text()="Mnemonic"]/following-sibling::table[@class="definition"])[1]')), None
        )
        if table is None:
            return '', ''
        content = self._nodes_to_string(table.xpath('./tr[1]/td[2]/p/node()'), base_url)
        full = self._nodes_to_string([table], base_url)
        return full, content

    # tries to extract the kd kanji components, otherwise returns the empty string
    def _get_components(self, doc, base_url):
        h1 = next(iter(doc.xpath('//div[@class="span8"]/h1/span[@class="kanji_character"]/..')), None)
        if h1 is not None:
            return self._nodes_to_string([h1.tail] + h1.xpath('./following-sibling::*'), base_url)
        return ''

    # tries to extract the kd kanji jukugo, otherwise returns the empty string
    def _get_jukugo(self, doc, base_url):
        table = next(iter(doc.xpath('(//h2[text()="Jukugo"]/following-sibling::table[@class="definition"])[1]')), None)
        if table is None:
            return '', '', '', ''
        full = self._nodes_to_string([table], base_url)
        jk = self._nodes_to_string(table.xpath('./tr[1]/td[1]/node()'), base_url)
        jk_meaning = str(next(iter(table.xpath('./tr[1]/td[2]/p/text()[1]')), '')).strip()
        jk_use = str(next(iter(table.xpath('./tr[1]/td[2]/p/span[@class="usefulness-stars"]/text()')), '')).strip()
        return full, jk, jk_meaning, jk_use

    # tries to extract the kd kanji full header, otherwise returns the empty string
    def _get_header(self, doc, base_url):
        path = '(//div[@class="span8"])[1]'
        return self._nodes_to_string(doc.xpath(path), base_url)

    # tries to extract the kd kanji lookalikes table, otherwise returns the empty string
    def _get_lookalikes(self, doc, base_url):
        nodes = []
        n = next(iter(doc.xpath('//h2[text()="Lookalikes"]')), None)
        if n is not None:
            nodes.append(n.tail)
            n = n.getnext()
            while (n is not None) and (n.tag != 'h2'):
                nodes.append(n)
                n = n.getnext()
        return self._nodes_to_string(nodes, base_url)

    # for each valid kanji character in the database, loads the word examples (kunyomi and jukugo)
    # and returns a map {kanji : [words sorted by appearance]}
    # a word is {'word': <in kanji>, 'furigana': <reading>, 'meaning': <meaning>}
    def get_kanji_to_words(self):
        import codecs
        f = codecs.open('words.csv', 'wb', encoding='utf-8')
        self.log.info('loading words from kanji damage')
        kanji_to_words = {}
        for kanji, note in self.get_notes(expr=util.KANJI_REGEX).items():
            kanji_to_words[kanji] = self._extract_kuyomis(note, f)
        f.close()
        return kanji_to_words

    def _extract_kuyomis(self, note, f):
        kun = note['Full kunyomi']
        words = []
        if kun:
            table = lxml.html.fromstring(kun)
            for row in table.xpath('//tr'):
                meaning = self._letter_sanitize(''.join(row.xpath('td[2]/text()'))).strip()
                word = ''.join([re.sub(KD_KUN_BLANK, '', self._letter_sanitize(node).lower()) for node in row.xpath('td[1]//text()')])
                parts = word.split('*')
                if parts:
                    prefix, word = self._kunyomi_break_prefix(parts[0])
                word = '\t'.join(parts)

                f.write('{0}\t{1}\n'.format(word, meaning))
                words.append({'word': word, 'meaning': meaning})
        return words

    def _kunyomi_break_prefix(self, word):
        return '', word

    def _letter_sanitize(self, word):
        chars = []
        for c in word:
            # japanese full width letters to ascii
            n = ord(c)
            if (n >= ORD_JAP_BASE) and (n <= ORD_JAP_TOP):
                chars.append(chr(ORD_ASCII_BASE + n - ORD_JAP_BASE))
            elif n == ORD_JAP_SP:
                chars.append(' ')
            else:
                chars.append(c)
        return ''.join(chars)
