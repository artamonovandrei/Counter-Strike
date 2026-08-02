# path: backend/tests/test_weapons.py
"""The expanded arsenal: loadouts, shotgun pellets, ADS, and per-weapon balance shape.

The balance assertions are deliberately about *relationships* rather than exact numbers —
"the sniper hits harder than the SMG", "the shotgun falls off faster than the rifle". That
way the numbers stay tunable without the tests turning into a second copy of the config,
but a change that inverts a weapon's role still fails loudly.
"""

from __future__ import annotations

import math
import random

import pytest

from app.config import MOVE, PRIMARY_WEAPONS, WEAPONS, make_loadout
from app.game.combat import resolve_shot
from app.game.mathx import Vec3
from app.game.weapons import Arsenal, damage_at_range
from conftest import make_entity, make_world

RIFLE = WEAPONS["rifle"]
SMG = WEAPONS["smg"]
SNIPER = WEAPONS["sniper"]
SHOTGUN = WEAPONS["shotgun"]
PISTOL = WEAPONS["pistol"]
KNIFE = WEAPONS["knife"]


def armed(primary: str, now: float = 0.0) -> Arsenal:
    ars = Arsenal(primary)
    ars.reset(now, primary)
    ars.switch_end_at = 0.0
    return ars


# ── loadouts ──────────────────────────────────────────────────────────────────

def test_loadout_is_primary_pistol_knife():
    assert make_loadout("sniper") == ["sniper", "pistol", "knife"]
    assert make_loadout("smg") == ["smg", "pistol", "knife"]


def test_unknown_primary_falls_back_to_the_rifle():
    assert make_loadout("rocket_launcher")[0] == "rifle"
    assert make_loadout(None)[0] == "rifle"
    assert make_loadout("pistol")[0] == "rifle", "a secondary is not a valid primary"


@pytest.mark.parametrize("primary", PRIMARY_WEAPONS)
def test_number_keys_mean_the_same_thing_for_every_loadout(primary):
    """Slot 1 is always your primary, 2 the pistol, 3 the knife."""
    ars = armed(primary)
    assert ars.current == primary
    ars.select_slot(2, 10.0)
    assert ars.current == "pistol"
    ars.select_slot(3, 20.0)
    assert ars.current == "knife"
    ars.select_slot(1, 30.0)
    assert ars.current == primary


def test_dropping_a_primary_leaves_you_the_pistol():
    ars = armed("shotgun")
    assert ars.drop_current(1.0) == "shotgun"
    assert ars.current == "pistol"
    assert "shotgun" not in ars.slots


# ── shotgun ───────────────────────────────────────────────────────────────────

def test_shotgun_fires_the_configured_pellet_count():
    ars = armed("shotgun")
    rng = random.Random(1)
    dirs = ars.pellet_directions(0.0, 0.0, 3.0, rng)
    assert len(dirs) == SHOTGUN.pellets == 9


def test_single_projectile_weapons_fire_one_ray():
    for wid in ("rifle", "smg", "sniper", "pistol"):
        ars = armed(wid)
        assert len(ars.pellet_directions(0.0, 0.0, 1.0, random.Random(2))) == 1


def test_shotgun_pellets_are_spread_but_the_first_is_centred():
    ars = armed("shotgun")
    dirs = ars.pellet_directions(0.0, 0.0, 4.0, random.Random(3))
    centre_err = math.hypot(dirs[0][0], dirs[0][1])
    others = [math.hypot(y, p) for y, p in dirs[1:]]
    assert centre_err < max(others), "the aimed pellet should be tighter than the rest"
    assert max(others) > 0.0


def test_full_shotgun_blast_at_point_blank_is_lethal():
    """Nine pellets on an unarmoured torso must kill; otherwise the weapon has no role."""
    total = SHOTGUN.pellets * damage_at_range(SHOTGUN, 3.0)
    assert total > MOVE.max_health


def test_shotgun_is_harmless_at_range():
    far = SHOTGUN.pellets * damage_at_range(SHOTGUN, 40.0)
    assert far < MOVE.max_health * 0.5


def test_shotgun_blast_aggregates_into_one_damage_number():
    """Verified through the real raycaster, not just the damage table."""
    w = make_world()
    shooter = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    target = make_entity(2, "B", Vec3(0.0, 0.0, -4.0))
    ars = armed("shotgun")
    rng = random.Random(7)

    origin = shooter.eye_pos()
    aim = (Vec3(0.0, 1.0, -4.0) - origin).normalized()
    from app.game.mathx import dir_to_angles

    yaw, pitch = dir_to_angles(aim)

    hits = 0
    total = 0.0
    for py, pp in ars.pellet_directions(yaw, pitch, 3.2, rng):
        from app.game.mathx import angles_to_dir

        res = resolve_shot(w, shooter, [shooter, target], SHOTGUN, origin, angles_to_dir(py, pp), 1.0)
        if res.victim is target:
            hits += 1
            total += res.damage
    assert hits >= 5, f"most pellets should connect at 4 m, got {hits}"
    assert total > 0


# ── sniper ────────────────────────────────────────────────────────────────────

def test_sniper_body_shot_kills_an_unarmoured_target():
    assert damage_at_range(SNIPER, 40.0) > MOVE.max_health


