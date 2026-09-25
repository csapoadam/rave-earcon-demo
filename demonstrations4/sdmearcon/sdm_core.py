"""Spiral Discovery Method controller for a family of earcon parameter vectors.

The algorithm is the one in ``sdm-new/sdm/sdm.py`` (Csapo & Baranyi, JACIII 16(2),
2012). That file is used unmodified as the source of truth; nothing is copied.

THE CONTROL IS PER-GRADATION, which is what the paper's section 3 calls LOCAL
TUNING: modifying row p_k of X_k changes only the outputs belonging to gradation
p_k. Equation 13 makes it concrete, f_i = w_i * m(B) + c, one scalar tuning
weight per gradation, all sharing a common direction m(B) that the core tensor
mix rotates.

An earlier version of this file moved the whole family together, because that is
what ``explore_tensor`` in sdm-new does (``U[0][:, 0] += step`` on the entire
first column). That function is on the AUGMENTATION path: ``SdmModel.augment``
sweeps it to stack synthetic training rows. It is not the interactive tuning
control, and using it as one made it impossible to adjust one earcon relative to
its neighbours, which is most of what fine-tuning a graded family is.

Two controls, both DIMENSIONLESS, matching the paper's "one for traversing the
spiral, and one for setting the sensitivity of the first control":

* ``t``    traverse, in turns. Advances the core mix AND the tuning weight
           together, so the selected gradation traces a genuine spiral rather
           than a straight line. Independent phase and radius knobs, which is
           what this file had before, can move radially at a fixed angle, and
           that is a line.
* ``sens`` sensitivity, in FAMILY WIDTHS PER TURN. 1.0 means one full turn
           displaces the gradation by about the family's own spread. Expressing
           it this way keeps it unitless and meaningful for any model.

The direction length varies by about 40 percent around the spiral (measured:
6.58 at one mix, 9.17 at another), so displacement is specified directly and the
tuning weight is derived from it. That keeps the knob linear in what the user
perceives.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SDM_NEW = _PROJECT_ROOT / "sdm-src"


# NumPy 2.x removed a number of long-standing aliases. The vendored sktensor in
# sdm-new/ predates that and uses three of them: np.in1d (removed in 2.4),
# np.Inf and np.row_stack (removed in 2.0). The rule in CLAUDE.md is not to
# modify files under sdm-new/, so instead the missing names are restored on the
# numpy module before sktensor is imported.
#
# This does patch numpy process-wide, which is worth being explicit about. It is
# narrow: only names that are ALREADY MISSING are set, and only to their exact
# documented replacements, so on an older numpy it does nothing at all. The
# alternative was editing Adam's source tree, which is worse.
_NUMPY_REMOVED = {
    "in1d": "isin", "row_stack": "vstack", "Inf": "inf", "infty": "inf",
    "NaN": "nan", "float_": "float64", "complex_": "complex128",
    "unicode_": "str_", "string_": "bytes_", "alltrue": "all", "sometrue": "any",
    "product": "prod", "cumproduct": "cumprod", "round_": "round",
}


# What the shim actually had to restore on this interpreter. Recorded at the
# moment it runs, because by the time anything asks, the names are all present
# and asking again would answer "nothing needed" no matter what happened.
SHIM_RESTORED: list = []
_SHIM_DONE = False


def _shim_numpy() -> list:
    """Restore removed numpy aliases the vendored sktensor still uses."""
    global _SHIM_DONE
    if _SHIM_DONE:
        return SHIM_RESTORED
    for old_name, new_name in _NUMPY_REMOVED.items():
        if not hasattr(np, old_name) and hasattr(np, new_name):
            setattr(np, old_name, getattr(np, new_name))
            SHIM_RESTORED.append(old_name)
    _SHIM_DONE = True
    return SHIM_RESTORED


def _load_reference_sdm():
    """Import ``sdm-new/sdm/sdm.py`` without triggering that package's __init__.

    ``sdm/__init__.py`` imports the whole research stack (scikit-learn, torch,
    pympler). The core algorithm needs only numpy, scipy and the vendored
    sktensor, so the two modules that matter are loaded into a synthetic package
    and the relative import inside sdm.py resolves against it.
    """
    if "sdm_vendor.sdm" in sys.modules:
        return sys.modules["sdm_vendor.sdm"]
    _shim_numpy()                          # must happen before sktensor imports
    if str(_SDM_NEW) not in sys.path:
        sys.path.insert(0, str(_SDM_NEW))  # for the vendored sktensor
    pkg = types.ModuleType("sdm_vendor")
    pkg.__path__ = [str(_SDM_NEW / "sdm")]
    sys.modules["sdm_vendor"] = pkg
    for name in ("randomgen", "sdm"):
        path = _SDM_NEW / "sdm" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"sdm_vendor.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"sdm_vendor.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["sdm_vendor.sdm"]


def snake_mix(phase: float) -> tuple[float, float, float]:
    """Mixing coefficients over (S_min, S_zero, S_max) at a point on the spiral.

    Continuous, periodic reformulation of ``_snake_combo_generator``. The four
    quarters are median->max, max->median, median->min, min->median, so the
    coefficients always sum to 1 and the path is closed.
    """
    p = float(phase) % 1.0
    q, u = divmod(p * 4.0, 1.0)
    q = int(q)
    if q == 0:
        return (0.0, 1.0 - u, u)
    if q == 1:
        return (0.0, u, 1.0 - u)
    if q == 2:
        return (u, 1.0 - u, 0.0)
    return (1.0 - u, u, 0.0)


@dataclass
class SdmState:
    """Everything needed to reproduce a tuning session, plus the SDM matrices."""

    family0: np.ndarray                 # the seed family, shape (P, H)
    weights: list                       # U_n from the SDM decomposition
    core_zero: np.ndarray
    core_max: np.ndarray
    core_min: np.ndarray
    seed: int
    _dir_cache: dict = field(default_factory=dict, repr=False)


def build_state(family: np.ndarray, seed: int = 0) -> SdmState:
    """Decompose a family of shape (n_gradations, n_params)."""
    family = np.asarray(family, dtype=float)
    if family.ndim != 2:
        raise ValueError(f"family must be 2-D (gradations, params), got {family.shape}")
    if not np.isfinite(family).all():
        raise ValueError("family contains non-finite values")
    ref = _load_reference_sdm()
    rng = sys.modules["sdm_vendor.randomgen"].RandGenerator(seed)
    weights, s_zero, s_max, s_min = ref.create_sdm_state(family, randomgen=rng)
    return SdmState(family.copy(), weights, s_zero, s_max, s_min, seed)


def _reconstruct(state: SdmState, mix, offset: float) -> np.ndarray:
    """Full tensor reconstruction: S(mix) x_n U_n, with U_0[:,0] shifted."""
    ref = _load_reference_sdm()
    dtensor = ref.dtensor
    a, b, c = mix
    U = [w.copy() for w in state.weights]
    U[0][:, 0] = state.weights[0][:, 0] + offset
    S = a * state.core_min + b * state.core_zero + c * state.core_max
    out = dtensor(S)
    for dim in range(len(U)):
        out = out.ttm(U[dim], dim)
    return np.asarray(out, dtype=float)


def direction(state: SdmState, phase: float):
    """Unit direction m(B) that the tuning weight moves a gradation along.

    Returns the unit vector. ``direction_norm`` gives its length, which is the
    displacement produced by one unit of tuning weight and varies around the
    spiral. Cached per phase, since each one costs a tensor reconstruction.
    """
    return _direction_cached(state, phase)[0]


def direction_norm(state: SdmState, phase: float) -> float:
    """Displacement in parameter space per unit of tuning weight, at this phase."""
    return _direction_cached(state, phase)[1]


def _direction_cached(state: SdmState, phase: float):
    key = round(float(phase) % 1.0, 9)
    hit = state._dir_cache.get(key)
    if hit is not None:
        return hit
    mix = snake_mix(key)
    delta = _reconstruct(state, mix, 1.0) - _reconstruct(state, mix, 0.0)
    vec = delta[0]
    norm = float(np.linalg.norm(vec))
    if norm < 1e-12:
        raise ValueError(f"degenerate direction at phase {phase}")
    out = (vec / norm, norm)
    state._dir_cache[key] = out
    return out


def evaluate_weights(state: SdmState, dw, phase: float) -> np.ndarray:
    """Family after changing the per-gradation tuning weights by ``dw``.

    ``dw`` is one scalar per gradation. Per equation 13 the effect is
    ``F[i] += dw[i] * m(B)``, so a single non-zero entry moves exactly one
    gradation. Verified against the full tensor reconstruction in the selftest.
    """
    dw = np.asarray(dw, dtype=float).reshape(-1, 1)
    return state.family0 + dw * (direction(state, phase) * direction_norm(state, phase))


def _reconstruct_weights(state: SdmState, mix, dw) -> np.ndarray:
    """Full tensor reconstruction with per-row weight changes. Verification only."""
    ref = _load_reference_sdm()
    dtensor = ref.dtensor
    a, b, c = mix
    U = [w.copy() for w in state.weights]
    U[0][:, 0] = state.weights[0][:, 0] + np.asarray(dw, dtype=float)
    S = a * state.core_min + b * state.core_zero + c * state.core_max
    out = dtensor(S)
    for dim in range(len(U)):
        out = out.ttm(U[dim], dim)
    return np.asarray(out, dtype=float)


WHOLE_FAMILY = None


class SdmController:
    """Per-gradation SDM tuning with a dimensionless traverse and sensitivity.

    The working family is mutable: a traverse is previewed, then committed, and
    the decomposition is rebuilt from the new family. That matters because the
    direction m(B) is shared by every gradation, so an uncommitted edit to one
    gradation cannot survive a change of phase. Committing bakes the edit into
    the family and starts the next edit from there, which is also how a designer
    actually works: tune one, keep it, tune the next.
    """

    def __init__(self, family: np.ndarray, seed: int = 0, bounds=None):
        family = np.asarray(family, dtype=float)
        if family.ndim != 2:
            raise ValueError(f"family must be 2-D, got {family.shape}")
        self.seed_family = family.copy()
        self.family = family.copy()
        self.seed = int(seed)
        self.bounds = None if bounds is None else np.asarray(bounds, dtype=float)
        if self.bounds is not None and self.bounds.shape != (family.shape[1], 2):
            raise ValueError("bounds must have shape (n_params, 2)")
        self.phase = 0.0          # where on the spiral the next traverse starts
        self.sens = 0.5           # family widths per turn
        self.target = WHOLE_FAMILY  # None = whole family, else a gradation index
        self._rebuild()

    def _rebuild(self) -> None:
        self.state = build_state(self.family, self.seed)

    @property
    def spread(self) -> float:
        """The family's own scale: RMS distance of gradations from their centre.

        This is what makes ``sens`` dimensionless. It is recomputed after every
        commit, so the knob keeps meaning the same thing as the family moves.
        """
        centred = self.family - self.family.mean(axis=0)
        value = float(np.sqrt((centred ** 2).sum(axis=1).mean()))
        return value if value > 1e-9 else 1.0

    def phase_at(self, t: float) -> float:
        return (self.phase + float(t)) % 1.0

    def displacement(self, t: float) -> float:
        """Latent-space distance travelled by the selected gradation."""
        return self.sens * self.spread * float(t)

    def weight_delta(self, t: float) -> float:
        """The SDM tuning weight change this traverse corresponds to.

        The UI works in displacement because that is what the ear tracks, but the
        underlying model parameter is the weight, so it is reported.
        """
        return self.displacement(t) / direction_norm(self.state, self.phase_at(t))

    def preview(self, t: float) -> np.ndarray:
        """The family as it would be at traverse ``t``. Pure, nothing mutated."""
        out = self.family.copy()
        step = self.displacement(t) * direction(self.state, self.phase_at(t))
        if self.target is WHOLE_FAMILY:
            out += step
        else:
            out[int(self.target)] += step
        return out

    def commit(self, t: float) -> np.ndarray:
        """Bake the traverse into the family and rebuild the decomposition."""
        self.family = self.preview(t)
        self.phase = self.phase_at(t)
        self._rebuild()
        return self.family

    def reorder(self, perm) -> np.ndarray:
        """Permute the gradations. ``new_family[k] = old_family[perm[k]]``.

        In a graded family the ORDER is the meaning — gradation 3 is what the
        interface will use for level 3 — so rearranging is a first-class edit,
        not a display preference. Nothing about any single earcon changes, so no
        sound needs re-rendering; the decomposition is rebuilt because U_0's rows
        are indexed by gradation and would otherwise describe the old order.
        """
        perm = np.asarray(perm, dtype=int).reshape(-1)
        n = self.family.shape[0]
        if perm.shape != (n,) or sorted(perm.tolist()) != list(range(n)):
            raise ValueError(f"reorder needs a permutation of 0..{n - 1}, got {perm.tolist()}")
        self.family = self.family[perm]
        self._rebuild()
        return self.family

    def move(self, idx: int, delta: int) -> list:
        """Move one gradation up or down by ``delta`` slots. Returns the
        permutation applied, or None if it would fall off either end."""
        n = self.family.shape[0]
        idx, j = int(idx), int(idx) + int(delta)
        if not (0 <= idx < n and 0 <= j < n):
            return None
        perm = list(range(n))
        perm[idx], perm[j] = perm[j], perm[idx]
        self.reorder(perm)
        return perm

    def set_row(self, idx: int, values) -> np.ndarray:
        """Set one gradation's parameters directly, then rebuild.

        The typed-in coordinates become the working family exactly as a
        committed traverse would, so the spiral continues from where the edit
        left off rather than from where it was before. Values outside ``bounds``
        are clamped and the clamping is reported: the box is an ergonomic limit,
        not a safety mechanism (safety is in the signal domain), but letting a
        row sit outside it would freeze the traverse, since admissible_t would
        then find no travel at all.
        """
        values = np.asarray(values, dtype=float).reshape(-1)
        n, h = self.family.shape
        if not (0 <= int(idx) < n):
            raise ValueError(f"gradation {idx} is not in 0..{n - 1}")
        if values.shape != (h,):
            raise ValueError(f"expected {h} parameters, got {values.shape[0]}")
        if not np.isfinite(values).all():
            raise ValueError("parameters must be finite")
        clamped = []
        if self.bounds is not None:
            lo, hi = self.bounds[:, 0], self.bounds[:, 1]
            out = np.clip(values, lo, hi)
            clamped = [j for j in range(h) if out[j] != values[j]]
            values = out
        self.family[int(idx)] = values
        self._rebuild()
        return clamped

    def reset(self) -> np.ndarray:
        self.family = self.seed_family.copy()
        self.phase = 0.0
        self._rebuild()
        return self.family

    def admissible_t(self, offset: float = 0.0, limit: float = 2.0,
                     step: float = 0.005) -> tuple:
        """Traverse range that keeps every parameter inside ``bounds``.

        Scanned rather than solved: the direction rotates with ``t``, so the
        constraint is not linear in it and there is no closed form. ``offset`` is
        any uniform shift applied afterwards, i.e. the urgency control, which has
        to be included or the interval describes a family that is not the one
        played.
        """
        if self.bounds is None:
            return (-limit, limit)
        lo_b, hi_b = self.bounds[:, 0], self.bounds[:, 1]

        def ok(t):
            fam = self.preview(t) + offset
            return bool((fam >= lo_b - 1e-9).all() and (fam <= hi_b + 1e-9).all())

        if not ok(0.0):
            return (0.0, 0.0)
        out = []
        for sign in (-1.0, 1.0):
            edge = 0.0
            k = 1
            while k * step <= limit:
                t = sign * k * step
                if not ok(t):
                    break
                edge = t
                k += 1
            out.append(edge)
        return (float(out[0]), float(out[1]))

    def session(self, t: float) -> dict:
        """Everything needed to reproduce the current position."""
        return {
            "seed": self.seed,
            "phase": self.phase,
            "sens": self.sens,
            "t": float(t),
            "target": self.target,
            "spread": self.spread,
            "weight_delta": self.weight_delta(t),
            "family": self.family.tolist(),
            "seed_family": self.seed_family.tolist(),
        }
