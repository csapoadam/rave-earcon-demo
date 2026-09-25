"""Tuning session state: the SDM controller plus the direct controls.

Python is the state host. It owns the SDM controller, the direct parameter
values, and the session history. SuperCollider owns audio, the model, the
measurement and the safety layer, and is told what to play over OSC.

Two rules worth stating.

Urgency is applied HERE, in the parameter domain, and the already-shifted rows
are what gets sent. sclang has its own ~setUrgency, but this side never uses it,
so there is exactly one place that decides what the latents are.

The SDM controls are PER-GRADATION (the paper's local tuning) with a whole-family
mode kept alongside. A traverse is previewed while the slider moves and committed
when it is released; committing bakes it into the working family and rebuilds the
decomposition, because the direction m(B) is shared and an uncommitted edit
cannot survive a change of phase.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from . import presets
from .osc import OscClient, OscServer
from .sdm_core import SdmController

# The two demo presets the default family interpolates between.
PRESET_NETWORK = [-1.0, -1.5, -0.5, 0.5]
PRESET_USER = [2.0, 1.0, 1.0, -1.0]

# Rhythm patterns as hit onset times in seconds. Mirrors ~patterns in sdm.scd.
PATTERNS = {
    "single": [0.0],
    "info": [0.0],
    "warning": [0.0, 0.14],
    "error": [0.0, 0.11, 0.22],
    "success": [0.0, 0.22],
}

# Latent box, from encoding real percussive audio through the model's own
# encoder (NOTES.md 9.3b). Used to limit the control's travel, never as a
# safety mechanism: safety is in the signal domain, on the SuperCollider side.
LATENT_BOX = [[-6.2, 5.7], [-9.1, 5.0], [-3.1, 7.2], [-5.0, 6.0]]

MODEL_NAME = "rave"
MODEL_PATH = "rave-example/RAVE/percussion.ts"


def _import_expectation(data: dict) -> dict:
    """What a preset file claims about itself, for checking after the re-measure.

    The loudness target is not stored in the v1 format, but it is recoverable:
    every usable row was given ``gain = targetK - krms``, so ``gain + krms`` is
    the target, and taking it from any usable row recovers the number exactly.
    """
    rows = [r for r in (data.get("measured_at_export") or []) if isinstance(r, dict)]
    usable = {int(r["i"]): bool(r.get("usable")) for r in rows if "i" in r}
    targets = [float(r["gain_db"]) + float(r["krms_db"]) for r in rows
               if r.get("usable") and r.get("gain_db") is not None
               and r.get("krms_db") is not None]
    return {"usable": usable,
            "target_k": (sorted(targets)[len(targets) // 2] if targets else None)}


def lerp_family(a, b, n: int = 10) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n).reshape(-1, 1)
    return (1 - t) * np.asarray(a, float) + t * np.asarray(b, float)


class Session:
    def __init__(self, host="127.0.0.1", send_port=57120, recv_port=57121, seed=0):
        self.lock = threading.RLock()
        self.seed_family = lerp_family(PRESET_NETWORK, PRESET_USER, 10)
        self.ctl = SdmController(self.seed_family, seed=seed, bounds=LATENT_BOX)

        # SDM controls, both dimensionless (NOTES.md 28)
        self.t = 0.0            # traverse, in turns
        self.sens = 0.5         # sensitivity, in family widths per turn
        self.target = None      # None = whole family, else a gradation index

        # direct controls, not tuned by SDM
        self.urgency = 0.0
        self.atk, self.dur, self.rel = 0.005, 0.25, 0.30
        self.master = 0.0
        self.pattern = "single"

        # what came back from SuperCollider
        self.grads: dict[int, dict] = {}
        self.n_usable = None
        self.target_k = None
        self.pool_size = None
        self.armed = None
        self.status = "not connected"
        self.last_error = ""
        self.measuring = False
        self.last_commit = 0.0
        # sclang states its own constants rather than Python duplicating them,
        # so "how close is this gradation to being refused" stays one number in
        # one place. None until the first /sdm/limits arrives.
        self.peak_ceil_db = None
        self.max_boost_db = None
        self.max_crest_db = None
        # What an imported preset claimed, so the re-measure can be compared
        # against it. See _on_done.
        self._import_check = None
        self._import_name = ""
        self._env_at_render = (self.atk, self.dur, self.rel)

        self.client = OscClient(host, send_port)
        self.server = OscServer(recv_port, host)
        self.server.on("/sdm/grad", self._on_grad)
        self.server.on("/sdm/familyDone", self._on_done)
        self.server.on("/sdm/pong", self._on_pong)
        self.server.on("/sdm/error", self._on_error)
        self.server.on("/sdm/limits", self._on_limits)
        self.server.on("/sdm/reordered", self._on_reordered)
        self.server.start()

    # ---------- inbound from SuperCollider ----------
    def _on_grad(self, idx, peak, krms, crest, onset_ms, gain, usable):
        with self.lock:
            self.grads[int(idx)] = dict(peak=peak, krms=krms, crest=crest,
                                        onset=onset_ms, gain=gain, usable=bool(usable))

    def _on_done(self, n_usable, n_total, target_k):
        with self.lock:
            self.n_usable, self.target_k = int(n_usable), target_k
            self.measuring = False
            self.status = f"measured, {int(n_usable)} of {int(n_total)} usable"
            if self._import_check is not None:
                self._compare_with_import(target_k)

    def _compare_with_import(self, target_k) -> None:
        """Say so when a re-measured preset does not agree with the file.

        The family is restored bit for bit, but usability is not a property of
        the family alone: it is the family plus a fresh measurement, run through
        a loudness target derived as ``peakCeil - max(crest)`` over the ten. That
        target is a MAXIMUM over ten noisy numbers, so one gradation measuring
        slightly less spiky lowers the boost available to everybody, and any
        gradation that was sitting near the cap is refused. A family can be
        saved 0.4 dB from that cliff with nothing saying so, which is exactly
        how a preset that played comes back silent.

        Called with the lock held.
        """
        want, name = self._import_check, self._import_name
        self._import_check = None
        lost = sorted(i for i, was_usable in want["usable"].items()
                      if was_usable and not (self.grads.get(i) or {}).get("usable", False))
        bits = []
        if want["target_k"] is not None and target_k is not None:
            moved = float(target_k) - want["target_k"]
            if abs(moved) >= 0.25:
                bits.append("the loudness target moved %+.2f dB (%.2f at save, %.2f now)"
                            % (moved, want["target_k"], float(target_k)))
        if lost:
            bits.append("gradation%s %s played when %s was saved and %s refused now"
                        % ("" if len(lost) == 1 else "s",
                           ", ".join(str(i) for i in lost), name,
                           "is" if len(lost) == 1 else "are"))
        if bits:
            self.last_error = ("%s. The family itself is restored exactly; what "
                               "changed is the measurement." % "; ".join(bits))

    def _on_pong(self, armed, master, pool):
        with self.lock:
            self.armed, self.master, self.pool_size = bool(armed), float(master), int(pool)
            self.status = "connected, armed" if armed else "connected, DISARMED"

    def _on_limits(self, peak_ceil_db, max_boost_db, max_crest_db):
        with self.lock:
            self.peak_ceil_db = float(peak_ceil_db)
            self.max_boost_db = float(max_boost_db)
            self.max_crest_db = float(max_crest_db)

    def _on_reordered(self, n):
        with self.lock:
            self.status = "reordered %d gradations" % int(n)

    def _on_error(self, message):
        with self.lock:
            self.last_error = str(message)
            # A refusal ENDS the exchange: SuperCollider sends no /sdm/grad
            # and no /sdm/familyDone after it, so nothing else would ever
            # clear the busy flag and the panel would sit on "measuring..."
            # for the rest of the session with no visible reason.
            if self.measuring:
                self.measuring = False
                self.status = "refused: %s" % str(message)

    # ---------- the family ----------
    def family(self) -> np.ndarray:
        """What is actually sent: the previewed traverse, then urgency."""
        return self.ctl.preview(self.t) + self.urgency

    def t_limits(self) -> tuple[float, float]:
        """Traverse range keeping the family in the latent box AT THIS URGENCY."""
        lo, hi = self.ctl.admissible_t(offset=self.urgency)
        if lo == 0.0 and hi == 0.0 and self.urgency != 0.0:
            self.last_error = ("urgency %.2f pushes the family out of the latent box "
                               "on its own" % self.urgency)
        return (lo, hi)

    # ---------- outbound to SuperCollider ----------
    def ping(self):
        self.client.send("/sdm/ping")

    def push_direct(self):
        self.client.send("/sdm/evt", float(self.atk), float(self.dur), float(self.rel))
        self.client.send("/sdm/pattern", *[float(t) for t in PATTERNS[self.pattern]])

    def set_master(self, amp: float):
        with self.lock:
            self.master = float(np.clip(amp, 0.0, 1.0))
        self.client.send("/sdm/master", self.master)

    def commit(self):
        """Bake the traverse into the family, then measure it.

        A per-gradation edit only changes one row, so only that row is
        re-measured: about 0.8 s instead of about 3 s. The other nine
        measurements are still valid, and SuperCollider recomputes every gain
        from them, because the loudness target depends on the family's worst
        crest factor.
        """
        with self.lock:
            target = self.target
            self.ctl.commit(self.t)
            self.t = 0.0
            fam = self.family()
            self.measuring = True
            self.status = "measuring"
            self.last_commit = time.time()
            # A single-gradation re-render is only valid when nothing
            # family-wide changed. The envelope is baked into every recorded
            # buffer, so if it moved, all ten have to be rendered again.
            env_changed = (self.atk, self.dur, self.rel) != self._env_at_render
            single = (target is not None and len(self.grads) == fam.shape[0]
                      and not env_changed)
            self._env_at_render = (self.atk, self.dur, self.rel)
            if single:
                self.grads.pop(int(target), None)
            else:
                self.grads.clear()
        self.push_direct()
        if single:
            row = [float(v) for v in fam[int(target)]]
            self.client.send("/sdm/grad1", int(target), int(fam.shape[1]), *row)
        else:
            flat = [float(v) for v in fam.reshape(-1)]
            self.client.send("/sdm/family", int(fam.shape[0]), int(fam.shape[1]), *flat)

    def reset(self):
        with self.lock:
            self.ctl.reset()
            self.t = 0.0
        self.commit()

    def move_gradation(self, idx: int, delta: int) -> None:
        """Move one earcon up or down the list.

        Deliberately NOT a re-measure. A reorder changes no sound at all, so the
        measurements, the gains and the recorded buffers are permuted alongside
        the rows on both sides of the bridge and stay valid. The gains do not
        even need recomputing: the loudness target is derived from the family's
        worst crest, and a maximum does not care about order.
        """
        with self.lock:
            n = self.ctl.family.shape[0]
            perm = self.ctl.move(int(idx), int(delta))
            if perm is None:
                return
            # grads is keyed by slot, so it moves with the rows.
            self.grads = {k: self.grads[old] for k, old in enumerate(perm)
                          if old in self.grads}
            # The selection follows the EARCON, not the slot it used to sit in.
            if self.target is not None:
                self.target = perm.index(int(self.target))
                self.ctl.target = self.target
            # Any previewed traverse described the old arrangement.
            self.t = 0.0
            self.status = "moved gradation %d" % int(idx)
        self.client.send("/sdm/reorder", n, *[int(q) for q in perm])

    def set_row(self, idx: int, values) -> None:
        """Type one earcon's coordinates in directly, then re-measure that row.

        This is the same edit a committed traverse makes — the working family
        changes and the decomposition is rebuilt from it — so the spiral
        continues from the typed-in point. Direct entry and SDM tuning are two
        ways of moving the same row, not two separate states.
        """
        with self.lock:
            clamped = self.ctl.set_row(int(idx), values)
            self.target = int(idx)
            self.ctl.target = self.target
            self.t = 0.0
            if clamped:
                names = ", ".join("z%d" % (j + 1) for j in clamped)
                self.last_error = ("%s clamped to the latent box; outside it the "
                                   "traverse has no travel" % names)
            else:
                self.last_error = ""
        self.commit()

    # ---------- presets ----------
    def export_preset(self, name: str) -> str:
        with self.lock:
            path = presets.save(self, name, model=MODEL_NAME, model_path=MODEL_PATH)
            self.last_error = ""
            self.status = "saved %s" % path.name
        return str(path)

    def import_preset(self, name: str) -> None:
        """Restore a saved family, then MEASURE it.

        Nothing measured is taken from the file. A preset can carry gains from a
        different model or a different machine, and trusting them would mean
        auditioning a family on the strength of numbers nobody checked here. The
        master gain is not restored either: it starts where it was.
        """
        data = presets.read(name)
        presets.validate(data, int(self.ctl.family.shape[1]))
        with self.lock:
            sdm = data.get("sdm", {}) or {}
            direct = data.get("direct", {}) or {}
            seed_family = np.asarray(data.get("seed_family") or data["family"], dtype=float)
            self.ctl = SdmController(seed_family, seed=int(sdm.get("seed", 0)),
                                     bounds=LATENT_BOX)
            self.ctl.family = np.asarray(data["family"], dtype=float).copy()
            self.ctl._rebuild()
            self.ctl.phase = float(sdm.get("phase", 0.0)) % 1.0
            self.sens = float(sdm.get("sens", 0.5))
            tgt = sdm.get("target", None)
            self.target = None if tgt is None else int(tgt)
            self.ctl.sens, self.ctl.target = self.sens, self.target
            self.urgency = float(direct.get("urgency", 0.0))
            self.atk = float(direct.get("atk", 0.005))
            self.dur = float(direct.get("dur", 0.25))
            self.rel = float(direct.get("rel", 0.30))
            pat = direct.get("pattern", "single")
            self.pattern = pat if pat in PATTERNS else "single"
            self.t = 0.0
            self.grads.clear()          # measurements are never restored
            self.n_usable = None
            self.target_k = None
            self.last_error = ""
            self.status = "imported %s, measuring" % presets.safe_name(name)
            self._import_name = presets.safe_name(name)
            self._import_check = _import_expectation(data)
        self.commit()

    def play(self, idx: int):
        self.client.send("/sdm/play", int(idx))

    def sweep(self):
        self.client.send("/sdm/sweep")

    def panic(self):
        with self.lock:
            self.master = 0.0
        self.client.send("/sdm/panic")

    # ---------- for the UI ----------
    def snapshot(self) -> dict:
        with self.lock:
            fam = self.family()
            lo, hi = self.t_limits()
            rows = []
            for i in range(fam.shape[0]):
                g = self.grads.get(i)
                rows.append({
                    "i": i,
                    "z": [round(float(v), 3) for v in fam[i]],
                    "peak": None if g is None else round(g["peak"], 2),
                    "krms": None if g is None else round(g["krms"], 2),
                    "crest": None if g is None else round(g["crest"], 2),
                    "gain": None if g is None else round(g["gain"], 2),
                    "usable": None if g is None else g["usable"],
                    # How many dB of boost are left before this gradation is
                    # refused. Small numbers here are what a family looks like
                    # just before a re-measure loses it.
                    "headroom": (None if (g is None or self.max_boost_db is None
                                          or not g["usable"])
                                 else round(self.max_boost_db - g["gain"], 2)),
                    "selected": (self.target is not None and int(self.target) == i),
                })
            return {
                "t": self.t, "sens": self.sens,
                "t_lo": round(lo, 3), "t_hi": round(hi, 3),
                "target": self.target,
                "phase": round(self.ctl.phase_at(self.t), 4),
                "spread": round(self.ctl.spread, 3),
                "weight": round(self.ctl.weight_delta(self.t), 4),
                "urgency": self.urgency, "atk": self.atk, "dur": self.dur,
                "rel": self.rel, "master": self.master, "pattern": self.pattern,
                "patterns": list(PATTERNS),
                "status": self.status, "measuring": self.measuring,
                "armed": self.armed, "pool": self.pool_size,
                "target_k": None if self.target_k is None else round(self.target_k, 2),
                "max_boost_db": self.max_boost_db,
                "error": self.last_error,
                "presets": presets.list_presets(),
                "rows": rows,
            }

    def apply(self, changes: dict) -> None:
        with self.lock:
            for key in ("t", "sens", "urgency", "atk", "dur", "rel"):
                if key in changes:
                    setattr(self, key, float(changes[key]))
            if "pattern" in changes and changes["pattern"] in PATTERNS:
                self.pattern = changes["pattern"]
            if "target" in changes:
                tgt = changes["target"]
                self.target = None if tgt in (None, "", "all") else int(tgt)
            self.ctl.sens = self.sens
            self.ctl.target = self.target
            lo, hi = self.t_limits()
            self.t = float(np.clip(self.t, lo, hi))

    def close(self):
        self.server.stop()
        self.client.close()
