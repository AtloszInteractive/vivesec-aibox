"""Put the accents back on a query, using the corpus's own vocabulary.

Users type Hungarian without accents. Measured on the production index, that is
not a degradation but a failure: "utazasi koltsegterites napidij" scores 0.350
where "utazási költségtérítés napidíj" scores 0.715, so every hit falls under
the relevance floor and the box answers "no data". German and Danish barely
move (0.02), because ue/oe/aa are established spellings that the embedding
model has seen; Hungarian accent loss instead changes vowel identity (a/á,
o/ó/ö/ő), which turns the words into nonsense for the model.

The repair needs no dictionary: the corpus already contains the words spelled
correctly. Fold every corpus word to its accent-less form, and a query token
that folds onto a corpus word -- but is not itself a corpus word -- is that
word with its accents dropped.

Matching is by longest common folded prefix, not by equality, because Hungarian
is agglutinative: the corpus has "költségtérítési", the user types
"koltsegterites". One word must be a prefix of the other, so that words which
only share an opening are left alone; accents are copied over the shared part
and the rest of the token is kept as typed.
"""
import bisect
import collections
import re
import unicodedata

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# NFD leaves these alone, so they need spelling out. Danish ø/æ and German ß
# fold to what a user without the key would type.
_EXTRA_FOLD = {
    "ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l", "ð": "d", "þ": "th",
}

# Below this many shared characters a prefix match is a coincidence, not a word.
MIN_PREFIX = 5
# ...and it has to cover most of the token, or "kulfoldi" would be repaired
# from any corpus word starting with "kulf".
MIN_PREFIX_RATIO = 0.6
# A folded form with several accented spellings is only repaired if one of them
# clearly dominates; otherwise we would be guessing.
MIN_DOMINANCE = 0.6
# How far to look back for the longest corpus word the token starts with.
_SCAN_BACK = 64


def fold(text):
    """The accent-less spelling of `text`, lowercased."""
    out = []
    for ch in unicodedata.normalize("NFD", text.lower()):
        if unicodedata.combining(ch):
            continue
        out.append(_EXTRA_FOLD.get(ch, ch))
    return "".join(out)


def _transfer(spelling, token):
    """Copy the accents of `spelling` onto `token` over their shared prefix."""
    out = []
    i = 0
    for ch in spelling:
        folded = fold(ch)
        if token[i:i + len(folded)].lower() != folded:
            break
        out.append(ch)
        i += len(folded)
    if i < MIN_PREFIX or i < MIN_PREFIX_RATIO * len(token):
        return token
    repaired = "".join(out) + token[i:]
    if token[:1].isupper():
        repaired = repaired[:1].upper() + repaired[1:]
    return repaired


class Vocabulary(object):
    """The accented words of one corpus, indexed by their accent-less form."""

    def __init__(self):
        self._spellings = collections.defaultdict(collections.Counter)
        self._folded = []            # sorted accent-less forms of accented words
        self._accented = []          # their spellings, same order
        self._dirty = False

    def add_text(self, text):
        for word in WORD_RE.findall(text or ""):
            word = word.lower()
            if len(word) < MIN_PREFIX:
                continue
            self._spellings[fold(word)][word] += 1
        self._dirty = True

    def _compile(self):
        pairs = []
        for folded, counter in self._spellings.items():
            spelling, count = counter.most_common(1)[0]
            if spelling == folded:
                continue                      # nothing to put back
            if count < MIN_DOMINANCE * sum(counter.values()):
                continue                      # ambiguous spelling
            pairs.append((folded, spelling))
        pairs.sort()
        self._folded = [p[0] for p in pairs]
        self._accented = [p[1] for p in pairs]
        self._dirty = False

    def __len__(self):
        if self._dirty:
            self._compile()
        return len(self._accented)

    def _best_match(self, token):
        """The accented corpus word that contains `token` or is contained by it.

        One word has to be a prefix of the other. Where two words merely share
        an opening and then diverge, the match is a coincidence rather than the
        same word -- "europe" and "európai" share five letters and mean
        different things -- and rewriting one into the other would answer a
        question nobody asked.
        """
        at = bisect.bisect_left(self._folded, token)
        # Anything starting with the whole token sorts right after it, shortest
        # first, so the insertion point itself is the closest such word.
        if at < len(self._folded) and self._folded[at].startswith(token):
            return self._accented[at]
        # Otherwise take the longest corpus word that the token starts with.
        # Words sharing only an opening sit in between, so they are skipped.
        for i in range(at - 1, max(at - _SCAN_BACK, -1), -1):
            candidate = self._folded[i]
            if token.startswith(candidate):
                return self._accented[i]
            if candidate[:MIN_PREFIX] != token[:MIN_PREFIX]:
                break
        return None

    def repair(self, text):
        """Return `text` with the accents its words have in the corpus."""
        if self._dirty:
            self._compile()
        if not self._accented or not text:
            return text

        def repair_token(match):
            token = match.group(0)
            folded = fold(token)
            if folded != token.lower():
                return token                  # already accented
            if len(token) < MIN_PREFIX:
                return token
            counter = self._spellings.get(folded)
            if counter and counter.get(folded):
                return token                  # a real corpus word as typed
            spelling = self._best_match(folded)
            if not spelling:
                return token
            return _transfer(spelling, token)

        return WORD_RE.sub(repair_token, text)


def build(texts):
    """A Vocabulary over an iterable of chunk texts."""
    vocabulary = Vocabulary()
    for text in texts:
        vocabulary.add_text(text)
    return vocabulary
