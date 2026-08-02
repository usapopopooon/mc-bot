from __future__ import annotations

import re

_MOB_NAMES_JA = {
    "Allay": "アレイ",
    "Armadillo": "アルマジロ",
    "Axolotl": "ウーパールーパー",
    "Bat": "コウモリ",
    "Bee": "ミツバチ",
    "Blaze": "ブレイズ",
    "Bogged": "ボグド",
    "Breeze": "ブリーズ",
    "Camel": "ラクダ",
    "Cat": "ネコ",
    "Cave Spider": "洞窟グモ",
    "Chicken": "ニワトリ",
    "Cod": "タラ",
    "Cow": "ウシ",
    "Creaking": "クリーキング",
    "Creeper": "クリーパー",
    "Dolphin": "イルカ",
    "Donkey": "ロバ",
    "Drowned": "ドラウンド",
    "Elder Guardian": "エルダーガーディアン",
    "Ender Dragon": "エンダードラゴン",
    "Enderman": "エンダーマン",
    "Endermite": "エンダーマイト",
    "Evoker": "エヴォーカー",
    "Fox": "キツネ",
    "Frog": "カエル",
    "Ghast": "ガスト",
    "Giant": "ジャイアント",
    "Glow Squid": "ヒカリイカ",
    "Goat": "ヤギ",
    "Guardian": "ガーディアン",
    "Happy Ghast": "ハッピーガスト",
    "Hoglin": "ホグリン",
    "Horse": "ウマ",
    "Husk": "ハスク",
    "Illusioner": "イリュージョナー",
    "Iron Golem": "アイアンゴーレム",
    "Llama": "ラマ",
    "Magma Cube": "マグマキューブ",
    "Mooshroom": "ムーシュルーム",
    "Mule": "ラバ",
    "Ocelot": "ヤマネコ",
    "Panda": "パンダ",
    "Parrot": "オウム",
    "Phantom": "ファントム",
    "Pig": "ブタ",
    "Piglin": "ピグリン",
    "Piglin Brute": "ピグリンブルート",
    "Pillager": "ピリジャー",
    "Polar Bear": "シロクマ",
    "Pufferfish": "フグ",
    "Rabbit": "ウサギ",
    "Ravager": "ラヴェジャー",
    "Salmon": "サケ",
    "Sheep": "ヒツジ",
    "Shulker": "シュルカー",
    "Silverfish": "シルバーフィッシュ",
    "Skeleton": "スケルトン",
    "Skeleton Horse": "スケルトンホース",
    "Slime": "スライム",
    "Sniffer": "スニッファー",
    "Snow Golem": "スノウゴーレム",
    "Spider": "クモ",
    "Squid": "イカ",
    "Stray": "ストレイ",
    "Strider": "ストライダー",
    "Tadpole": "オタマジャクシ",
    "Trader Llama": "行商人のラマ",
    "Tropical Fish": "熱帯魚",
    "Turtle": "カメ",
    "Vex": "ヴェックス",
    "Villager": "村人",
    "Vindicator": "ヴィンディケーター",
    "Wandering Trader": "行商人",
    "Warden": "ウォーデン",
    "Witch": "ウィッチ",
    "Wither": "ウィザー",
    "Wither Skeleton": "ウィザースケルトン",
    "Wolf": "オオカミ",
    "Zoglin": "ゾグリン",
    "Zombie": "ゾンビ",
    "Zombie Horse": "ゾンビホース",
    "Zombie Villager": "村人ゾンビ",
    "Zombified Piglin": "ゾンビピグリン",
}

