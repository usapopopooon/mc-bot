import pytest

from mc_bot.material_buyback import (
    MaterialBuybackReleaseResult,
    MaterialBuybackResult,
    material_buyback_command,
    material_buyback_release_command,
    parse_material_buyback_release_result,
    parse_material_buyback_result,
)

PLAYER_UUID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"


def test_builds_and_parses_bound_material_buyback_command() -> None:
    assert material_buyback_command(PLAYER_UUID, "minecraft:sandstone", 256, REQUEST_ID) == (
        f"usapo-event-bridge material-buyback {PLAYER_UUID} minecraft:sandstone 256 {REQUEST_ID}"
    )
    assert material_buyback_command(PLAYER_UUID, "minecraft:emerald", 64, REQUEST_ID) == (
        f"usapo-event-bridge material-buyback {PLAYER_UUID} minecraft:emerald 64 {REQUEST_ID}"
    )
    assert parse_material_buyback_result(
        f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST_ID}|completed|minecraft:sandstone|256|duplicate",
        expected_request_id=REQUEST_ID,
        expected_item_id="minecraft:sandstone",
        expected_item_count=256,
    ) == MaterialBuybackResult(
        request_id=REQUEST_ID,
        status="completed",
        item_id="minecraft:sandstone",
        item_count=256,
        duplicate=True,
    )


@pytest.mark.parametrize(
    "response",
    [
        "garbage",
        f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST_ID}|completed|minecraft:dirt|256|new",
        f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST_ID}|completed|minecraft:sandstone|64|new",
        f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST_ID}|storage_error|minecraft:sandstone|256|duplicate",
    ],
)
def test_rejects_unbound_or_impossible_result(response: str) -> None:
    with pytest.raises(ValueError, match="material buyback result"):
        parse_material_buyback_result(
            response,
            expected_request_id=REQUEST_ID,
            expected_item_id="minecraft:sandstone",
            expected_item_count=256,
        )


def test_rejects_unsupported_items_and_partial_stacks() -> None:
    with pytest.raises(ValueError, match="selection"):
        material_buyback_command(PLAYER_UUID, "minecraft:diamond", 64, REQUEST_ID)
    with pytest.raises(ValueError, match="selection"):
        material_buyback_command(PLAYER_UUID, "minecraft:sand", 63, REQUEST_ID)


def test_builds_and_parses_request_bound_release_command() -> None:
    assert material_buyback_release_command(PLAYER_UUID, REQUEST_ID) == (
        f"usapo-event-bridge material-buyback-release {PLAYER_UUID} {REQUEST_ID}"
    )
    assert parse_material_buyback_release_result(
        f"USAPO_MATERIAL_BUYBACK_RELEASE_RESULT|1|{REQUEST_ID}|{PLAYER_UUID}|released",
        expected_player_uuid=PLAYER_UUID,
        expected_request_id=REQUEST_ID,
    ) == MaterialBuybackReleaseResult(REQUEST_ID, PLAYER_UUID, "released")
    assert parse_material_buyback_release_result(
        f"USAPO_MATERIAL_BUYBACK_RELEASE_RESULT|1|{REQUEST_ID}|{PLAYER_UUID}|request_mismatch",
        expected_player_uuid=PLAYER_UUID,
        expected_request_id=REQUEST_ID,
    ) == MaterialBuybackReleaseResult(REQUEST_ID, PLAYER_UUID, "request_mismatch")


@pytest.mark.parametrize(
    "response",
    [
        "garbage",
        f"USAPO_MATERIAL_BUYBACK_RELEASE_RESULT|1|{REQUEST_ID}|{PLAYER_UUID}|invalid",
        f"USAPO_MATERIAL_BUYBACK_RELEASE_RESULT|1|{PLAYER_UUID}|{REQUEST_ID}|released",
    ],
)
def test_rejects_unbound_or_failed_release(response: str) -> None:
    with pytest.raises(ValueError, match="material buyback release result"):
        parse_material_buyback_release_result(
            response,
            expected_player_uuid=PLAYER_UUID,
            expected_request_id=REQUEST_ID,
        )
