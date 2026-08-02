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
    "was squashed by a falling anvil": "落下してきた金床に押しつぶされた",
    "was pricked to death": "サボテンが刺さって死んだ",
    "was squished too much": "押しつぶされた",
    "was roasted in dragon's breath": "ドラゴンブレスで炙り焼きにされた",
    "drowned": "溺死した",
    "died from dehydration": "脱水で死んだ",
    "was killed by even more magic": "魔法の奔流で殺された",
    "blew up": "爆発に巻き込まれた",
    "hit the ground too hard": "地面に強く激突した",
    "was squashed by a falling block": "落下してきたブロックに押しつぶされた",
    "was skewered by a falling stalactite": "落ちてきた鍾乳石に串刺しにされた",
    "went off with a bang": "花火の爆発に巻き込まれた",
    "experienced kinetic energy": "運動エネルギーを体験した",
    "froze to death": "凍え死んだ",
    "died": "死んだ",
    "was killed": "殺された",
    "discovered the floor was lava": "床が溶岩だったと気付いた",
    "went up in flames": "炎に巻かれた",
    "suffocated in a wall": "壁の中で窒息した",
    "tried to swim in lava": "溶岩遊泳を試みた",
    "was struck by lightning": "雷に打たれた",
    "was killed by magic": "魔法で殺された",
    "burned to death": "こんがりと焼けた",
    "fell out of the world": "奈落の底へ落ちた",
    "left the confines of this world": "ワールドの外側へと踏み出した",
    "was obliterated by a sonically-charged shriek": "衝撃波に消し飛ばされた",
    "was impaled on a stalagmite": "鍾乳石に突き刺さった",
    "starved to death": "餓死した",
    "was stung to death": "刺されて死んだ",
    "died because not just the floor is lava": "溶岩は床だけではなかったと気付いた",
    "was poked to death by a sweet berry bush": "スイートベリーの棘が刺さって死んだ",
    "withered away": "干からびた",
    "fell from a high place": "高い所から落ちた",
    "fell off a ladder": "はしごから落ちた",
    "fell while climbing": "登る途中で落ちた",
    "fell off scaffolding": "足場から滑り落ちた",
    "fell off some twisting vines": "ねじれツタから滑り落ちた",
    "fell off some vines": "ツタから滑り落ちた",
    "fell off some weeping vines": "しだれツタから滑り落ちた",
    "was doomed to fall": "落ちる運命だった",
}

