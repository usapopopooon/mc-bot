import pytest

from mc_bot.emerald_exchange import (
    parse_diamond_emerald_exchange_result,
    parse_emerald_diamond_exchange_result,
)
from mc_bot.market import parse_market_transfer_result
from mc_bot.material_buyback import parse_material_buyback_result
from mc_bot.quest import parse_quest_action_result

REQUEST = "11111111-1111-4111-8111-111111111111"


def test_world_restriction_is_definite_no_delivery_for_all_item_protocols() -> None:
    emerald = parse_emerald_diamond_exchange_result(
        f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST}|world_restricted|32|1|new",
        expected_request_id=REQUEST,
        expected_emerald_count=32,
    )
    diamond = parse_diamond_emerald_exchange_result(
        f"USAPO_DIAMOND_EXCHANGE_RESULT|1|{REQUEST}|world_restricted|4|64|new",
        expected_request_id=REQUEST,
        expected_diamond_count=4,
    )
    buyback = parse_material_buyback_result(
        f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST}|world_restricted|minecraft:emerald|64|new",
        expected_request_id=REQUEST,
        expected_item_id="minecraft:emerald",
        expected_item_count=64,
    )
    market = parse_market_transfer_result(
        f"USAPO_MARKET_TRANSFER_RESULT|1|{REQUEST}|17|world_restricted|active|new",
        request_id=REQUEST,
        listing_id=17,
    )
    quest = parse_quest_action_result(
        f"USAPO_QUEST_ACTION_RESULT|1|{REQUEST}|17|world_restricted|accepted|new",
        request_id=REQUEST,
        quest_id=17,
    )
    for result in (emerald, diamond, buyback, market, quest):
        assert result.status == "world_restricted"
        assert result.duplicate is False
    assert market.delivery_recorded is False


@pytest.mark.parametrize("disposition", ["duplicate", "unknown"])
def test_denied_exchange_cannot_claim_completed_replay(disposition: str) -> None:
    with pytest.raises(ValueError):
        parse_emerald_diamond_exchange_result(
            f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST}|world_restricted|32|1|{disposition}",
            expected_request_id=REQUEST,
            expected_emerald_count=32,
        )
    with pytest.raises(ValueError):
        parse_diamond_emerald_exchange_result(
            f"USAPO_DIAMOND_EXCHANGE_RESULT|1|{REQUEST}|world_restricted|4|64|{disposition}",
            expected_request_id=REQUEST,
            expected_diamond_count=4,
        )
    with pytest.raises(ValueError):
        parse_material_buyback_result(
            f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST}|world_restricted|minecraft:emerald|64|{disposition}",
            expected_request_id=REQUEST,
            expected_item_id="minecraft:emerald",
            expected_item_count=64,
        )