_EXACT_DEATHS = {
    "was pricked to death": "サボテンに刺されて死亡しました",
    "walked into a cactus": "サボテンに刺されて死亡しました",
    "drowned": "溺れました",
    "experienced kinetic energy": "壁に激突しました",
    "blew up": "爆発しました",
    "hit the ground too hard": "高い場所から落下しました",
    "fell from a high place": "高い場所から落下しました",
    "fell off a ladder": "はしごから落下しました",
    "fell off some vines": "ツタから落下しました",
    "fell off some weeping vines": "しだれツタから落下しました",
    "fell off some twisting vines": "ねじれツタから落下しました",
    "fell off scaffolding": "足場から落下しました",
    "fell while climbing": "登っている途中で落下しました",
    "was impaled on a stalagmite": "石筍に串刺しにされました",
    "was squashed by a falling anvil": "落下した金床に押し潰されました",
    "was skewered by a falling stalactite": "落下した鍾乳石に串刺しにされました",
    "went up in flames": "炎に包まれました",
    "walked into fire": "炎に包まれました",
    "burned to death": "焼け死にました",
    "was burned to a crisp": "焼け死にました",
    "went off with a bang": "爆発しました",
    "tried to swim in lava": "溶岩に落ちました",
    "was struck by lightning": "雷に打たれました",
    "discovered the floor was lava": "熱い床の上で焼け死にました",
    "walked into danger zone": "熱い床の上で焼け死にました",
    "was killed by magic": "魔法で死亡しました",
    "froze to death": "凍死しました",
    "was stung to death": "ハチに刺されて死亡しました",
    "was obliterated by a sonically-charged shriek": "衝撃波で死亡しました",
    "starved to death": "餓死しました",
    "suffocated in a wall": "壁の中で窒息しました",
    "was squished too much": "押し潰されました",
    "left the confines of this world": "奈落へ落ちました",
    "fell out of the world": "奈落へ落ちました",
    "withered away": "衰弱して死亡しました",
    "was poked to death by a sweet berry bush": "スイートベリーの茂みに刺されました",
    "died from dehydration": "乾燥して死亡しました",
    "was killed by even more magic": "強力な魔法で死亡しました",
    "was roasted in dragon breath": "ドラゴンブレスで焼かれました",
    "died": "死亡しました",
}

_ATTACKER_DEATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"was slain by (.+?)(?: using \[.+])?"), "{attacker}に倒されました"),
    (re.compile(r"was shot by (.+?)(?: using \[.+])?"), "{attacker}に射抜かれました"),
    (re.compile(r"was impaled by (.+?)(?: with .+)?"), "{attacker}に串刺しにされました"),
    (re.compile(r"was fireballed by (.+?)(?: using \[.+])?"), "{attacker}の火球で死亡しました"),
    (re.compile(r"was blown up by (.+)"), "{attacker}に爆破されました"),
    (re.compile(r"was pummeled by (.+?)(?: using \[.+])?"), "{attacker}に打ち倒されました"),
    (re.compile(r"was killed by (.+) using magic"), "{attacker}の魔法で死亡しました"),
    (re.compile(r"was killed by (.+?)(?: using \[.+])?"), "{attacker}に倒されました"),
    (re.compile(r"was squashed by (.+)"), "{attacker}に押し潰されました"),
    (re.compile(r"was frozen to death by (.+)"), "{attacker}によって凍死しました"),
    (re.compile(r"was stung to death by (.+)"), "{attacker}に刺されて死亡しました"),
    (
        re.compile(r"was roasted in dragon breath by (.+)"),
        "{attacker}のドラゴンブレスで焼かれました",
    ),
    (re.compile(r"was killed trying to hurt (.+)"), "{attacker}を攻撃しようとして死亡しました"),
    (
        re.compile(r"was blown from a high place by (.+?)(?: using \[.+])?"),
        "{attacker}に高い場所から落とされました",
    ),
    (
        re.compile(r"didn't want to live in the same world as (.+)"),
        "{attacker}によって死亡しました",
    ),
    (re.compile(r"died because of (.+)"), "{attacker}によって死亡しました"),
)

_CONTEXT_SUFFIX = re.compile(
    r" (?:(?:whilst|while) (?:trying to escape|fighting)|to escape|due to) .+$"
)


def translate_death(detail: str) -> str:
    if translated := _EXACT_DEATHS.get(detail):
        return translated
    for pattern, template in _ATTACKER_DEATHS:
        if match := pattern.fullmatch(detail):
            return template.format(attacker=_MOB_NAMES_JA.get(match[1], match[1]))

    base_detail = _CONTEXT_SUFFIX.sub("", detail)
    if translated := _EXACT_DEATHS.get(base_detail):
        return translated
    return f"死亡しました（{detail}）"  # noqa: RUF001


def is_death_detail(detail: str) -> bool:
    if detail in _EXACT_DEATHS:
        return True
    if any(pattern.fullmatch(detail) for pattern, _ in _ATTACKER_DEATHS):
        return True
    return _CONTEXT_SUFFIX.sub("", detail) in _EXACT_DEATHS
