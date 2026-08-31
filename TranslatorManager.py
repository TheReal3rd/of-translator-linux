import asyncio
import logging
from enum import Enum

from googletrans import Translator
from libretranslatepy import LibreTranslateAPI

from HailoAPI import send_chat_stream
from lang_utils import has_non_english_script, is_target_language
from prompt_templates import render_prompt

logger = logging.getLogger(__name__)


class TranslatorsMode(Enum):
    LibreTranslate = 0
    GoogleTranslate = 1
    HailoAITranslate = 2
    OllamaAITranslate = 3


class TranslatorManager:
    def __init__(self, config_data: dict):
        self.client = None
        self._google: Translator | None = None
        self._apply_config(config_data)

    def _apply_config(self, config_data: dict):
        self.config = config_data
        self.currentMode = config_data["translatorMode"]
        self.sourceLang = config_data["sourceLang"]
        self.targetLang = config_data["targetLang"]
        self.libreURL = config_data["libreTranslateURL"]
        self.apiKey = config_data.get("apiKey") or None
        self.aiIP = config_data["aiIP"]
        self.aiModel = config_data["model"]
        self.aiPrompt = config_data["modelPrompt"]
        self.aiPort = config_data.get("aiPort")
        self.client = None

    def update_config(self, config_data: dict):
        self._apply_config(config_data)

    async def close(self):
        if self._google is not None:
            await self._google.__aexit__(None, None, None)
            self._google = None

    def _ai_port(self, default: int) -> int:
        if self.aiPort not in (None, ""):
            return int(self.aiPort)
        return default

    def _ai_prompt(self, text: str) -> str:
        return render_prompt(self.aiPrompt, text, self.sourceLang, self.targetLang)

    def _should_translate(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if has_non_english_script(text):
            return True
        if not any(c.isalpha() for c in text):
            return False
        return not is_target_language(text, self.targetLang)

    async def _get_google_translator(self) -> Translator:
        if self._google is None:
            self._google = Translator()
            await self._google.__aenter__()
        return self._google

    async def translateText(self, text: str) -> str:
        if not text or not text.strip():
            return text

        if not self._should_translate(text):
            logger.debug("Skipping translation — already in target language")
            return text

        try:
            match self.currentMode:
                case TranslatorsMode.LibreTranslate.name:
                    if self.client is None:
                        self.client = LibreTranslateAPI(
                            self.libreURL,
                            api_key=self.apiKey,
                        )
                    return self.client.translate(
                        text,
                        source=self.sourceLang,
                        target=self.targetLang,
                    )

                case TranslatorsMode.GoogleTranslate.name:
                    translator = await self._get_google_translator()
                    result = await translator.translate(text, dest=self.targetLang)
                    return result.text

                case TranslatorsMode.HailoAITranslate.name:
                    port = self._ai_port(8000)
                    result = send_chat_stream(
                        self.aiIP,
                        self.aiModel,
                        self._ai_prompt(text),
                        port=port,
                    )
                    if not result:
                        logger.error(
                            "AI translation empty (model=%s, server=%s:%s)",
                            self.aiModel,
                            self.aiIP,
                            port,
                        )
                    return result or text

                case TranslatorsMode.OllamaAITranslate.name:
                    port = self._ai_port(11434)
                    result = send_chat_stream(
                        self.aiIP,
                        self.aiModel,
                        self._ai_prompt(text),
                        port=port,
                    )
                    if not result:
                        logger.error(
                            "AI translation empty (model=%s, server=%s:%s)",
                            self.aiModel,
                            self.aiIP,
                            port,
                        )
                    return result or text

                case _:
                    logger.warning("Unknown translator mode: %s", self.currentMode)
                    return text
        except Exception:
            logger.exception("Translation failed (%s)", self.currentMode)
            return text
