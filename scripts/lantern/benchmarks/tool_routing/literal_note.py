"""Conservative literal extraction after a save intent, never authorization."""
import re

# Full matches keep quoted examples, negations, unrelated prefixes, and compound
# actions out. Other phrasings retain the generated proposal without repair.
PATTERNS = [
    r"(?:Save a private note|Create a private note) (?:titled|with title) '(?P<title>[^'\n]+)' (?:with exact text|containing exactly|and exact body) '(?P<text>[^\n]+)'",
    r"(?:Record this in private memory|Store this note privately): title '(?P<title>[^'\n]+)'; (?:contents|text) '(?P<text>[^\n]+)'",
    r"I want you to save a private note now\. Name it '(?P<title>[^'\n]+)' and copy this exact text: '(?P<text>[^\n]+)'",
    r"Remember a new private note with title '(?P<title>[^'\n]+)' and exact body '(?P<text>[^\n]+)'",
    r"Create this note privately: '(?P<title>[^'\n]+)', text '(?P<text>[^\n]+)'",
    r"Please make a private note\. Title '(?P<title>[^'\n]+)'; copy the text '(?P<text>[^\n]+)' exactly\.",
    r"Save one personal note named '(?P<title>[^'\n]+)' containing exactly '(?P<text>[^\n]+)'",
    r"(?:Simpan catatan peribadi bertajuk|Simpan nota bertajuk) '(?P<title>[^'\n]+)'(?:, teks tepat| dengan teks tepat) '(?P<text>[^\n]+)'",
    r"Catat nota peribadi sekarang\. Tajuk '(?P<title>[^'\n]+)'; teks tepat '(?P<text>[^\n]+)'",
    r"(?:احفظ ملاحظة خاصة بعنوان|أضف ملاحظة خاصة الآن بعنوان) '(?P<title>[^'\n]+)' (?:ونصها بالضبط|ومحتواها حرفيا) '(?P<text>[^\n]+)'",
]


def literal_note(question):
    matches=[m.groupdict() for pattern in PATTERNS if (m:=re.fullmatch(pattern,question))]
    if len(matches)!=1 or not matches[0]["text"].strip():
        return None
    return matches[0]
