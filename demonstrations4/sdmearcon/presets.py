"""Saving and loading earcon families as JSON files.

A preset holds everything needed to reproduce a family: the ten latent vectors,
the SDM position that produced them, and the direct controls.

Two things are deliberately recorded but NOT restored:

* **The measurements** (peak, K-rms, crest, gain). They describe what a
  particular model produced on a particular machine. Restoring them would mean
  auditioning a family on the strength of numbers from a file, which breaks the
  one rule the whole safety design rests on: nothing is played before it has been
  measured here, now. They are kept in the file as provenance, so you can see
  what it sounded like when it was saved, and they are re-measured on import.

* **The master gain.** It starts at zero every session and never rises on its
  own. A file that could raise it would be a way around that.

The family stored is the controller's working family, BEFORE urgency, because
urgency is stored separately and re-applied. One source of truth for each value.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

FORMAT = "sdm-earcons/preset"
VERSION = 1

PRESET_DIR = Path(__file__).resolve().parent.parent / "output" / "presets"
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class PresetError(Exception):
    pass


def safe_name(name: str) -> str:
    """Reject anything that is not a plain file stem.

    The web endpoint takes this straight from a text field, so a name that could
    contain a path separator or `..` would let the panel write outside the
    presets folder.
    """
    name = (name or "").strip()
    if name.endswith(".json"):
        name = name[:-5]
    if not _SAFE.match(name) or name in (".", ".."):
        raise PresetError(
            "name must be 1 to 64 characters of letters, digits, dot, dash or "
            "underscore, and start with a letter or digit"
        )
    return name


def path_for(name: str) -> Path:
    return PRESET_DIR / (safe_name(name) + ".json")


def list_presets() -> list:
    if not PRESET_DIR.exists():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def to_dict(session, model: str = "rave", model_path: str = "") -> dict:
    ctl = session.ctl
    fam = ctl.family
    measured = []
    for i in range(fam.shape[0]):
        g = session.grads.get(i)
        measured.append(None if g is None else {
            "i": i,
            "peak_db": round(float(g["peak"]), 3),
            "krms_db": round(float(g["krms"]), 3),
            "crest_db": round(float(g["crest"]), 3),
            "gain_db": round(float(g["gain"]), 3),
            "usable": bool(g["usable"]),
        })
    return {
        "format": FORMAT,
        "version": VERSION,
        "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "model_path": model_path,
        "n_gradations": int(fam.shape[0]),
        "n_params": int(fam.shape[1]),
        "param_names": [f"z{j + 1}" for j in range(fam.shape[1])],
        "family": [[float(v) for v in row] for row in fam],
        "seed_family": [[float(v) for v in row] for row in ctl.seed_family],
        "sdm": {
            "seed": int(ctl.seed),
            "phase": float(ctl.phase),
            "sens": float(session.sens),
            "target": None if session.target is None else int(session.target),
        },
        "direct": {
            "urgency": float(session.urgency),
            "atk": float(session.atk),
            "dur": float(session.dur),
            "rel": float(session.rel),
            "pattern": str(session.pattern),
        },
        # provenance only, never restored: see the module docstring
        "measured_at_export": measured,
        "master_at_export": float(session.master),
        "note": ("measured_at_export and master_at_export are provenance. They "
                 "are not restored on import: the family is re-measured, and the "
                 "master gain always starts where it was."),
    }


def validate(data: dict, n_params_expected: int) -> None:
    if not isinstance(data, dict):
        raise PresetError("not a JSON object")
    if data.get("format") != FORMAT:
        raise PresetError(f"not an SDM earcon preset (format={data.get('format')!r})")
    if int(data.get("version", 0)) > VERSION:
        raise PresetError(f"preset version {data.get('version')} is newer than this tool")
    fam = data.get("family")
    if not isinstance(fam, list) or not fam:
        raise PresetError("missing 'family'")
    widths = {len(r) for r in fam}
    if len(widths) != 1:
        raise PresetError("rows of 'family' have different lengths")
    width = widths.pop()
    if width != n_params_expected:
        raise PresetError(
            f"preset has {width} parameters per gradation, this model has "
            f"{n_params_expected}. It was probably made with a different model."
        )
    arr = np.asarray(fam, dtype=float)
    if not np.isfinite(arr).all():
        raise PresetError("'family' contains non-finite values")


def save(session, name: str, model: str = "rave", model_path: str = "") -> Path:
    target = path_for(name)
    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    payload = to_dict(session, model=model, model_path=model_path)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def read(name: str) -> dict:
    target = path_for(name)
    if not target.exists():
        raise PresetError(f"no preset named {safe_name(name)!r}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PresetError(f"{target.name} is not valid JSON: {exc}") from exc
