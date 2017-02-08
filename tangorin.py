import codecs
import json
import util


TG_BASE_URL = 'http://tangorin.com'
TG_KANJI_PATH = '/kanji'


class Tangorin:
    # for each kanji on the list, loads word examples from tangorin website
    # and returns a map {kanji : {reading : [words sorted by appearance]}}
    @staticmethod
    def get_kanji_to_words(cache_file, kanjis, log):
        log.info('loading tangorin words')
        kanji_to_words = {}
        try:
            with codecs.open(cache_file, 'rb', 'utf-8') as f:
                kanji_to_words = json.load(f)
                log.info('loaded from cache file %s', cache_file)
        except (FileNotFoundError, IOError):
            pass

        i = 1
        for kanji in kanjis:
            if kanji not in kanji_to_words:
                kanji_to_words[kanji] = Tangorin._get_words_for_kanji(kanji, log)
            log.debug('[%d/%d] %s: %s', i, len(kanjis), kanji, str(kanji_to_words[kanji]))
            i += 1
        try:
            with codecs.open(cache_file, 'wb', 'utf-8') as f:
                json.dump(kanji_to_words, f, ensure_ascii=False)
            log.info('saved cache file %s', cache_file)
        except IOError:
            pass
        return kanji_to_words

    # given one kanji, uses tangorin to find example words
    # and returns a map {kanji : {reading : [words sorted by appearance]}}
    # a word is {'word': <in kanji>, 'furigana': <kana>, 'meaning': <meaning>}
    @staticmethod
    def _get_words_for_kanji(kanji, log):
        try:
            doc = util.get_html(TG_BASE_URL + TG_KANJI_PATH + '/' + kanji, log)
            if doc is not None:
                kanji_words = {}
                for tr in doc.xpath('//table[@class="k-compounds-table"]//tr'):
                    reading, reading_words = Tangorin._process_reading_row(tr)
                    if not reading:
                        log.debug('invalid kanji: %s', kanji)
                        return None
                    kanji_words[reading] = reading_words
                return kanji_words
        except:
            log.exception('failed to load words for kanji %s', kanji)
        return None

    # processes one row from the tangorin page main table
    @staticmethod
    def _process_reading_row(tr):
        words = []
        reading = str(tr.xpath('.//td[1]/span[@class="kana"]/b')[0].text).strip()
        for a in tr.xpath('.//td[2]/a'):
            word = ''.join(a.xpath('.//text()')).strip()
            furigana = a.xpath('(./following-sibling::span[@class="kana"])[1]')[0].text.strip()
            meaning = a.xpath('(./following-sibling::span[@class="romaji"])[1]')[0].tail.replace(u'】', '').strip()
            words.append({'word': word, 'furigana': furigana, 'meaning': meaning})
        return reading, words
