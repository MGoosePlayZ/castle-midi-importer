#!/usr/bin/env python3
"""
midi_to_castle.py

Converts a MIDI file into a Castle actor JSON with a populated Music component.
One Castle track is created per MIDI track (skipping empty ones).
Each Castle track gets one pattern per bar, all referenced in that track's sequence.

Usage:
    python3 midi_to_castle.py input.mid [output.json] [--tpb BEATS] [--template actor.json]
    python3 midi_to_castle.py input.mid --no-split   # one pattern per track, no bar splitting
"""

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

try:
    import mido
except ImportError:
    print("ERROR: 'mido' not installed.  Run: pip install mido")
    sys.exit(1)


# ---------------------------------------------------------------------------
# MIDI helpers
# ---------------------------------------------------------------------------

def collect_tracks(mid: mido.MidiFile) -> list[list[tuple[float, int]]]:
    """
    Returns one list per MIDI track, each containing (beat_pos, midi_note)
    tuples for every note_on (velocity > 0) in that track.
    Type-0 files have a single track; type-1 files have one track per channel.
    """
    tpb = mid.ticks_per_beat
    result = []
    for track in mid.tracks:
        events = []
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                beat = abs_tick / tpb
                events.append((beat, msg.note))
        if events:
            result.append(events)
    return result


# ---------------------------------------------------------------------------
# Castle format helpers
# ---------------------------------------------------------------------------

def beat_key(beat: float) -> str:
    return f"{beat:.6f}"

def make_color() -> dict:
    return {"r": 0.19607, "g": 0.16862, "b": 0.15681, "a": 1.0}

def build_pattern(name: str, notes_by_beat: dict) -> tuple[str, dict]:
    pat_id = str(uuid.uuid4())
    notes = {
        beat_key(b): [{"key": n} for n in notes_by_beat[b]]
        for b in sorted(notes_by_beat)
    }
    return pat_id, {
        "patternId": pat_id,
        "name": name,
        "color": make_color(),
        "loop": "nextBar",
        "loopLength": 0,
        "notes": notes,
    }

def build_track(sequence: dict[float, str]) -> dict:
    """One Castle track. sequence: {beat_pos: patternId}"""
    return {
        "instrument": {
            "type": "sampler",
            "props": {"name": "tone", "muted": False, "volume": 1},
            "sample": {
                "type": "tone",
                "playbackRate": {"value": 1},
                "amplitude": {"value": 1},
                "pan": {"value": 0},
                "recordingUrl": "",
                "uploadUrl": "",
                "category": "random",
                "seed": 1337,
                "mutationSeed": 0,
                "mutationAmount": 5,
                "midiNote": 48,
                "waveform": "sawtooth",
                "attack": 0,
                "release": 0.4,
                "wait": False,
            },
        },
        "sequence": {
            beat_key(b): {"patternId": pid, "loop": False}
            for b, pid in sorted(sequence.items())
        },
    }

def events_to_bars(events: list[tuple[float, int]], beats_per_bar: int) -> list[tuple[float, dict]]:
    """
    Returns list of (bar_start_beat, notes_by_bar_rel_beat).
    notes_by_bar_rel_beat keys are relative to bar start.
    """
    if not events:
        return []
    bars: dict[int, dict] = {}
    for beat, note in events:
        idx = int(beat // beats_per_bar)
        rel = round(beat - idx * beats_per_bar, 9)
        bars.setdefault(idx, {}).setdefault(rel, []).append(note)
    return [(idx * beats_per_bar, notes) for idx, notes in sorted(bars.items())]

def events_to_single(events: list[tuple[float, int]]) -> dict:
    result: dict[float, list] = {}
    for beat, note in events:
        beat = round(beat, 9)
        result.setdefault(beat, []).append(note)
    return result


# ---------------------------------------------------------------------------
# Minimal actor shell
# ---------------------------------------------------------------------------

MINIMAL_TEMPLATE = {
    "entryId": "",
    "library": {},
    "entryType": "actorBlueprint",
    "title": "MidiActor",
    "titleEdited": False,
    "category": "",
    "actorBlueprint": {
        "components": {
            "Body": {
                "x": 0, "y": 0, "angle": 0,
                "width": 0.98995, "height": 0.98995,
                "widthScale": 0.1, "heightScale": 0.1,
                "visible": True, "relativeToCamera": False,
                "relativeToCameraFix": True, "layerName": "main",
                "fixtures": [], "paddingTop": 0, "paddingRight": 0,
                "paddingBottom": 0, "paddingLeft": 0,
                "editorBounds": {"minX": -9.375, "maxX": 8.39842, "minY": -10, "maxY": 10},
                "disabled": False,
            },
            "Music": None,
        }
    },
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("midi")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--tpb", type=int, default=4, help="beats per bar (default 4)")
    ap.add_argument("--template", help="base Castle actor JSON")
    ap.add_argument("--no-split", action="store_true", help="one pattern per MIDI track")
    ap.add_argument("--autoplay", default="loop", choices=["loop", "once", "none"])
    args = ap.parse_args()

    midi_path = Path(args.midi)
    if not midi_path.exists():
        sys.exit(f"ERROR: '{midi_path}' not found.")

    out_path = Path(args.output) if args.output else \
        midi_path.with_stem(midi_path.stem + "_castle").with_suffix(".json")

    mid = mido.MidiFile(midi_path)
    print(f"Loaded '{midi_path}' — type {mid.type}, "
          f"{len(mid.tracks)} MIDI track(s), {mid.ticks_per_beat} ticks/beat")

    midi_tracks = collect_tracks(mid)
    print(f"Non-empty MIDI tracks: {len(midi_tracks)}")

    patterns: dict[str, dict] = {}
    castle_tracks: list[dict] = []

    for t_idx, events in enumerate(midi_tracks):
        if args.no_split:
            notes_by_beat = events_to_single(events)
            pat_id, pat = build_pattern(f"t{t_idx}-all", notes_by_beat)
            patterns[pat_id] = pat
            castle_tracks.append(build_track({0.0: pat_id}))
        else:
            bars = events_to_bars(events, args.tpb)
            seq: dict[float, str] = {}
            for bar_beat, notes_by_beat in bars:
                name = f"t{t_idx}-b{int(bar_beat // args.tpb) + 1}"
                pat_id, pat = build_pattern(name, notes_by_beat)
                patterns[pat_id] = pat
                seq[bar_beat] = pat_id
            castle_tracks.append(build_track(seq))

    print(f"  Patterns : {len(patterns)}")
    print(f"  Tracks   : {len(castle_tracks)}")

    music = {
        "song": {"patterns": patterns, "tracks": castle_tracks},
        "autoplay": args.autoplay,
        "disabled": False,
    }

    if args.template:
        with open(args.template, "r", encoding="utf-8") as f:
            actor = json.load(f)
        actor["actorBlueprint"]["components"]["Music"] = music
    else:
        actor = copy.deepcopy(MINIMAL_TEMPLATE)
        actor["entryId"] = str(uuid.uuid4())
        actor["title"] = midi_path.stem
        actor["actorBlueprint"]["components"]["Music"] = music

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(actor, f, indent=2)
    print(f"Written to '{out_path}'.")


if __name__ == "__main__":
    main()
