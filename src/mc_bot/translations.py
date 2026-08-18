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


class MinecraftItemTranslator:
    def __init__(self, translations: dict[str, str]) -> None:
        self._translations = translations

    @classmethod
    def load(cls) -> MinecraftItemTranslator:
        resource = files("mc_bot.resources").joinpath("items-ja.tsv")
        translations: dict[str, str] = {}
        with resource.open("r", encoding="utf-8") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                item_id, separator, translated = line.partition("\t")
                if not separator:
                    raise ValueError(f"Invalid item translation line: {line}")
                translations[item_id] = translated
        return cls(translations)

    def translate(self, item_id: str, supplied_name: str) -> str:
        default_name = item_id.removeprefix("minecraft:").replace("_", " ")
        if supplied_name != default_name:
            return supplied_name
        return self._translations.get(item_id, supplied_name)

    def __len__(self) -> int:
        return len(self._translations)
