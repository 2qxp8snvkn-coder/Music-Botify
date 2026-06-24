import lavalink
from lavalink.filters import (
    Equalizer, Timescale, Rotation, Tremolo, Vibrato,
    ChannelMix, Distortion, LowPass, Volume, Karaoke,
)


FILTERS = {
    "lofi": {
        "timescale": {"speed": 0.75, "pitch": 0.8, "rate": 1.0},
    },
    "nightcore": {
        "timescale": {"speed": 1.165, "pitch": 1.125, "rate": 1.05},
    },
    "slowmo": {
        "timescale": {"speed": 0.5, "pitch": 1.0, "rate": 0.8},
    },
    "chipmunk": {
        "timescale": {"speed": 1.05, "pitch": 1.35, "rate": 1.25},
    },
    "darthvader": {
        "timescale": {"speed": 0.975, "pitch": 0.5, "rate": 0.8},
    },
    "daycore": {
        "timescale": {"speed": 0.9, "pitch": 1.0, "rate": 1.0},
    },
    "damon": {
        "timescale": {"speed": 0.69, "pitch": 0.8, "rate": 1.0},
    },
}


async def apply_filter(player, name):
    name = name.lower()

    if name == "8d":
        rot = Rotation()
        rot.update(rotation_hz=0.3)
        await player.set_filter(rot)
        return True

    if name == "tremolo":
        t = Tremolo()
        t.update(frequency=4.0, depth=0.75)
        await player.set_filter(t)
        return True

    if name == "vibrate":
        t = Tremolo()
        t.update(frequency=4.0, depth=0.75)
        v = Vibrato()
        v.update(frequency=4.0, depth=0.75)
        await player.set_filter(t)
        await player.set_filter(v)
        return True

    if name == "bassboost":
        eq = Equalizer()
        eq.update(bands=[
            (0, 0.2), (1, 0.15), (2, 0.1), (3, 0.05), (4, 0.0),
            (5, -0.05), (6, -0.1), (7, -0.1), (8, -0.1), (9, -0.1),
            (10, -0.1), (11, -0.1), (12, -0.1), (13, -0.1), (14, -0.1),
        ])
        await player.set_filter(eq)
        return True

    if name == "earrape":
        await player.clear_filters()

        eq = Equalizer()
        eq.update(bands=[
            (0, 0.25), (1, 0.25), (2, 0.25), (3, 0.25), (4, 0.25),
            (5, 0.25), (6, 0.25), (7, 0.25), (8, 0.25), (9, 0.25),
            (10, 0.25), (11, 0.25), (12, 0.25), (13, 0.25), (14, 0.25),
        ])
        await player.set_filter(eq)

        vol = Volume()
        vol.update(volume=5.0)
        await player.set_filter(vol)

        dist = Distortion()
        dist.update(
            sin_offset=0.5, sin_scale=2.0,
            cos_offset=0.5, cos_scale=2.0,
            tan_offset=0.5, tan_scale=2.0,
            offset=0.5, scale=2.0,
        )
        await player.set_filter(dist)

        cm = ChannelMix()
        cm.update(
            left_to_left=1.0, left_to_right=0.5,
            right_to_left=0.5, right_to_right=1.0,
        )
        await player.set_filter(cm)

        rot = Rotation()
        rot.update(rotation_hz=0.15)
        await player.set_filter(rot)

        await player.set_volume(1000)
        return True

    if name == "121":
        await player.clear_filters()

        eq = Equalizer()
        eq.update(bands=[
            (0, 1.0), (1, 1.0), (2, 1.0), (3, 0.85), (4, 0.75),
            (5, 0.75), (6, 0.7), (7, 0.65), (8, 0.6), (9, 0.6),
            (10, 0.55), (11, 0.5), (12, 0.5), (13, 0.5), (14, 0.5),
        ])
        await player.set_filter(eq)

        vol = Volume()
        vol.update(volume=5.0)
        await player.set_filter(vol)

        cm = ChannelMix()
        cm.update(
            left_to_left=1.0, left_to_right=1.0,
            right_to_left=1.0, right_to_right=1.0,
        )
        await player.set_filter(cm)

        rot = Rotation()
        rot.update(rotation_hz=0.20)
        await player.set_filter(rot)

        await player.set_volume(1000)
        return True

    if name == "dis":
        await player.clear_filters()

        eq = Equalizer()
        eq.update(bands=[
            (0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0), (4, 1.0),
            (5, 1.0), (6, 1.0), (7, 1.0), (8, 1.0), (9, 1.0),
            (10, 1.0), (11, 1.0), (12, 1.0), (13, 1.0), (14, 1.0),
        ])
        await player.set_filter(eq)

        vol = Volume()
        vol.update(volume=5.0)
        await player.set_filter(vol)

        dist = Distortion()
        dist.update(
            sin_offset=0.05, sin_scale=1.1,
            cos_offset=0.05, cos_scale=1.1,
            tan_offset=0.03, tan_scale=1.05,
            offset=0.05, scale=1.1,
        )
        await player.set_filter(dist)

        cm = ChannelMix()
        cm.update(
            left_to_left=1.0, left_to_right=1.0,
            right_to_left=1.0, right_to_right=1.0,
        )
        await player.set_filter(cm)

        rot = Rotation()
        rot.update(rotation_hz=0.5)
        await player.set_filter(rot)

        await player.set_volume(1000)
        return True

    if name == "loud":
        await player.clear_filters()

        eq = Equalizer()
        eq.update(bands=[
            (0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0), (4, 0.9),
            (5, 0.85), (6, 0.8), (7, 0.8), (8, 0.85), (9, 0.9),
            (10, 0.95), (11, 1.0), (12, 1.0), (13, 1.0), (14, 1.0),
        ])
        await player.set_filter(eq)

        vol = Volume()
        vol.update(volume=5.0)
        await player.set_filter(vol)

        cm = ChannelMix()
        cm.update(
            left_to_left=1.0, left_to_right=1.0,
            right_to_left=1.0, right_to_right=1.0,
        )
        await player.set_filter(cm)

        rot = Rotation()
        rot.update(rotation_hz=0.15)
        await player.set_filter(rot)

        await player.set_volume(1000)
        return True

    if name in FILTERS:
        cfg = FILTERS[name]
        if "timescale" in cfg:
            ts = Timescale()
            ts.update(**cfg["timescale"])
            await player.set_filter(ts)
        return True

    return False


async def clear_filters(player):
    await player.clear_filters()


def list_filters():
    return ["lofi", "nightcore", "slowmo", "chipmunk", "darthvader",
            "daycore", "damon", "8d", "tremolo", "vibrate", "bassboost",
            "earrape", "121", "dis", "loud"]
