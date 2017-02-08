##KanjiDamage for Anki - Word Flashcard Generator##

If you just want to download the pre-generated deck:

[KanjiDamage Words](KanjiDamageWords.apkg)


This is a simple python script that processes the [KanjiDamage](http://www.kanjidamage.com/) official deck for the
[Anki](https://apps.ankiweb.net/) flashcard system and generates a new deck 'KanjiDamage Words' based on all the
Kunyomi and Jukugo for those Kanji and also extra examples from [Tangorin](http://tangorin.com/) website.

This is a free software and has no commercial purposes.

####Setup####

You'll need to clone or download the [anki module](https://github.com/dae/anki) and put the 'anki' folder on the
project root. If you don't have the KanjiDamage or 'KanjiDamage Reordered' deck on your anki collection, you'll need
to download those as well:
* for the kanji/radicals in the original order, use [KanjiDamage](https://ankiweb.net/shared/info/748570187)
* for the kanji/radicals reordered by frequency, use [KanjiDamage Reordered](https://ankiweb.net/shared/info/1917095458)

Either import one of those collections in your anki applications or call the script with the option `-r` and informing
the name of the file you downloaded with `-k`:

`anki-kanji.py -r -k <path to apkg file>`

#####Usage####

If the setup was done properly, the script needs no additional parameter. It accepts the following options:

* `-h, --help` - show this help message and exit
* `-f COLLECTION, --file COLLECTION` -  path to collection file (overwrites -p)
* `-p PROFILE, --profile PROFILE` - the Anki profile to be used (will use default Anki path on your system)
* `-o PATH, --output PATH` - KanjiDamage Words output file
* `-v, --verbose` - show detailed messages
* `-q, --quiet` - show no message
* `-r, --reset-kd` - reimports Kanji Damage deck into the collection
* `-k APKG, --kd-file APKG` - Kanji Damage deck file
* `-u, --update-kd` - updates Kanji Damage data from web
* `-d, --force-download` - forces to download all images again (clears local cache)
