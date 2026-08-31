from langdetect import LangDetectException, detect

DEFAULT_AI_PROMPT_TEMPLATE = """You are a professional {SOURCE_LANG} ({SOURCE_CODE}) to {TARGET_LANG} ({TARGET_CODE}) translator. Your goal is to accurately convey the meaning and nuances of the original {SOURCE_LANG} text while adhering to {TARGET_LANG} grammar, vocabulary, and cultural sensitivities.
Produce only the {TARGET_LANG} translation, without any additional explanations or commentary. Please translate the following {SOURCE_LANG} text into {TARGET_LANG}:


{TEXT}"""

PROMPT_PLACEHOLDER_GUIDE = """Available placeholders:
• {TEXT} — the chat message to translate
• {SOURCE_LANG} — full source language name (e.g. Japanese)
• {SOURCE_CODE} — source language code (e.g. JA)
• {TARGET_LANG} — full target language name (e.g. English)
• {TARGET_CODE} — target language code (e.g. EN)
• {SOURCE} — source language from settings (or detected code when set to auto)
• {TARGET} — target language code from settings"""

DEFAULT_OLLAMA_MODEL = "gemma3:4b"

# ISO 639-1 codes → (display name, uppercase code)
LANG_NAMES = {
    "en": ("English", "EN"),
    "ja": ("Japanese", "JA"),
    "zh": ("Chinese", "ZH"),
    "zh-cn": ("Chinese (Simplified)", "ZH"),
    "zh-tw": ("Chinese (Traditional)", "ZH"),
    "es": ("Spanish", "ES"),
    "fr": ("French", "FR"),
    "ar": ("Arabic", "AR"),
    "ru": ("Russian", "RU"),
    "pt": ("Portuguese", "PT"),
    "de": ("German", "DE"),
    "ko": ("Korean", "KO"),
    "it": ("Italian", "IT"),
    "hi": ("Hindi", "HI"),
    "tr": ("Turkish", "TR"),
    "vi": ("Vietnamese", "VI"),
    "th": ("Thai", "TH"),
    "id": ("Indonesian", "ID"),
    "ms": ("Malay", "MS"),
    "nl": ("Dutch", "NL"),
    "pl": ("Polish", "PL"),
    "sv": ("Swedish", "SV"),
    "no": ("Norwegian", "NO"),
    "da": ("Danish", "DA"),
    "fi": ("Finnish", "FI"),
    "cs": ("Czech", "CS"),
    "el": ("Greek", "EL"),
    "he": ("Hebrew", "HE"),
    "hu": ("Hungarian", "HU"),
    "ro": ("Romanian", "RO"),
    "uk": ("Ukrainian", "UK"),
    "bg": ("Bulgarian", "BG"),
    "sr": ("Serbian", "SR"),
    "hr": ("Croatian", "HR"),
    "sk": ("Slovak", "SK"),
    "sl": ("Slovenian", "SL"),
    "lt": ("Lithuanian", "LT"),
    "lv": ("Latvian", "LV"),
    "et": ("Estonian", "ET"),
    "fa": ("Persian", "FA"),
    "ur": ("Urdu", "UR"),
    "bn": ("Bengali", "BN"),
    "ta": ("Tamil", "TA"),
    "te": ("Telugu", "TE"),
    "auto": ("Auto-detected", "AUTO"),
    "unknown": ("Unknown", "UNK"),
}


def _normalize_code(code: str) -> str:
    return (code or "unknown").lower().strip()


def _lang_info(code: str) -> tuple[str, str]:
    normalized = _normalize_code(code)
    if normalized in LANG_NAMES:
        return LANG_NAMES[normalized]
    base = normalized.split("-")[0]
    if base in LANG_NAMES:
        return LANG_NAMES[base]
    upper = base.upper()
    return (upper, upper)


def _detect_source_lang(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"


def resolve_source_lang(text: str, source_lang: str) -> str:
    if source_lang and _normalize_code(source_lang) != "auto":
        return _normalize_code(source_lang)
    return _detect_source_lang(text)


def render_prompt(
    template: str,
    text: str,
    source_lang: str,
    target_lang: str,
) -> str:
    resolved_source = resolve_source_lang(text, source_lang)
    source_name, source_code = _lang_info(resolved_source)
    target_name, target_code = _lang_info(target_lang)

    replacements = {
        "{TEXT}": text,
        "{SOURCE_LANG}": source_name,
        "{SOURCE_CODE}": source_code,
        "{TARGET_LANG}": target_name,
        "{TARGET_CODE}": target_code,
        "{SOURCE}": resolved_source,
        "{TARGET}": _normalize_code(target_lang),
    }

    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result
