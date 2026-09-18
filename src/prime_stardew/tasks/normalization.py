"""Convert StarDojo responses into stable scorer input."""

from __future__ import annotations

from collections import Counter

from prime_stardew.env.models import Observation, TileInfo

from .models import ItemCount, NormalizedCrop, NormalizedState, NormalizedTile


def normalize_state(
    observation: Observation,
    *,
    target_tile: TileInfo | None = None,
) -> NormalizedState:
    """Normalize one observation, merging global crop state into a target tile."""

    target = _normalize_tile(observation, target_tile) if target_tile is not None else None
    return NormalizedState(
        player=observation.player.name,
        date=observation.game_state.date,
        time=observation.game_state.time,
        location=observation.player.location,
        position=(observation.player.position.x, observation.player.position.y),
        facing=observation.player.facing_direction,
        stamina=observation.player.stamina,
        money=observation.player.money,
        inventory=_counts(
            (item.name, item.quantity or 0) for item in observation.player.inventory if item.name
        ),
        target=target,
        menu_type=observation.current_menu.type,
        chest=_counts(
            (item.name, item.quantity)
            for item in (observation.current_menu.items_in_chest or [])
        ),
    )


def _normalize_tile(observation: Observation, tile: TileInfo) -> NormalizedTile:
    global_crop = next(
        (
            crop
            for crop in observation.crops
            if crop.position is not None
            and (crop.position.x, crop.position.y) == tile.position
        ),
        None,
    )
    local_crop = tile.crop_at_tile
    crop = None
    if local_crop is not None or global_crop is not None:
        crop = NormalizedCrop(
            seed_id=(local_crop.seed_id if local_crop else None)
            or (global_crop.id if global_crop else None),
            harvest_id=local_crop.index_harvest if local_crop else None,
            watered=(
                global_crop.is_watered
                if global_crop is not None
                else local_crop.is_watered if local_crop is not None else None
            ),
            dead=global_crop.is_dead if global_crop is not None else None,
            phase=(
                global_crop.current_phase
                if global_crop is not None
                else local_crop.current_phase if local_crop is not None else None
            ),
        )
    return NormalizedTile(
        position=tile.position,
        object_name=_present(tile.object_at_tile),
        terrain=_present(tile.terrain_at_tile),
        debris_name=_present(tile.debris_at_tile),
        crop=crop,
    )


def _counts(entries: object) -> tuple[ItemCount, ...]:
    counts: Counter[str] = Counter()
    for name, quantity in entries:  # type: ignore[union-attr]
        counts[name] += quantity
    return tuple(ItemCount(name=name, quantity=counts[name]) for name in sorted(counts))


def _present(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None