def test_sniper_barely_falls_off():
    near = damage_at_range(SNIPER, 10.0)
    far = damage_at_range(SNIPER, 150.0)
    assert far == pytest.approx(near)


def test_sniper_is_useless_from_the_hip_and_precise_when_scoped():
    ars = armed("sniper")
    hip = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    ars.set_ads(True)
    ars.ads_progress = 1.0
    scoped = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    assert hip > 5.0, "hip-firing a bolt gun must not be viable"
    assert scoped < 0.2, "scoped and still, it should be a laser"


def test_sniper_bolt_cycle_is_slow():
    assert SNIPER.shot_interval > 1.0
    assert SNIPER.shot_interval > RIFLE.shot_interval * 8


def test_sniper_cannot_be_fired_twice_without_the_bolt():
    ars = armed("sniper")
    assert ars.can_fire(0.0, True)
    ars.consume_shot(0.0)
    ars.trigger_held = False
    assert not ars.can_fire(0.5, True)
    assert ars.can_fire(1.6, True)


# ── SMG ───────────────────────────────────────────────────────────────────────

def test_smg_trades_damage_for_rate_of_fire():
    assert SMG.damage < RIFLE.damage
    assert SMG.rpm > RIFLE.rpm
    # Sustained DPS should be in the same league, or nobody would pick it.
    smg_dps = SMG.damage * SMG.rpm / 60.0
    rifle_dps = RIFLE.damage * RIFLE.rpm / 60.0
    assert 0.7 < smg_dps / rifle_dps < 1.15


def test_smg_stays_accurate_on_the_move():
    smg = armed("smg")
    rifle = armed("rifle")
    speed = MOVE.sprint_speed
    assert smg.current_spread_deg(speed, True, speed) < rifle.current_spread_deg(
        speed, True, speed
    ), "mobility is the SMG's entire identity"


def test_smg_falls_off_harder_than_the_rifle_at_range():
    assert damage_at_range(SMG, 60.0) < damage_at_range(RIFLE, 60.0)


# ── ADS ───────────────────────────────────────────────────────────────────────

def test_sights_take_time_to_raise():
    ars = armed("rifle")
    ars.set_ads(True)
    ars.update(0.0, 0.016)
    assert 0.0 < ars.ads_progress < 1.0, "must not snap to fully sighted in one tick"
    for i in range(200):
        ars.update(i * 0.016, 0.016)
    assert ars.ads_progress == pytest.approx(1.0)


def test_sights_lower_when_released():
    ars = armed("rifle")
    ars.set_ads(True)
    for i in range(100):
        ars.update(i * 0.016, 0.016)
    ars.set_ads(False)
    for i in range(100):
        ars.update(i * 0.016, 0.016)
    assert ars.ads_progress == pytest.approx(0.0)


def test_spread_improves_progressively_with_the_sights():
    ars = armed("rifle")
    hip = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    ars.ads_progress = 0.5
    half = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    ars.ads_progress = 1.0
    full = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    assert hip > half > full


def test_knife_cannot_be_scoped():
    ars = armed("rifle")
    ars.select("knife", 0.0)
    ars.switch_end_at = 0.0
    assert ars.current == "knife"
    assert not ars.can_ads()
    ars.set_ads(True)
    ars.update(0.0, 0.5)
    assert ars.ads_progress == 0.0


def test_reloading_drops_you_out_of_the_sights():
    ars = armed("rifle")
    ars.set_ads(True)
    ars.consume_shot(0.0)
    assert ars.begin_reload(1.0)
    assert not ars.ads


def test_switching_weapons_drops_the_sights():
    ars = armed("sniper")
    ars.set_ads(True)
    ars.ads_progress = 1.0
    ars.select("pistol", 5.0)
    assert not ars.ads and ars.ads_progress == 0.0


def test_sights_steady_the_recoil():
    hip = armed("rifle")
    scoped = armed("rifle")
    scoped.ads_progress = 1.0
    hip.consume_shot(0.0)
    scoped.consume_shot(0.0)
    assert scoped.recoil_kick()[1] < hip.recoil_kick()[1]


# ── cross-weapon sanity ───────────────────────────────────────────────────────

@pytest.mark.parametrize("wid", list(WEAPONS))
def test_every_weapon_is_internally_consistent(wid):
    w = WEAPONS[wid]
    assert w.slot in (1, 2, 3)
    assert w.category in ("primary", "secondary", "melee")
    assert w.pellets >= 1
    assert w.damage > 0
    assert w.range > 0
    assert w.falloff_end >= w.falloff_start
    assert 0.0 < w.falloff_min <= 1.0
    assert w.rpm > 0
    if not w.melee:
        assert w.mag_size > 0 and w.reserve_max > 0
        assert w.reload_time > 0
    if w.ads_fov > 0:
        assert w.ads_time > 0
        assert 0 < w.ads_spread_mult <= 1.0, "sights must never make you less accurate"


def test_only_the_sniper_uses_the_full_scope():
    scoped = [w.id for w in WEAPONS.values() if w.scope]
    assert scoped == ["sniper"]


def test_headshots_matter_least_for_the_shotgun():
    assert SHOTGUN.headshot_mult < RIFLE.headshot_mult
    assert SHOTGUN.headshot_mult < PISTOL.headshot_mult
