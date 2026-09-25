# RAVE / SDM earcons

Tuning a graded family of earcons using raw inputs, and with the
Spiral Discovery Method (SDM), where the sound comes from a RAVE model
played through SuperCollider (based on Csapo and Baranyi, JACIII 16(2), 2012).

## What you need

- SuperCollider 3.14 with the `nn.ar` extension. You can get the extension
[here][https://github.com/elgiano/nn.ar], and
[here][https://doc.sccode.org/Guides/UsingExtensions.html] is how to install an
extension.
- `RAVE/percussion.ts`, already in the repo - also accessible from
[IRCAM][https://acids-ircam.github.io/rave_models_download]
- **uv** for the Python side. Nothing needs to be installed by hand: `uv` creates
  a local `.venv` in this folder and fetches everything, including a suitable
  Python interpreter.

Only two Python dependencies are required: numpy and scipy, and scipy only because the
extended `sktensor` (originally from [here][https://github.com/mnick/scikit-tensor])
in `sdm-src/` uses it. The OSC layer and the web layer are standard library on purpose.


## Running it, in order

For demonstrations 1 to 3, open the supercollider file inside the folder and execute the
blocks inside it, one after the other.

For demonstration 4, SuperCollider must be started first and the first block must be run
before Python is any use. The Python side only tells the Supercollider side what to play.

Therefore, the following steps should be carried out - inside the demonstrations4 folder:

**1. SuperCollider.** Open `supercollider/sdm.scd`. Put the cursor anywhere
inside `BLOCK 1` and press Cmd+Enter. Wait about 30 seconds. It boots the
server, loads the model, runs a six-part self-test, measures how many voices
this machine can run, and then measures the default family. It is silent
throughout.

Watch for the last line. `SELF-TEST PASSED. System ARMED` means go on. If it
says `DISARMED`, stop: nothing will make sound until the failure is fixed, which
is deliberate.

**2. Python.** In a terminal, from demonstrations4 folder, the first time only:

```
uv sync
```

That makes `.venv` here and installs numpy and scipy against the pinned
`uv.lock`. Then, every time:

```
uv run python -m sdmearcon.app
```

A browser opens at `http://127.0.0.1:8731/`. The header should say
`connected, armed`. If it says `not connected`, SuperCollider is not running
step 1, or its self-test failed.

**3. Make sound.** In the browser, raise **master gain** from 0. It starts at
zero every session on purpose and never rises by itself. Then click any row in
the table to play that gradation, or press **sweep family**.

## The panel

The left column is the controls, split the way the paper splits them.

- **Spiral (SDM)**: pick which earcon the spiral tunes by clicking its row, or
  press "whole family" to move all ten together. Then `traverse` advances the
  core tensor mix and that earcon's tuning weight at the same time, so it
  follows a spiral rather than a straight line: broadly along the principal
  component, deviating as it goes. `sensitivity` is the second control from
  section 4 of the paper. Both are dimensionless: traverse is in turns,
  sensitivity in **family widths per turn**, so 1.0 means one turn moves the
  earcon by about the family's own spread.
- **Envelope**: attack, sustain, release. Direct controls, not tuned.
- **Structure**: rhythm pattern and urgency. Urgency is a uniform offset added
  to every latent, so it changes the sound and forces a re-measurement.
- **Presets**: save the current family to `output/presets/<name>.json`, and load
  one back. See below.
- **Output**: master gain, sweep, panic.

Each earcon is **recorded once** when it is measured, and played back from that
recording. The model emits a continuously varying texture even at a fixed
latent, so gating it live produced a different sound on every repetition. The
consequence is that the envelope is part of the recording: changing it
re-renders all ten.

**Moving the traverse previews. Releasing it commits**: the edit is baked into
the family, the family becomes the new origin, and the traverse recentres on
zero. A per-earcon edit re-measures only that earcon, about 0.8 s; a
whole-family move re-measures all ten, about 3 s. It has to re-measure at all
because the level a set of latents produces cannot be predicted from the
latents, so nothing is auditioned until it has been measured. Rows are greyed
while a measurement is in flight.

## Presets

`save` writes everything needed to reproduce a family: the ten latent vectors,
the SDM position that produced them (seed, phase, sensitivity, selected
gradation), and the direct controls. `load` restores all of that.

Two things are recorded in the file but **not restored**:

- **The measurements** (peak, K-rms, crest, gain). They describe what one model
  produced on one machine. Restoring them would mean auditioning a family on the
  strength of numbers from a file, which breaks the rule the whole safety design
  rests on: nothing is played before it has been measured here, now. They are
  kept as provenance so you can see what it sounded like when you saved it, and
  the family is re-measured on load.
- **The master gain.** It starts where it is and only you raise it. A file that
  could raise it would be a way around that.

A preset made with a model of different dimensionality is refused rather than
half-applied. The files are plain JSON and are meant to be readable and
diffable, so a family can go into a paper's supplementary material as it stands.

## Stopping

- **panic** in the browser, or `~panic.()` in SuperCollider: stops everything and
  sets the gain to zero. Nothing is audible again until you raise it.
- **Cmd+.** in SuperCollider is the global stop and always works, whatever else
  is going on.

## Checking the parts on their own

```
uv run python -m sdmearcon.selftest          # the SDM controller, 14 checks
uv run python -m sdmearcon.selftest_bridge   # the OSC bridge and presets, against a fake sclang, 18 checks
```

Neither needs SuperCollider or an audio device. The SuperCollider self-test is
built into `BLOCK 1` and cannot be skipped.


## Two ports

SuperCollider listens on **57120** (its own langPort), Python on **57121**.
If either is taken, `sdmearcon/session.py` and the OSC section of `sdm.scd` are
where to change them.
