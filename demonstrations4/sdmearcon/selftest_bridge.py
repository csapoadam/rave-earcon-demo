"""End-to-end test of the Python side against a FAKE sclang.

The real sclang is on a machine this code cannot reach from the development
environment, so the bridge is proved here instead: a stand-in speaks the exact
schema from NOTES.md 24.3, and the Session must drive it and absorb the replies.

Run:  python3 -m sdmearcon.selftest_bridge
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import numpy as np

import json
import shutil
import tempfile

from . import presets
from .osc import decode, encode
from .presets import PresetError
from .session import Session

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else '*** FAIL ***':14s} {name:46s} {detail}")


class FakeSclang(threading.Thread):
    """Speaks the sclang half of the schema. Deliberately literal."""
    daemon = True

    def __init__(self, port=57120, reply_to=57121):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.3)
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.reply_to = ("127.0.0.1", reply_to)
        self.seen = []
        self.families = []
        self.grad1_rows = []
        self.reorders = []
        self.n = 10
        # When set, the next /sdm/grad1 is answered with /sdm/error and
        # nothing else — exactly what ~regrad does when it refuses.
        self.refuse_next = False
        self.stop_flag = threading.Event()

    def send(self, addr, *args):
        self.out.sendto(encode(addr, *args), self.reply_to)

    def run(self):
        while not self.stop_flag.is_set():
            try:
                data, _ = self.sock.recvfrom(65536)
            except (socket.timeout, OSError):
                continue
            addr, args = decode(data)
            self.seen.append((addr, args))
            if addr == "/sdm/ping":
                self.send("/sdm/pong", 1, 0.0, 4)
                self.send("/sdm/limits", -13.0, 12.0, 25.0)
            elif addr == "/sdm/reorder":
                # A permutation only. The real sclang re-measures NOTHING here:
                # it permutes the rows, the gains, the usable flags, the cached
                # measurements and the recorded buffers, then acknowledges.
                self.reorders.append([int(v) for v in args[1:]])
                self.send("/sdm/reordered", int(args[0]))
            elif addr == "/sdm/family":
                n, p = int(args[0]), int(args[1])
                rows = np.array(args[2:], dtype=float).reshape(n, p)
                self.families.append(rows)
                self.n = n
                time.sleep(0.05)
                # Plausible measurements: level rises across the family, and one
                # gradation is deliberately unusable.
                for i in range(n):
                    peak = -30.0 + 2.0 * i
                    krms = peak - 22.0
                    usable = 0 if i == 3 else 1
                    self.send("/sdm/grad", i, peak, krms, 22.0, 1.4,
                              -10.0 if usable else -999.0, usable)
                self.send("/sdm/familyDone", n - 1, n, -35.0)
            elif addr == "/sdm/grad1":
                idx, p = int(args[0]), int(args[1])
                self.grad1_rows.append((idx, np.array(args[2:2 + p], dtype=float)))
                if self.refuse_next:
                    self.refuse_next = False
                    time.sleep(0.05)
                    self.send("/sdm/error", "no prior family measurement")
                    continue
                time.sleep(0.05)
                # Re-measures one, then renormalises and reports all of them,
                # which is what the SuperCollider ~regrad does.
                for i in range(self.n):
                    peak = -28.0 + 2.0 * i
                    self.send("/sdm/grad", i, peak, peak - 22.0, 22.0, 1.4,
                              -10.0, 1)
                self.send("/sdm/familyDone", self.n, self.n, -34.0)


def main() -> int:
    print("\n============ SDM BRIDGE SELF-TEST (fake sclang) ============")
    fake = FakeSclang()
    fake.start()
    time.sleep(0.2)
    s = Session()
    try:
        # B1 handshake
        s.ping(); time.sleep(0.4)
        check("B1 ping -> pong, armed state received",
              s.armed is True and s.pool_size == 4, f"armed={s.armed} pool={s.pool_size}")

        # B2 commit sends a well-formed family
        s.apply({"t": 0.2, "sens": 0.5, "target": "all"})
        s.commit(); time.sleep(0.6)
        sent = [m for m in fake.seen if m[0] == "/sdm/family"]
        ok = len(sent) == 1 and int(sent[0][1][0]) == 10 and int(sent[0][1][1]) == 4 \
             and len(sent[0][1]) == 2 + 40
        check("B2 /sdm/family is well formed", ok,
              f"{len(sent)} sent, {len(sent[0][1]) - 2 if sent else 0} values")

        # B3 CLOSED FORM: what went on the wire must equal the controller's own
        # preview plus urgency, computed independently.
        s.apply({"urgency": 0.25, "t": 0.15, "target": "all"})
        expect = s.ctl.preview(0.15) + 0.25
        s.commit(); time.sleep(0.7)
        err = np.abs(fake.families[-1] - expect).max()
        check("B3 sent family == preview() + urgency", err < 1e-5, f"max err {err:.2e}")

        # B4 replies are absorbed and reach the UI snapshot
        snap = s.snapshot()
        row3 = snap["rows"][3]
        ok = (snap["n_usable" ] if False else s.n_usable) == 9 and row3["usable"] is False \
             and snap["rows"][0]["peak"] == -30.0 and snap["target_k"] == -35.0
        check("B4 measurements absorbed into the snapshot", ok,
              f"usable={s.n_usable}, row3 usable={row3['usable']}, target={snap['target_k']}")

        # B5 the traverse is limited by the latent box, not clamped per value
        s.apply({"t": 999.0, "sens": 3.0, "target": "all"})
        lo, hi = s.t_limits()
        fam = s.family()
        box = np.array([[-6.2, 5.7], [-9.1, 5.0], [-3.1, 7.2], [-5.0, 6.0]])
        inside = bool((fam >= box[:, 0] - 1e-6).all() and (fam <= box[:, 1] + 1e-6).all())
        check("B5 traverse limited to the admissible interval",
              inside and s.t <= hi + 1e-9, f"t {s.t:.3f} in [{lo:.2f}, {hi:.2f}]")

        # B6 panic zeroes the master gain and says so downstream
        s.set_master(0.8); time.sleep(0.1)
        s.panic(); time.sleep(0.2)
        got_panic = any(m[0] == "/sdm/panic" for m in fake.seen)
        check("B6 panic sent and master gain zeroed",
              got_panic and s.master == 0.0, f"master={s.master}")

        # B7 direct controls travel with every commit
        s.apply({"t": 0.0, "urgency": 0.0, "sens": 0.5, "target": "all",
                 "atk": 0.02, "dur": 0.4, "rel": 0.5, "pattern": "warning"})
        s.commit(); time.sleep(0.5)
        evt = [m for m in fake.seen if m[0] == "/sdm/evt"][-1][1]
        pat = [m for m in fake.seen if m[0] == "/sdm/pattern"][-1][1]
        check("B7 envelope and pattern sent with the family",
              abs(evt[1] - 0.4) < 1e-6 and len(pat) == 2,
              f"evt={[round(x,3) for x in evt]} pattern={[round(x,3) for x in pat]}")
        # B8 a per-gradation edit re-measures ONE gradation, not ten. This is
        # the whole point of the per-gradation control: nine of the ten
        # measurements are still valid, so re-measuring them is wasted time.
        before = len(fake.families)
        s.apply({"target": 6, "t": 0.12, "sens": 0.5})
        expect_row = s.ctl.preview(0.12)[6] + s.urgency
        s.commit(); time.sleep(0.7)
        ok = (len(fake.grad1_rows) == 1 and fake.grad1_rows[0][0] == 6
              and len(fake.families) == before)
        err = np.abs(fake.grad1_rows[0][1] - expect_row).max() if fake.grad1_rows else 9e9
        check("B8 per-gradation edit sends /sdm/grad1 only",
              ok and err < 1e-5, f"idx {fake.grad1_rows[0][0] if fake.grad1_rows else '-'}, "
              f"row err {err:.2e}, no /sdm/family resent")

        # B9 CLOSED FORM: only the edited row differs from the previous family,
        # so the SDM control really is local
        s.apply({"target": 2, "t": 0.1})
        prev = s.ctl.family.copy()
        after = s.ctl.preview(0.1)
        moved = np.flatnonzero(np.abs(after - prev).sum(1) > 1e-12)
        check("B9 only the selected gradation moves", list(moved) == [2],
              f"rows moved {list(moved)}")

        # B10 whole-family mode still sends the full family
        before = len(fake.families)
        s.apply({"target": "all", "t": 0.08})
        s.commit(); time.sleep(0.7)
        check("B10 whole-family edit sends /sdm/family",
              len(fake.families) == before + 1, f"{len(fake.families) - before} family message")
        # B18 changing the ENVELOPE forces a full re-render even in
        # per-gradation mode. The envelope is baked into every recorded buffer,
        # so re-rendering one earcon would leave the other nine carrying the old
        # one. Written to fail if that guard is removed.
        s.apply({"target": 5, "t": 0.04, "atk": 0.005})
        s.commit(); time.sleep(0.7)
        before_fam, before_g1 = len(fake.families), len(fake.grad1_rows)
        s.apply({"target": 5, "t": 0.04})          # same envelope -> one gradation
        s.commit(); time.sleep(0.7)
        one_only = (len(fake.families) == before_fam
                    and len(fake.grad1_rows) == before_g1 + 1)
        before_fam = len(fake.families)
        s.apply({"target": 5, "t": 0.04, "atk": 0.09})   # envelope moved -> all ten
        s.commit(); time.sleep(0.7)
        all_ten = len(fake.families) == before_fam + 1
        check("B18 envelope change re-renders all ten", one_only and all_ten,
              "same envelope -> /sdm/grad1, changed envelope -> /sdm/family")

        # B19 a REFUSAL has to end the busy state. sclang answers a refused
        # edit with /sdm/error and nothing else: no /sdm/grad, no
        # /sdm/familyDone. If that does not clear the flag, the panel sits on
        # "measuring..." for the rest of the session with no visible reason,
        # which is exactly what happened on 2026-09-24. Written so that it
        # fails if the clearing is removed from Session._on_error.
        fake.refuse_next = True
        s.apply({"target": 5, "t": 0.05})
        s.commit(); time.sleep(0.7)
        snap = s.snapshot()
        check("B19 a refusal clears the busy flag",
              (snap["measuring"] is False) and ("no prior family measurement" in snap["error"]),
              "measuring=%s, error shown=%s" % (snap["measuring"], bool(snap["error"])))

        # ---- reordering and direct latent entry ----
        # B20 moving an earcon is a REORDER, not a new family: it must send
        # /sdm/reorder and must NOT trigger any measurement. If it fell back to
        # re-sending the family, ten perfectly good renders would be thrown away
        # on a change that alters no sound. Written to fail either way round.
        s.apply({"target": 5, "t": 0.0})
        s.commit(); time.sleep(0.7)
        before_fam, before_g1, before_ro = (len(fake.families), len(fake.grad1_rows),
                                            len(fake.reorders))
        rows_before = [tuple(r["z"]) for r in s.snapshot()["rows"]]
        peaks_before = [r["peak"] for r in s.snapshot()["rows"]]
        s.move_gradation(3, 1); time.sleep(0.4)
        snap = s.snapshot()
        rows_after = [tuple(r["z"]) for r in snap["rows"]]
        peaks_after = [r["peak"] for r in snap["rows"]]
        swapped = (rows_after[3] == rows_before[4] and rows_after[4] == rows_before[3]
                   and peaks_after[3] == peaks_before[4]
                   and peaks_after[4] == peaks_before[3])
        no_measure = (len(fake.families) == before_fam
                      and len(fake.grad1_rows) == before_g1)
        check("B20 a move reorders without re-measuring",
              swapped and no_measure and len(fake.reorders) == before_ro + 1,
              "rows and measurements swapped together, no /sdm/family or /sdm/grad1")

        # B21 the permutation on the wire has to be a real permutation, or
        # sclang refuses it and the two sides silently disagree about which
        # earcon is which.
        perm = fake.reorders[-1]
        check("B21 the permutation sent is a permutation",
              sorted(perm) == list(range(len(perm))) and perm[3] == 4 and perm[4] == 3,
              f"{perm}")

        # B22 typing latents in re-measures ONLY that row, like any other
        # per-gradation edit, and what goes on the wire is what was typed.
        before_fam, before_g1 = len(fake.families), len(fake.grad1_rows)
        typed = [0.21, -0.34, 0.55, -0.12][:s.ctl.family.shape[1]]
        s.set_row(6, typed); time.sleep(0.7)
        idx, sent = fake.grad1_rows[-1]
        check("B22 a direct latent edit sends only that row",
              len(fake.families) == before_fam
              and len(fake.grad1_rows) == before_g1 + 1
              and idx == 6 and np.allclose(sent, typed, atol=1e-9),
              f"grad1 for row {idx}, values match to 1e-9")

        # ---- presets ----
        # Redirect the preset folder so the test never touches real saves.
        tmpdir = Path(tempfile.mkdtemp(prefix="sdm-presets-"))
        real_dir = presets.PRESET_DIR
        presets.PRESET_DIR = tmpdir
        try:
            s.apply({"target": "all", "t": 0.0, "sens": 0.8, "urgency": 0.1,
                     "atk": 0.03, "dur": 0.35, "rel": 0.4, "pattern": "error"})
            s.commit(); time.sleep(0.7)
            saved_family = s.ctl.family.copy()
            s.set_master(0.9); time.sleep(0.1)
            path = Path(s.export_preset("round_trip"))

            # P1 the file is valid JSON with the documented shape
            data = json.loads(path.read_text())
            ok = (data["format"] == presets.FORMAT and data["n_gradations"] == 10
                  and data["n_params"] == 4 and len(data["family"]) == 10
                  and "measured_at_export" in data)
            check("B11 preset file is valid and complete", ok, path.name)

            # P2 CLOSED FORM round trip: change everything, import, and the
            # family must come back bit-identically.
            s.apply({"target": 4, "t": 0.3, "sens": 2.0, "urgency": -0.4,
                     "atk": 0.01, "dur": 0.1, "rel": 0.9, "pattern": "single"})
            s.commit(); time.sleep(0.7)
            assert not np.allclose(s.ctl.family, saved_family)
            s.import_preset("round_trip"); time.sleep(0.8)
            err = float(np.abs(s.ctl.family - saved_family).max())
            check("B12 import restores the family exactly", err == 0.0,
                  f"max err {err:.2e}")

            # P3 the direct controls come back too
            ok = (abs(s.sens - 0.8) < 1e-9 and abs(s.urgency - 0.1) < 1e-9
                  and abs(s.atk - 0.03) < 1e-9 and abs(s.dur - 0.35) < 1e-9
                  and abs(s.rel - 0.4) < 1e-9 and s.pattern == "error")
            check("B13 import restores the direct controls", ok,
                  f"sens={s.sens} urgency={s.urgency} pattern={s.pattern}")

            # P4 import RE-MEASURES rather than trusting the file's gains
            fams_before = len(fake.families)
            s.import_preset("round_trip"); time.sleep(0.8)
            check("B14 import re-measures the whole family",
                  len(fake.families) == fams_before + 1, "one /sdm/family sent")

            # P5 the master gain is NOT restored from the file. A preset that
            # could raise the gain would be a way around the rule that it starts
            # at zero and only a person raises it.
            s.set_master(0.0); time.sleep(0.1)
            s.import_preset("round_trip"); time.sleep(0.8)
            check("B15 import does not restore the master gain",
                  s.master == 0.0, f"master stayed {s.master}")

            # P6 a preset from a different model is refused, not half-applied
            bad = json.loads(path.read_text())
            bad["family"] = [[0.0, 0.0, 0.0] for _ in range(10)]   # 3 params, not 4
            (tmpdir / "wrong_model.json").write_text(json.dumps(bad))
            fam_now = s.ctl.family.copy()
            try:
                s.import_preset("wrong_model")
                refused = False
            except PresetError:
                refused = True
            check("B16 mismatched preset is refused, nothing changed",
                  refused and np.array_equal(s.ctl.family, fam_now), "4 params vs 3")

            # P7 a name that could escape the presets folder is refused
            escaped = True
            for nm in ("../evil", "a/b", "..", ""):
                try:
                    presets.safe_name(nm)
                    escaped = False
                except PresetError:
                    pass
            check("B17 unsafe preset names are refused", escaped,
                  "../evil, a/b, .., empty")

            # B23 a preset restores the family exactly, but usability is not a
            # property of the family alone: it is the family plus a fresh
            # measurement, through a loudness target derived as
            # peakCeil - max(crest) over the ten. That target is a MAXIMUM over
            # ten noisy numbers, so one gradation measuring slightly less spiky
            # lowers the boost available to everybody and any gradation near the
            # cap is refused. A family can be saved a fraction of a dB from that
            # cliff with nothing saying so, which is how a preset that played
            # comes back silent. The round trip cannot be made deterministic, so
            # the tool has to SAY that the verdict changed.
            claim = json.loads(path.read_text())
            claim["measured_at_export"] = [
                {"i": i, "peak_db": -20.0, "krms_db": -40.0, "crest_db": 20.0,
                 "gain_db": 10.0, "usable": True} for i in range(10)]
            (tmpdir / "optimistic.json").write_text(json.dumps(claim))
            s.last_error = ""
            s.import_preset("optimistic")
            time.sleep(0.8)
            msg = s.snapshot()["error"]
            # the fake reports gradation 3 unusable and targetK -35.0, against
            # the file's claim of all ten usable at -30.0
            check("B23 a preset whose verdict changed on re-measure says so",
                  ("3" in msg) and ("-5.00 dB" in msg or "-5.0" in msg) and msg != "",
                  msg[:72] if msg else "NOTHING REPORTED")
        finally:
            presets.PRESET_DIR = real_dir
            shutil.rmtree(tmpdir, ignore_errors=True)
    finally:
        s.close()
        fake.stop_flag.set()

    n = sum(RESULTS)
    print(f"\n  {n} of {len(RESULTS)} passed")
    print("============================================================\n")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
