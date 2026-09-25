"""Self-test for the SDM controller. Mirrors the SuperCollider self-test.

The rule carried over from the SuperCollider side (NOTES.md 21.3, 27.4): at
least one check must compare against an answer known in closed form, not against
the code's own self-consistency, and no check may be able to pass vacuously.
T2 and T3 are the load-bearing ones: they verify the affine controller against
the full tensor reconstruction, which is an independent computation.

Run:  uv run python -m sdmearcon.selftest
"""
from __future__ import annotations

import numpy as np

from . import sdm_core
from .sdm_core import (SdmController, direction, direction_norm, evaluate_weights,
                       snake_mix, _load_reference_sdm, _reconstruct,
                       _reconstruct_weights, _NUMPY_REMOVED)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"  {'PASS' if ok else '*** FAIL ***':14s} {name:46s} {detail}")


def main() -> int:
    rng = np.random.default_rng(0)
    P, H = 10, 4
    F = rng.normal(scale=2.0, size=(P, H))
    box = np.tile(np.array([-6.0, 6.0]), (H, 1))

    print("\n================ SDM CONTROLLER SELF-TEST ================")

    import numpy as _np
    _load_reference_sdm()
    still = [k for k in _NUMPY_REMOVED if not hasattr(_np, k)]
    restored = sdm_core.SHIM_RESTORED
    check("T0 numpy alias shim (numpy %s)" % _np.__version__, not still,
          ("restored " + ", ".join(sorted(restored))) if restored
          else "no aliases missing on this numpy")

    c = SdmController(F, seed=0, bounds=box)
    st = c.state
    phases = np.linspace(0, 1, 9)[:-1]

    # T1 no traverse, no change
    check("T1 t=0 leaves the family untouched",
          np.array_equal(c.preview(0.0), c.family), "exact")

    # T2 CLOSED FORM. The affine per-gradation form must equal the full tensor
    # reconstruction with the same weight changes. Two independent computations.
    err = 0.0
    for ph in phases:
        for _ in range(3):
            dw = rng.normal(scale=0.3, size=P)
            a = evaluate_weights(st, dw, ph)
            b = _reconstruct_weights(st, snake_mix(ph), dw)
            err = max(err, float(np.abs(a - b).max()))
    check("T2 affine weights == tensor reconstruction", err < 1e-8, f"max err {err:.2e}")

    # T3 CLOSED FORM AND THE POINT OF THE REWRITE. In the FULL tensor
    # reconstruction, changing one gradation's tuning weight must move that
    # gradation and no other. This is the paper's local tuning (section 3), and
    # it is what the previous whole-family implementation could not do.
    worst_moved, worst_others = 0.0, 0.0
    for ph in phases[::2]:
        base = _reconstruct_weights(st, snake_mix(ph), np.zeros(P))
        for i in (0, 3, 9):
            dw = np.zeros(P); dw[i] = 0.37
            delta = _reconstruct_weights(st, snake_mix(ph), dw) - base
            worst_moved = max(worst_moved, float(np.linalg.norm(delta[i])))
            others = np.delete(delta, i, axis=0)
            worst_others = max(worst_others, float(np.abs(others).max()))
    check("T3 one weight moves exactly one gradation",
          worst_others < 1e-9 and worst_moved > 1e-3,
          f"target moved {worst_moved:.3f}, others {worst_others:.2e}")

    # T4 the sensitivity means what it says: displacement is sens * spread * t
    c.target, c.sens = 3, 0.5
    errs = []
    for t in (0.1, 0.5, 1.0, -0.7):
        d = c.preview(t) - c.family
        errs.append(abs(np.linalg.norm(d[3]) - abs(c.sens * c.spread * t)))
    check("T4 displacement == sens * spread * |t|", max(errs) < 1e-9,
          f"max err {max(errs):.2e}, spread {c.spread:.3f}")

    # T5 sens is genuinely dimensionless: scale the family, and the same (t,
    # sens) must scale the displacement by the same factor.
    c2 = SdmController(F * 7.0, seed=0, bounds=box * 7.0)
    c2.target, c2.sens = 3, 0.5
    r1 = np.linalg.norm((c.preview(0.4) - c.family)[3])
    r2 = np.linalg.norm((c2.preview(0.4) - c2.family)[3])
    check("T5 sens is scale-free (family x7 -> displacement x7)",
          abs(r2 / r1 - 7.0) < 1e-6, f"ratio {r2 / r1:.6f}")

    # T6 the traverse is a SPIRAL, not a line: the direction rotates with t
    # while the displacement grows. A fixed-angle radial move would be a line,
    # which is what independent phase and radius knobs allowed.
    c.target = 3
    angs = []
    for t in (0.05, 0.15, 0.25, 0.35):
        d = (c.preview(t) - c.family)[3]
        angs.append(d / np.linalg.norm(d))
    angs = np.array(angs)
    spread_deg = np.degrees(np.arccos(np.clip(angs @ angs.T, -1, 1))).max()
    grows = np.linalg.norm((c.preview(0.35) - c.family)[3]) > \
            np.linalg.norm((c.preview(0.05) - c.family)[3])
    check("T6 traverse rotates and extends (a spiral)",
          spread_deg > 2.0 and grows, f"direction turns {spread_deg:.1f} deg over 0.3 turn")

    # T7 purity: preview mutates nothing and repeats bit-identically
    before = c.family.copy()
    a = c.preview(0.31); b = c.preview(0.31)
    check("T7 preview is pure and repeatable",
          np.array_equal(a, b) and np.array_equal(before, c.family), "exact")

    # T8 commit bakes the traverse in and rebuilds from the new family
    c.target = 3
    want = c.preview(0.2)
    got = c.commit(0.2)
    check("T8 commit == preview, then family is the new origin",
          np.allclose(got, want) and np.allclose(c.preview(0.0), got), "exact")

    # T9 whole-family mode (control c, kept deliberately) moves every gradation
    # by the same vector, so the family translates rigidly
    c.target = None
    d = c.preview(0.3) - c.family
    check("T9 whole-family mode translates rigidly",
          np.linalg.matrix_rank(d, tol=1e-9) == 1 and np.abs(d - d[0]).max() < 1e-12,
          f"rank 1, row spread {np.abs(d - d[0]).max():.2e}")

    # T10 admissible traverse is tight: inside stays in the box, one step out leaves it
    c.target, c.sens = 5, 2.0
    lo, hi = c.admissible_t()
    inside = c.preview(hi)
    beyond = c.preview(hi + 0.05)
    ok = bool((inside >= box[:, 0] - 1e-6).all() and (inside <= box[:, 1] + 1e-6).all())
    ok &= bool((beyond < box[:, 0] - 1e-9).any() or (beyond > box[:, 1] + 1e-9).any())
    check("T10 admissible traverse is tight", ok, f"t in [{lo:.3f}, {hi:.3f}]")

    # T11 the snake path is closed and its coefficients sum to 1
    fine = np.linspace(0, 1, 401)[:-1]
    sums = [abs(sum(snake_mix(p)) - 1.0) for p in fine]
    check("T11 snake mix sums to 1, path closed",
          max(sums) < 1e-12 and np.allclose(snake_mix(0.0), snake_mix(1.0)),
          f"max err {max(sums):.2e}")

    # T12 every core tensor reconstructs the seed family: the property the
    # whole method rests on
    err = max(float(np.abs(_reconstruct(st, m, 0.0) - F).max())
              for m in [(0, 1, 0), (0, 0, 1), (1, 0, 0), (0, .5, .5), (.3, .3, .4)])
    check("T12 every core tensor reconstructs the family", err < 1e-10, f"max err {err:.2e}")

    # T13 direction length varies around the spiral, which is why displacement
    # is specified directly rather than as a raw weight
    norms = [direction_norm(st, p) for p in fine[::10]]
    check("T13 direction length varies (so weight != displacement)",
          max(norms) / min(norms) > 1.05,
          f"|m| from {min(norms):.2f} to {max(norms):.2f}, {100*(max(norms)/min(norms)-1):.0f} pct")

    # ---- reordering and direct entry -------------------------------------
    # T14 a reorder is a pure permutation of the rows. Written against an
    # independently computed expected matrix, not against the code's own idea
    # of what it did.
    c3 = SdmController(F, seed=0, bounds=box)
    before = c3.family.copy()
    perm = [0, 2, 1, 3, 4, 5, 6, 7, 8, 9][:F.shape[0]]
    perm = perm + list(range(len(perm), F.shape[0]))
    c3.reorder(perm)
    want = np.array([before[q] for q in perm])
    check("T14 reorder permutes the rows exactly",
          np.array_equal(c3.family, want), "rows 1 and 2 swapped")

    # T15 the decomposition must describe the family it claims to. U_0's rows
    # are indexed by gradation, so a reorder that permuted the rows and left the
    # state alone would leave m(B) derived from the old arrangement.
    #
    # The first version of this test asserted that the spiral still tunes the
    # row it points at, and PASSED with the rebuild deleted — preview() selects
    # the row by indexing the family directly, so that claim can never fail.
    # Vacuous, and caught only by removing the fix and re-running. The invariant
    # below is the one the rebuild actually provides.
    check("T15 a reorder rebuilds the decomposition from the new order",
          np.array_equal(c3.state.family0, c3.family),
          "state.family0 tracks the permuted family")

    # T16 moving off either end is refused rather than wrapping
    c4 = SdmController(F, seed=0, bounds=box)
    ends = (c4.move(0, -1), c4.move(F.shape[0] - 1, 1))
    check("T16 moving past either end does nothing",
          ends == (None, None) and np.array_equal(c4.family, F),
          "first up and last down both refused")

    # T17 a direct edit sets exactly one row, and the family it leaves behind is
    # what the next traverse starts from
    c5 = SdmController(F, seed=0, bounds=box)
    keep = c5.family.copy()
    newrow = keep[4] + np.array([0.31, -0.22, 0.13, 0.05])[:F.shape[1]]
    c5.set_row(4, newrow)
    others = all(np.array_equal(c5.family[i], keep[i])
                 for i in range(F.shape[0]) if i != 4)
    check("T17 a direct latent edit changes exactly that row",
          np.allclose(c5.family[4], newrow) and others, "row 4 set, nine untouched")
    check("T17b a direct edit also rebuilds the decomposition",
          np.array_equal(c5.state.family0, c5.family),
          "state.family0 tracks the edited family")

    # T18 values outside the latent box are clamped, and the clamping is
    # reported. Silently accepting them would leave admissible_t with no travel
    # at all, so the traverse would lock up with no stated reason.
    c6 = SdmController(F, seed=0, bounds=box)
    far = np.array([99.0, -99.0, 0.0, 0.0])[:F.shape[1]]
    clamped = c6.set_row(2, far)
    inside = bool((c6.family[2] >= box[:, 0] - 1e-9).all()
                  and (c6.family[2] <= box[:, 1] + 1e-9).all())
    check("T18 out-of-box latents are clamped and reported",
          inside and clamped == [0, 1], f"clamped {clamped}, row inside the box {inside}")

    n = sum(RESULTS)
    print(f"\n  {n} of {len(RESULTS)} passed")
    print("==========================================================\n")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
