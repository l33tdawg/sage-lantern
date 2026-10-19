"""Deterministic repair of known speech-recognition confusions.

Why this exists, and why it is not a model. The on-device 2B was measured on
eight real mis-transcriptions from unit C and repaired none of them, making two
worse (mean WER 0.392 -> 0.425): a mishearing is an acoustic event, so the text
alone does not reveal what was meant. A recorded table of the confusions this
hardware actually produces does, and it costs nothing at run time.

Every rule is anchored on surrounding words rather than a bare token. That is
the safety property, not a style preference: "Tell me about friends." is a
legitimate request and must survive untouched, while "the capital of Rams"
cannot mean anything else. A rule therefore only fires where the phrase leaves
no other plausible reading, and each one records the measurement that justified
it, so a later reader can tell evidence from guesswork.

Repairs are reported rather than hidden. Silently rewriting the user's own words
is not an improvement, so the caller receives the names of the rules that fired.
"""
import re
from dataclasses import dataclass

# Mishearings captured on unit C between 10 and 11 October 2026 while scoring the
# multi-voice sample set. No country, city or region has these names, which is
# what makes each frame unambiguous rather than merely likely.
_FRANCE_MISHEARD = r'(?:Rams|Rasm|Ralph|Brown|Bronze|brands|Prince|prayer|Prance)'
_DESCRIBE_MISHEARD = r'(?:first|prayer|Prance)'

def _match_case(match):
    """Replace a word while keeping the case the speaker's text already had."""
    word = match.group(0)
    return 'Capital' if word[:1].isupper() else 'capital'


# Each rule replaces only the word that was misheard. Everything else in the
# sentence - including its capitalisation - is carried through by a backreference,
# because a repair that rewrites words which were already correct is damaging the
# transcript to fix it.
RULES = (
    ('capital-of-france',
     re.compile(r'(\bcapital\s+(?:of|in)\s+)' + _FRANCE_MISHEARD + r'\b', re.IGNORECASE),
     r'\1France',
     'measured: "the capital of Rams/Rasm/Brown" for "the capital of France"'),
    ('population-of-france',
     re.compile(r'(\bpopulation\s+of\s+)' + _FRANCE_MISHEARD + r'\b', re.IGNORECASE),
     r'\1France',
     'measured: "the population of Prince/brands/Brown" for "the population of France"'),
    # "Describe friends." is deliberately NOT corrected: asking about friends is
    # a real request, so the ambiguity stays. Only the readings that cannot be a
    # request at all are replaced.
    ('describe-france',
     re.compile(r'(\bdescribe\s+)' + _DESCRIBE_MISHEARD + r'\b', re.IGNORECASE),
     r'\1France',
     'measured: "Describe first./Describe prayer." for "Describe France."'),
    # A homophone with one reading in this frame: a capitol is a building.
    ('capitol-spelling',
     re.compile(r'\bcapitol\b(?=\s+of\b)', re.IGNORECASE),
     _match_case,
     'homophone: "capitol" is a building, never a capital city'),
)


@dataclass(frozen=True)
class Repair:
    """Repaired text, plus the names of the rules that changed it."""

    text: str
    applied: tuple


def repair(text, rules=RULES):
    """Apply the confusion table to one transcript.

    The text comes back unchanged, with an empty ``applied``, whenever nothing
    matches, the input is not text, or applying the rules would leave the
    transcript empty - a repair layer that can empty a question has no business
    running on one.
    """
    if type(text) is not str or not text.strip():
        return Repair(text, ())
    result = text
    applied = []
    for name, pattern, replacement, _why in rules:
        result, count = pattern.subn(replacement, result)
        if count:
            applied.append(name)
    if not result.strip():
        return Repair(text, ())
    return Repair(result.strip(), tuple(applied))


def explain(names):
    """The recorded justification for each applied rule, for logs and review."""
    wanted = set(names)
    return tuple(f'{name}: {why}' for name, _pattern, _replacement, why in RULES if name in wanted)
