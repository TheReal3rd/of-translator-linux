import re

from langdetect import LangDetectException, detect_langs, DetectorFactory

DetectorFactory.seed = 0

_NON_ENGLISH_RE = re.compile(
    r"[\u3040-\u30ff"
    r"\u4e00-\u9fff"
    r"\u3400-\u4dbf"
    r"\uac00-\ud7af"
    r"\u0400-\u04ff"
    r"\u0600-\u06ff"
    r"\u0590-\u05ff"
    r"\u0e00-\u0e7f"
    r"\u0900-\u097f"
    r"]"
)

DETECT_MIN_CONFIDENCE = 0.9


def _normalize_lang(code: str) -> str:
    return (code or "en").lower().split("-")[0]


def has_non_english_script(text: str) -> bool:
    return bool(_NON_ENGLISH_RE.search(text))


def is_target_language(
    text: str,
    target_lang: str,
    min_confidence: float = DETECT_MIN_CONFIDENCE,
) -> bool:
    try:
        langs = detect_langs(text)
    except LangDetectException:
        return False

    if not langs:
        return False

    top = langs[0]
    return (
        _normalize_lang(top.lang) == _normalize_lang(target_lang)
        and top.prob >= min_confidence
    )
