from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

_MATERIAL_ITEM_ID = re.compile(r"minecraft:[a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class MinecraftItemOption:
    item_id: str
    name: str
    english_name: str = ""


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
    def __init__(
        self,
        translations: dict[str, str],
        english_translations: dict[str, str] | None = None,
    ) -> None:
        self._translations = translations
        self._english_translations = english_translations or {}

    @classmethod
    def load(cls) -> MinecraftItemTranslator:
        resources = files("mc_bot.resources")
        translations = _load_item_translations(resources.joinpath("items-ja.tsv"))
        english_translations = _load_item_translations(resources.joinpath("items-en.tsv"))
        return cls(translations, english_translations)

    def translate(self, item_id: str, supplied_name: str) -> str:
        default_name = item_id.removeprefix("minecraft:").replace("_", " ")
        if supplied_name != default_name:
            return supplied_name
        return self._translations.get(item_id, supplied_name)

    def search(self, query: str, *, limit: int = 25) -> list[MinecraftItemOption]:
        normalized_query = _normalize_search_text(query)
        if not normalized_query or limit <= 0:
            return []
        tokens = normalized_query.split()
        matches: list[tuple[tuple[int, int, str, str], MinecraftItemOption]] = []
        for item_id, name in self._translations.items():
            if _MATERIAL_ITEM_ID.fullmatch(item_id) is None:
                continue
            normalized_name = _normalize_search_text(name)
            english_name = self._english_translations.get(
                item_id, _default_english_item_name(item_id)
            )
            normalized_english_name = _normalize_search_text(english_name)
            normalized_id = _normalize_search_text(item_id.removeprefix("minecraft:"))
            normalized_full_id = _normalize_search_text(item_id)
            searchable = (
                f"{normalized_name} {normalized_english_name} {normalized_id} {normalized_full_id}"
            )
            if not all(token in searchable for token in tokens):
                continue
            if normalized_query in (
                normalized_name,
                normalized_english_name,
                normalized_id,
                normalized_full_id,
            ):
                rank = 0
            elif normalized_name.startswith(normalized_query) or normalized_english_name.startswith(
                normalized_query
            ):
                rank = 1
            elif normalized_id.startswith(normalized_query):
                rank = 2
            elif normalized_query in normalized_name or normalized_query in normalized_english_name:
                rank = 3
            else:
                rank = 4
            option = MinecraftItemOption(
                item_id=item_id,
                name=name,
                english_name=english_name,
            )
            matches.append(((rank, len(normalized_name), normalized_name, normalized_id), option))
        matches.sort(key=lambda match: match[0])
        return [option for _, option in matches[:limit]]

    def is_exact_match(self, query: str, option: MinecraftItemOption) -> bool:
        normalized = _normalize_search_text(query)
        return normalized in {
            _normalize_search_text(option.name),
            _normalize_search_text(option.english_name),
            _normalize_search_text(option.item_id),
            _normalize_search_text(option.item_id.removeprefix("minecraft:")),
        }

    def __len__(self) -> int:
        return len(self._translations)


def _normalize_search_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().strip().split())


def _default_english_item_name(item_id: str) -> str:
    return item_id.removeprefix("minecraft:").replace("_", " ").title()


def _load_item_translations(resource: Traversable) -> dict[str, str]:
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
    return translations


__all__ = [
    "AdvancementTranslator",
    "MinecraftItemOption",
    "MinecraftItemTranslator",
]
