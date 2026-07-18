from __future__ import annotations

from importlib.resources import files


class AdvancementTranslator:
    def __init__(self, translations: dict[str, str]) -> None:
        self._translations = translations

    @classmethod
    def load(cls) -> AdvancementTranslator:
        resource = files("mc_bot.resources").joinpath("advancements-ja.tsv")
        translations: dict[str, str] = {}
        with resource.open("r", encoding="utf-8") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                original, separator, translated = line.partition("\t")
                if not separator:
                    raise ValueError(f"Invalid advancement translation line: {line}")
                translations[original] = translated
        return cls(translations)

    def translate(self, original_title: str) -> str:
        return self._translations.get(original_title, original_title)

    def __len__(self) -> int:
        return len(self._translations)
