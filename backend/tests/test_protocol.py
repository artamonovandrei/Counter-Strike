# path: backend/tests/test_protocol.py
"""Wire protocol: parsing, clamping and sanitising.

Every one of these is an anti-cheat or anti-crash boundary, so they assert on hostile
input rather than well-formed input.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from app.protocol import (
    K_ALL, K_FIRE, K_FORWARD, K_JUMP, MAX_INPUT_DT_MS, MAX_NAME_LEN, PROTOCOL_VERSION,
    parse_input, parse_input_batch, sanitize_chat, sanitize_name,
)

SHARED_TS = Path(__file__).resolve().parents[2] / "shared" / "protocol.ts"


def cmd(**over) -> dict:
    base = {"s": 1, "dt": 16.0, "k": 0, "y": 0.0, "p": 0.0, "w": 0}
    base.update(over)
    return base


# ── input parsing ─────────────────────────────────────────────────────────────

def test_parses_a_well_formed_command():
    c = parse_input(cmd(s=42, dt=16.7, k=K_FORWARD | K_JUMP, y=1.2, p=-0.3, w=2))
    assert c is not None
    assert c.seq == 42
    assert c.dt == pytest.approx(0.0167, abs=1e-4)
    assert c.pressed(K_FORWARD) and c.pressed(K_JUMP)
    assert not c.pressed(K_FIRE)
    assert c.weapon == 2


def test_rejects_non_dict_and_missing_fields():
    assert parse_input(None) is None
    assert parse_input("nope") is None
    assert parse_input([1, 2, 3]) is None
    assert parse_input({"s": 1}) is None


def test_rejects_nan_and_infinity():
    assert parse_input(cmd(y=float("nan"))) is None
    assert parse_input(cmd(p=float("inf"))) is None
    assert parse_input(cmd(dt=float("nan"))) is None


def test_dt_is_clamped_both_ways():
    """dt is the most abusable field — a big one buys free distance."""
    fast = parse_input(cmd(dt=100000.0))
    assert fast is not None and fast.dt == pytest.approx(MAX_INPUT_DT_MS / 1000.0)
    slow = parse_input(cmd(dt=0.0))
    assert slow is not None and slow.dt > 0.0
    neg = parse_input(cmd(dt=-50.0))
    assert neg is not None and neg.dt > 0.0


def test_unknown_key_bits_are_masked_off():
    c = parse_input(cmd(k=0xFFFFFFFF))
    assert c is not None
    assert c.keys == K_ALL


def test_pitch_is_clamped_to_straight_up_and_down():
    up = parse_input(cmd(p=99.0))
    down = parse_input(cmd(p=-99.0))
    assert up is not None and up.pitch < math.pi / 2
    assert down is not None and down.pitch > -math.pi / 2


def test_yaw_is_wrapped_into_range():
    c = parse_input(cmd(y=math.pi * 9.5))
    assert c is not None
    assert -math.pi <= c.yaw <= math.pi


def test_invalid_weapon_slot_becomes_no_change():
    assert parse_input(cmd(w=99)).weapon == 0
    assert parse_input(cmd(w=-3)).weapon == 0
    assert parse_input(cmd(w=3)).weapon == 3


def test_negative_or_absurd_sequence_is_rejected():
    assert parse_input(cmd(s=-1)) is None
    assert parse_input(cmd(s=2**40)) is None


# ── batches ───────────────────────────────────────────────────────────────────

def test_batch_keeps_the_newest_commands_when_flooded():
    payload = {"c": [cmd(s=i) for i in range(100)]}
    parsed = parse_input_batch(payload, limit=8)
    assert len(parsed) == 8
    assert [c.seq for c in parsed] == list(range(92, 100)), "newest kept, oldest dropped"


def test_batch_skips_malformed_entries_without_failing():
    payload = {"c": [cmd(s=1), "garbage", None, cmd(s=2)]}
    parsed = parse_input_batch(payload, limit=16)
    assert [c.seq for c in parsed] == [1, 2]


def test_batch_accepts_a_bare_list():
    assert len(parse_input_batch([cmd(s=1), cmd(s=2)], limit=8)) == 2


def test_batch_of_junk_is_empty():
    assert parse_input_batch(None, limit=8) == []
    assert parse_input_batch({"c": "nope"}, limit=8) == []


# ── sanitising ────────────────────────────────────────────────────────────────

def test_name_strips_markup_and_control_characters():
    cleaned = sanitize_name("<script>alert(1)</script>")
    assert "<" not in cleaned and ">" not in cleaned and "(" not in cleaned
    assert sanitize_name("bad\x00name") == "badname"
    assert sanitize_name("a‮b") == "ab", "bidi override must not survive"


def test_name_is_truncated_and_collapsed():
    assert len(sanitize_name("A" * 100)) == MAX_NAME_LEN
    assert sanitize_name("  spaced   out  ") == "spaced out"


def test_empty_or_wrong_typed_name_falls_back():
    assert sanitize_name("") == "Recruit"
    assert sanitize_name("   ") == "Recruit"
    assert sanitize_name(None) == "Recruit"
    assert sanitize_name(12345) == "Recruit"


def test_name_keeps_reasonable_characters():
    assert sanitize_name("[CLAN] Andrei_1.0-x") == "[CLAN] Andrei_1."
    assert sanitize_name("Ann-Marie_7") == "Ann-Marie_7"


def test_chat_is_trimmed_and_length_limited():
    assert sanitize_chat("  hello  ") == "hello"
    assert len(sanitize_chat("x" * 500)) == 120
    assert sanitize_chat(None) == ""


# ── cross-language parity ─────────────────────────────────────────────────────

def _ts_const(source: str, name: str) -> str:
    m = re.search(rf"export const {name} = ([^;]+);", source)
    assert m, f"{name} not found in shared/protocol.ts"
    return m.group(1).strip()


def test_protocol_version_matches_the_typescript_mirror():
    source = SHARED_TS.read_text(encoding="utf-8")
    assert _ts_const(source, "PROTOCOL_VERSION").strip("'\"") == PROTOCOL_VERSION


@pytest.mark.parametrize(
    "name,value",
    [
        ("K_FORWARD", 1 << 0), ("K_BACK", 1 << 1), ("K_LEFT", 1 << 2), ("K_RIGHT", 1 << 3),
        ("K_JUMP", 1 << 4), ("K_SPRINT", 1 << 5), ("K_FIRE", 1 << 6), ("K_RELOAD", 1 << 7),
        ("K_CROUCH", 1 << 8),
        ("F_DEAD", 1 << 0), ("F_GROUNDED", 1 << 1), ("F_RELOADING", 1 << 2),
        ("F_SPRINTING", 1 << 3), ("F_MOVING", 1 << 4), ("F_BOT", 1 << 5),
    ],
)
def test_bit_constants_match_the_typescript_mirror(name, value):
    """A mismatched bit here means the client would misread every snapshot."""
    import app.protocol as py

    assert getattr(py, name) == value
    source = SHARED_TS.read_text(encoding="utf-8")
    expr = _ts_const(source, name)
    assert eval(expr.replace("<<", "<<")) == value  # noqa: S307 - fixed input from repo