_PATTERN_DEATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"was slain by (.+) using (.+)"), "{attacker}の{item}で殺害された"),
    (re.compile(r"was slain by (.+)"), "{attacker}に殺害された"),
    (re.compile(r"was blown up by (.+) using (.+)"), "{attacker}の{item}で爆破された"),
    (re.compile(r"was blown up by (.+)"), "{attacker}に爆破された"),
    (re.compile(r"was fireballed by (.+) using (.+)"), "{attacker}の{item}で火だるまにされた"),
    (re.compile(r"was fireballed by (.+)"), "{attacker}に火だるまにされた"),
    (re.compile(r"was smashed by (.+) with (.+)"), "{attacker}の{item}で叩き潰された"),
    (re.compile(r"was smashed by (.+)"), "{attacker}に叩き潰された"),
    (re.compile(r"was speared by (.+) using (.+)"), "{attacker}の{item}で突き刺された"),
    (re.compile(r"was speared by (.+)"), "{attacker}に突き刺された"),
    (re.compile(r"was impaled by (.+) with (.+)"), "{attacker}の{item}で突き抜かれた"),
    (re.compile(r"was impaled by (.+)"), "{attacker}によって突き抜かれた"),
    (re.compile(r"was pummeled by (.+) using (.+)"), "{attacker}の{item}でぺしゃんこにされた"),
    (re.compile(r"was pummeled by (.+)"), "{attacker}によってぺしゃんこにされた"),
    (re.compile(r"was squashed by (.+)"), "{attacker}に押しつぶされた"),
    (re.compile(r"was frozen to death by (.+)"), "{attacker}によって凍え死んだ"),
    (re.compile(r"was stung to death by (.+) using (.+)"), "{attacker}の{item}に刺されて死んだ"),
    (re.compile(r"was stung to death by (.+)"), "{attacker}に刺されて死んだ"),
    (
        re.compile(r"was roasted in dragon's breath by (.+)"),
        "{attacker}のドラゴンブレスで炙り焼きにされた",
    ),
    (re.compile(r"was killed while trying to hurt (.+)"), "{attacker}を傷つけようとして殺された"),
    (re.compile(r"died because of (.+)"), "{attacker}によって死んだ"),
    (
        re.compile(r"walked into a cactus while trying to escape (.+)"),
        "{attacker}から逃れようとしてサボテンにぶつかった",
    ),
    (re.compile(r"drowned while trying to escape (.+)"), "{attacker}から逃れようとして溺死した"),
    (
        re.compile(r"died from dehydration while trying to escape (.+)"),
        "{attacker}から逃れようとして脱水で死んだ",
    ),
    (
        re.compile(r"hit the ground too hard while trying to escape (.+)"),
        "{attacker}から逃れようとして地面に強く激突した",
    ),
    (
        re.compile(r"experienced kinetic energy while trying to escape (.+)"),
        "{attacker}から逃れようとして運動エネルギーを体験した",
    ),
    (
        re.compile(r"tried to swim in lava to escape (.+)"),
        "{attacker}から逃れようと溶岩遊泳を試みた",
    ),
    (
        re.compile(r"was killed by magic while trying to escape (.+)"),
        "{attacker}から逃れようとして魔法で殺された",
    ),
    (
        re.compile(r"was obliterated by a sonically-charged shriek while trying to escape (.+)"),
        "{attacker}から逃れようとして衝撃波に消し飛ばされた",
    ),
    (
        re.compile(r"was poked to death by a sweet berry bush while trying to escape (.+)"),
        "{attacker}から逃れようとしてスイートベリーの棘が刺さって死んだ",
    ),
    (
        re.compile(r"was squashed by a falling anvil while fighting (.+)"),
        "{attacker}と戦いながら落ちてきた金床に押しつぶされた",
    ),
    (
        re.compile(r"was squashed by a falling block while fighting (.+)"),
        "{attacker}と戦いながら落ちてきたブロックに押しつぶされた",
    ),
    (
        re.compile(r"was skewered by a falling stalactite while fighting (.+)"),
        "{attacker}と戦いながら落ちてきた鍾乳石に串刺しにされた",
    ),
    (
        re.compile(r"went off with a bang while fighting (.+)"),
        "{attacker}と戦いながら花火の爆発に巻き込まれた",
    ),
    (re.compile(r"was killed while fighting (.+)"), "{attacker}と戦いながら殺された"),
    (
        re.compile(r"walked into fire while fighting (.+)"),
        "{attacker}と戦いながら火の中へ踏み入った",
    ),
    (
        re.compile(r"suffocated in a wall while fighting (.+)"),
        "{attacker}と戦いながら壁の中で窒息した",
    ),
    (
        re.compile(r"was struck by lightning while fighting (.+)"),
        "{attacker}と戦いながら雷に打たれた",
    ),
    (
        re.compile(r"was burned to a crisp while fighting (.+)"),
        "{attacker}と戦いながらカリカリに焼けた",
    ),
    (
        re.compile(r"burned to death while fighting (.+)"),
        "{attacker}と戦いながらカリカリに焼けた",
    ),
    (
        re.compile(r"left the confines of this world while fighting (.+)"),
        "{attacker}と戦いながらワールドの外側へと踏み出した",
    ),
    (
        re.compile(r"was impaled on a stalagmite while fighting (.+)"),
        "{attacker}と戦いながら鍾乳石に突き刺さった",
    ),
    (re.compile(r"starved to death while fighting (.+)"), "{attacker}と戦いながら餓死した"),
    (re.compile(r"withered away while fighting (.+)"), "{attacker}と戦いながら干からびた"),
    (
        re.compile(r"walked into the danger zone due to (.+)"),
        "{attacker}に妨害されて危険地帯に足を踏み入れた",
    ),
    (
        re.compile(r"didn't want to live in the same world as (.+)"),
        "{attacker}と同じワールドに住みたくなかった",
    ),
    (
        re.compile(r"was doomed to fall by (.+) using (.+)"),
        "{attacker}の{item}で落とされる運命だった",
    ),
    (re.compile(r"was doomed to fall by (.+)"), "{attacker}に落とされる運命だった"),
    (
        re.compile(r"fell too far and was finished by (.+) using (.+)"),
        "高いところから落下し、{attacker}の{item}によってとどめを刺された",
    ),
    (
        re.compile(r"fell too far and was finished by (.+)"),
        "高いところから落下し、{attacker}によってとどめを刺された",
    ),
    (
        re.compile(r"was shot by a skull from (.+) using (.+)"),
        "{attacker}から{item}で頭蓋骨に打たれた",
    ),
    (re.compile(r"was shot by a skull from (.+)"), "{attacker}の頭蓋骨に打たれた"),
    (re.compile(r"was shot by (.+) using (.+)"), "{attacker}の{item}で射抜かれた"),
    (re.compile(r"was shot by (.+)"), "{attacker}に射抜かれた"),
    (re.compile(r"was killed by (.+) using magic"), "{attacker}の魔法で殺された"),
    (re.compile(r"was killed by (.+) using (.+)"), "{attacker}の{item}で殺された"),
    (re.compile(r"was killed by (.+)"), "{attacker}に殺された"),
)


def translate_death(detail: str) -> str:
    normalized_detail = detail.replace(" whilst ", " while ").replace(
        "dragon breath", "dragon's breath"
    )
    if translated := _EXACT_DEATHS.get(normalized_detail):
        return translated
    for pattern, template in _PATTERN_DEATHS:
        if match := pattern.fullmatch(normalized_detail):
            values = match.groups()
            attacker = _MOB_NAMES_JA.get(values[0], values[0])
            item = values[1] if len(values) > 1 else ""
            return template.format(attacker=attacker, item=item)
    return f"死んだ（{detail}）"  # noqa: RUF001


def is_death_detail(detail: str) -> bool:
    normalized_detail = detail.replace(" whilst ", " while ").replace(
        "dragon breath", "dragon's breath"
    )
    if normalized_detail in _EXACT_DEATHS:
        return True
    return any(pattern.fullmatch(normalized_detail) for pattern, _ in _PATTERN_DEATHS)
