// ============================================================
// SDM EARCONS
// ============================================================
//
// ONE CLICK, THEN ONE LINE.
//
// Put the cursor anywhere inside BLOCK 1 (far below) and press Cmd+Enter.
// It boots, self-tests, arms, AND measures the default family. About 30 s,
// silent. When it finishes it says "type ~go.()".
//
// Then come back up HERE and keep your cursor in this block. Put it on any
// line below and press Cmd+Enter to run just that line. No scrolling.
//
//   Cmd+Enter   run the line the cursor is on, or the enclosing ( ) block
//   Cmd+.       stop all sound immediately (SuperCollider's global stop)
//
// ---- the command line: cursor on a line, Cmd+Enter ----

// --- audible ---
~go.()                  // raise gain to 1.0 and play the whole family
~sweep.()               // play the family again
~audition.(3)           // play one gradation
~setMaster.(0.5)        // master gain. 1.0 is the normal operating point

// --- silent: these print, or take ~10 s and print a table ---
~status.()              // what is loaded, armed, measured
~verifyPlay.(3, 6)      // play gradation 3 six times and measure each one.
                        // Works with the master gain at 0.
~setPattern.(\warning)  // single / info / warning / error / success
~setUrgency.(0.3)       // uniform latent offset. re-measures (~3 s)
~again.()               // re-measure the current family        (~10 s)
~useFamily.(~famFileOpsToSystem)   // measure a different family (~10 s)
~useFamily.(~famNetworkToUser)

// --- stop ---
~panic.()               // stop everything AND set the gain to 0.
                        // Nothing is audible again until ~setMaster.
                        // Cmd+. also stops sound, without touching the gain.

// ------------------------------------------------------------
// Everything below is setup. You should not need to touch it after the
// first click of BLOCK 1. BLOCK 3 at the very bottom is teardown.
//
// This file supersedes the five files now in archive/. The safety test
// that used to be a separate file is self-test T1 below, and it runs on
// every setup whether anyone remembers it or not. If it fails the system
// stays DISARMED and ~audition refuses to make any sound.
// ------------------------------------------------------------


// ############################################################
// BLOCK 1  SETUP AND SELF-TEST.  Click once. Silent. ~20 s.
// ############################################################
(
s.options.memSize = 65536;

// ---- levels, all in one place ----
~ceiling    = 0.25;    // -12.04 dBFS, the master limiter. Never engages in
                       // normal operation; it is the safety net only.
~peakCeilDb = -13.0;   // per-gradation peak target, 1 dB under the limiter
~maxBoostDb = 12.0;    // never boost a quiet gradation by more than this
~maxCrestDb = 25.0;    // warn above this: one spiky gradation drags the
                       // whole family down in level
~anaDur     = 0.70;    // analysis window. With a persistent voice the onset
                       // is ~0, so this only has to cover the event.
~onsetThr   = 1e-4;    // -80 dBFS, onset detection threshold
~evt        = (atk: 0.005, dur: 0.25, rel: 0.30);
~settle     = 0.10;    // let new latents settle before triggering

~scDir = PathName(thisProcess.nowExecutingPath).pathOnly;


~modelPath = PathName(thisProcess.nowExecutingPath).pathOnly +/+ "../RAVE/Percussion.ts";

~projRoot  = ~scDir ++ "../";
~modelPath = ~projRoot ++ "../RAVE/percussion.ts";
~calDir    = ~projRoot ++ "output/calibration/";

~build = "2026-09-24 reorder + direct latents";
~armed = false; ~ready = false; ~gains = nil; ~usable = nil; ~family = nil;
~activeModel = nil; ~voiceModel = nil; ~pool = nil; ~familyRaw = nil;
~lastMeas = nil;   // the cached per-gradation measurements ~regrad splices into
~poolSize = 1; ~masterAmp = 0;

s.waitForBoot {
    var t1, t2, t3, t4, allPass;

    // ======== SAFETY LAYER ========
    // Voices never touch the hardware bus. This is the only node that
    // writes to it, and ReplaceOut means a stray synth writing to bus 0
    // is overwritten rather than added.
    SynthDef(\sdmSafety, { |inBus = 16, amp = 0, ceiling = 0.25, reportRate = 5|
        var sig, bad, badMax, limited, absmax, pk, trig;
        sig = In.ar(inBus, 2);
        sig = LeakDC.ar(sig);
        bad = sig.collect { |ch| CheckBadValues.ar(ch, 0, 0) };
        sig = [0, 1].collect { |i| Select.ar(bad[i] > 0, [sig[i], DC.ar(0)]) };
        limited = Limiter.ar(sig, ceiling, 0.01).clip2(ceiling);
        trig    = Impulse.kr(reportRate);
        absmax  = limited[0].abs.max(limited[1].abs);
        pk      = RunningMax.ar(absmax, K2A.ar(trig));
        badMax  = RunningMax.ar(bad[0].max(bad[1]), K2A.ar(trig));
        SendReply.kr(trig, '/sdmSafetyMon', [A2K.kr(pk), A2K.kr(badMax)]);
        ReplaceOut.ar(0, limited * Lag.kr(amp.clip(0, 1), 0.05));
    }).add;

    SynthDef(\sdmOutMon, { |reportRate = 5|
        var sig = In.ar(0, 2), trig = Impulse.kr(reportRate);
        SendReply.kr(trig, '/sdmOutMon',
            [A2K.kr(RunningMax.ar(sig[0].abs.max(sig[1].abs), K2A.ar(trig)))]);
    }).add;

    // ======== ANALYSER ========
    // Peak, K-weighted RMS, bad-value flag, and onset time.
    SynthDef(\sdmAnalyse, { |inBus = 16, replyId = 0, dur = 0.7, onsetThr = 1e-4|
        var sig, bad, mono, kw, sumsq, absmax, pk, rms, elapsed, onset, t;
        sig = In.ar(inBus, 2);
        bad = sig.collect { |ch| CheckBadValues.ar(ch, 0, 0) };
        sig = [0, 1].collect { |i| Select.ar(bad[i] > 0, [sig[i], DC.ar(0)]) };
        absmax  = sig[0].abs.max(sig[1].abs);
        pk      = RunningMax.ar(absmax, 0);
        elapsed = Line.ar(0, dur, dur);
        onset   = RunningMin.ar(Select.ar(absmax > onsetThr, [DC.ar(999), elapsed]), 0);
        mono    = (sig[0] + sig[1]) * 0.5;
        kw      = BHiPass.ar(BHiShelf.ar(mono, 1500, 1.0, 4.0), 38, 0.707);
        sumsq   = Integrator.ar(kw.squared, 1.0);
        rms     = (A2K.kr(sumsq) / (dur * SampleRate.ir)).sqrt;
        t       = Done.kr(Line.kr(0, 1, dur));
        SendReply.kr(t, '/sdmAnalysis',
            [replyId, A2K.kr(pk), rms, A2K.kr(bad[0].max(bad[1])), A2K.kr(onset)]);
        FreeSelf.kr(TDelay.kr(t, 0.05));
    }).add;

    // ======== RENDER TO BUFFER ========
    // An earcon is now a RECORDED BUFFER, not a live window onto the decoder.
    //
    // Measured (NOTES.md 30.1): held at a CONSTANT latent, this model emits a
    // continuously varying, non-repeating texture. Consecutive 0.35 s windows
    // of the same latent cross-correlate at about 0.21 and their peak levels
    // span 9.1 dB. Gating that stream with an envelope therefore produced a
    // different sound every time, and the measurement described one excerpt
    // while the audition played another. Worse, measurement used pool voice j
    // while audition always used pool voice 0, which are separate NN instances
    // with separate internal state.
    //
    // Recording each gradation once, during its measurement, and playing that
    // buffer back fixes all of it: every play is identical, and what was
    // measured is bit-for-bit what is heard.
    SynthDef(\sdmRecord, { |inBus = 16, bufnum = 0|
        RecordBuf.ar(In.ar(inBus, 2), bufnum, loop: 0, doneAction: 2);
    }).add;

    SynthDef(\sdmPlayBuf, { |out = 16, bufnum = 0, gain = 1|
        var sig = PlayBuf.ar(2, bufnum, BufRateScale.kr(bufnum), 1, 0, 0, doneAction: 2);
        Out.ar(out, sig * gain);
    }).add;

    // ======== VOICES ========
    // Generic contract: out, gain, t_trig, atk, dur, rel, p0..p3.
    // All voices are PERSISTENT (no doneAction). The envelope is a gate,
    // not the voice's lifetime. This is the fix for the 175 ms per-Synth
    // startup cost measured on the RAVE decoder.
    // `out` is a control, so one voice can be switched between the
    // analysis bus and the audition bus without being recreated.
    SynthDef(\sdmVoiceSine, { |out = 16, gain = 0.2, t_trig = 0,
                              atk = 0.005, dur = 0.25, rel = 0.30,
                              p0 = 4, p1 = -1, p2 = 0, p3 = 0|
        var sig = SinOsc.ar((2 ** p0.clip(3, 8)) * 27.5, 0, 10 ** p1.clip(-5, 0));
        var env = EnvGen.kr(Env([0, 1, 1, 0], [atk, dur, rel], \sin), gate: t_trig);
        Out.ar(out, (sig * env * gain) ! 2);
    }).add;

    // Pathological voice, for the safety self-test only.
    //   p0=0 normal -13.98 dBFS, 1 NaN, 2 loud +16.90 dBFS, 3 tiny -93.98
    SynthDef(\sdmVoiceTest, { |out = 16, gain = 1, t_trig = 0,
                              atk = 0.005, dur = 0.25, rel = 0.02, p0 = 0|
        var norm = SinOsc.ar(440, 0, 0.2), nan = DC.ar(0) / DC.ar(0);
        var loud = SinOsc.ar(440, 0, 7.0), tiny = SinOsc.ar(440, 0, 0.00002);
        var sig  = Select.ar(p0.clip(0, 3), [norm, nan, loud, tiny]);
        var env  = EnvGen.kr(Env([0, 1, 1, 0], [atk, dur, rel], \sin), gate: t_trig);
        Out.ar(out, (sig * env * gain) ! 2);
    }).add;

    s.sync;

    // ======== BUSSES, GROUPS, NODES ========
    ~auditionBus = Bus.audio(s, 2);
    ~anaBus      = 10.collect { Bus.audio(s, 2) };
    // one buffer per gradation, long enough for the whole analysis window
    ~buf = 10.collect { Buffer.alloc(s, (s.sampleRate * ~anaDur).ceil.asInteger, 2) };
    ~gVoice  = Group.new(s, \addToTail);
    ~gAirV   = Group.after(~gVoice);
    ~gAirA   = Group.after(~gAirV);
    ~gSafety = Group.after(~gAirA);
    ~gMon    = Group.after(~gSafety);
    s.sync;
    ~safety = Synth(\sdmSafety, [\inBus, ~auditionBus, \amp, 0, \ceiling, ~ceiling], ~gSafety);
    ~outMon = Synth(\sdmOutMon, [], ~gMon);

    ~monPeak = 0; ~monBad = 0; ~outPeak = 0;
    // The limiter should never act in normal operation, so when it does we want
    // to know rather than deduce it from how something sounded.
    ~limitHits = 0;
    OSCdef(\sdmSafetyMon, { |m|
        ~monPeak = m[3]; ~monBad = m[4];
        if(~monPeak >= (~ceiling * 0.995)) { ~limitHits = ~limitHits + 1 };
    }, '/sdmSafetyMon');
    OSCdef(\sdmOutMon,    { |m| ~outPeak = m[3] },                 '/sdmOutMon');

    // ======== MODEL REGISTRY ========
    ~models = IdentityDictionary.new;
    ~registerModel = { |key, synthDefName, paramNames, nominal, persistent = false|
        ~models[key] = (synthDef: synthDefName, nParams: paramNames.size,
                        paramNames: paramNames, nominal: nominal, persistent: persistent);
    };
    ~registerModel.(\sine, \sdmVoiceSine, ["pitchOct", "log10amp"], [[4, 8], [-4, 0]], true);

    // ======== CONTROLS ========
    ~setMaster = { |amp| ~masterAmp = amp.clip(0, 1); ~safety.set(\amp, ~masterAmp);
                         "  master gain -> %".format(~masterAmp).postln };
    ~panic = { ~gVoice.freeAll; ~gAirV.freeAll; ~gAirA.freeAll;
               ~voice = nil; ~pool = nil; ~voiceModel = nil; ~safety.set(\amp, 0); ~masterAmp = 0;
               "  PANIC: all voices freed, master gain 0".postln };
    ~rowArgs = { |row| var a = []; row.do { |v, j| a = a ++ [("p" ++ j).asSymbol, v] }; a };

    // ======== POOL ========
    // T5 measured that 4 persistent voices run fine. Measurement now uses
    // them in parallel, which takes a family from about 10 s to about 3 s.
    // That is the difference between a tuning loop and a batch job.
    ~makePool = { |modelKey|
        var m = ~models[modelKey];
        if(~pool.notNil) { ~pool.do(_.free) };
        ~pool = ~poolSize.collect { |k|
            Synth(m[\synthDef], [\out, ~anaBus[k], \gain, 1], ~gAirV) };
        ~voiceModel = modelKey;
    };

    // ======== MEASUREMENT ========
    // Rows are measured in batches of ~poolSize. The per-row isolation from
    // NOTES.md 21.2 is kept and extended: replyId is the GLOBAL row index, so
    // a late reply from an earlier batch falls outside the current batch's
    // range and is rejected rather than being mistaken for this batch's data.
    ~measureRows = { |modelKey, rows, action, bufIdx|
        Routine {
            var m = ~models[modelKey], results = Array.newClear(rows.size), np;
            // which buffer each row renders into. Defaults to row order;
            // ~regrad passes [idx] so gradation 7 re-renders buffer 7.
            var bufs = bufIdx ?? { (0 .. (rows.size - 1)) };
            if(~pool.isNil or: { ~voiceModel != modelKey }) {
                ~makePool.(modelKey);
                0.8.wait;                  // NN startup, once for the whole pool
            };
            np = ~pool.size;
            // Every render is logged. If a buffer changes when nothing was
            // asked to change, this line is where it will show.
            "  [render] % row(s) -> buffer(s) %".format(rows.size, bufs).postln;
            ~pool.do { |v, k| v.set(\out, ~anaBus[k], \gain, 1,
                \atk, ~evt[\atk], \dur, ~evt[\dur], \rel, ~evt[\rel]) };
            rows.clump(np).do { |batch, bi|
                var base = bi * np, need = batch.size, seen = 0;
                var cond = Condition.new, resp, got = Array.newClear(batch.size);
                resp = OSCFunc({ |msg|
                    var id = msg[3].asInteger - base;
                    if((id >= 0) and: { id < need } and: { got[id].isNil }) {
                        got[id] = (peak: msg[4], rms: msg[5],
                                   bad: msg[6].asInteger, onset: msg[7]);
                        seen = seen + 1;
                        if(seen >= need) { cond.test = true; cond.signal };
                    };
                }, '/sdmAnalysis');
                batch.do { |row, j| ~pool[j].set(*~rowArgs.(row)) };
                ~settle.wait;
                s.bind {
                    batch.do { |row, j|
                        Synth(\sdmAnalyse, [\inBus, ~anaBus[j], \replyId, base + j,
                                            \dur, ~anaDur, \onsetThr, ~onsetThr], ~gAirA);
                        // record the very same audio the analyser is measuring
                        Synth(\sdmRecord, [\inBus, ~anaBus[j],
                                           \bufnum, ~buf[bufs[base + j]]], ~gAirA);
                        ~pool[j].set(\t_trig, 1);
                    };
                };
                fork {
                    (~anaDur + 0.8).wait;
                    if(cond.test.not) {
                        cond.test = true; cond.signal;
                        "    (batch % incomplete: % of %)".format(bi, seen, need).postln };
                };
                cond.wait; resp.free;
                need.do { |j| results[base + j] = got[j] ?? { (peak: 0, rms: 0, bad: -1, onset: 999) } };
                0.05.wait;
            };
            action.(results);
        }.play;
    };

    // ======== NORMALISATION ========
    // The loudness target is DERIVED from the family, not fixed in advance:
    //   targetK = peakCeilDb - max(crest)
    // The worst-crest gradation then lands exactly on the peak ceiling, so
    // the peak constraint never binds and every gradation reaches the same
    // K-weighted level. Loudness matching by construction.
    ~normalise = { |meas, modelKey, rows|
        var crests, maxCrest, targetK, gains, usable, ok;
        crests = meas.collect { |m| if(m[\bad] == 0 and: { m[\peak] > ~onsetThr })
            { m[\peak].ampdb - m[\rms].ampdb } { nil } };
        ok = crests.select(_.notNil);
        if(ok.size == 0) {
            "  NO USABLE GRADATIONS: every one was silent or bad.".postln;
            ~gains = nil; ~usable = nil; ~lastMeas = nil;
            ~toPy.('/sdm/familyDone', [0, meas.size, -999]);
        } {
            maxCrest = ok.maxItem;
            targetK  = ~peakCeilDb - maxCrest;
            gains = Array.newClear(meas.size); usable = Array.newClear(meas.size);
            "".postln;
            "  max crest % dB  ->  loudness target % dB K-weighted".format(
                maxCrest.round(0.01), targetK.round(0.01)).postln;
            if(maxCrest > ~maxCrestDb) {
                "  WARNING: max crest above % dB, so the whole family is held".format(~maxCrestDb).postln;
                "  quiet by one spiky gradation.".postln;
            };
            "  idx  peak dBFS  K-rms dB  crest dB  onset ms   gain dB  new peak  status".postln;
            meas.do { |m, i|
                var pk, k, cr, g, silent = m[\peak] <= ~onsetThr;
                if((m[\bad] != 0) or: silent) {
                    usable[i] = false; gains[i] = 0;
                    "  %                                                            %".format(
                        i.asString.padLeft(3),
                        if(m[\bad] == -1) { "NO REPLY" } {
                        if(silent) { "SILENT" } { "BAD VALUES" } }).postln;
                } {
                    pk = m[\peak].ampdb; k = m[\rms].ampdb; cr = pk - k;
                    g  = targetK - k;
                    usable[i] = g <= ~maxBoostDb;
                    gains[i]  = if(usable[i]) { g.dbamp } { 0 };
                    "  %  %  %  %  %  %  %  %".format(
                        i.asString.padLeft(3), pk.round(0.01).asString.padLeft(9),
                        k.round(0.01).asString.padLeft(8), cr.round(0.01).asString.padLeft(8),
                        (m[\onset] * 1000).round(0.1).asString.padLeft(9),
                        if(usable[i]) { g.round(0.01).asString.padLeft(8) } { "       -" },
                        if(usable[i]) { (pk + g).round(0.01).asString.padLeft(8) } { "       -" },
                        if(usable[i]) { "ok" } { "UNUSABLE (needs % dB)".format(g.round(0.1)) }).postln;
                };
            };
            // ~lastMeas is the cache ~regrad needs: without it every
            // per-gradation edit is refused with "nothing measured yet".
            ~gains = gains; ~usable = usable; ~family = rows; ~activeModel = modelKey;
            ~lastMeas = meas;
            "  % of % usable, all matched to % dB K-weighted".format(
                usable.count(_ == true), meas.size, targetK.round(0.01)).postln;
            meas.do { |m, i|
                var pkDb = if(m[\peak] > 0) { m[\peak].ampdb } { -999 };
                var kDb  = if(m[\rms]  > 0) { m[\rms].ampdb }  { -999 };
                ~toPy.('/sdm/grad', [i, pkDb, kDb,
                    if((pkDb > -999) and: { kDb > -999 }) { pkDb - kDb } { 0 },
                    m[\onset] * 1000,
                    if(usable[i]) { gains[i].ampdb } { -999 },
                    usable[i].asInteger]);
            };
            ~toPy.('/sdm/familyDone', [usable.count(_ == true), meas.size, targetK]);
        };
    };

    ~loadFamily = { |modelKey, rows|
        var shifted;
        ~familyRaw = rows;
        shifted = rows.collect { |r| r + ~urgency };     // urgency is a row transform
        "".postln;
        "Measuring % gradations (urgency %). Silent, please wait.".format(
            rows.size, ~urgency).postln;
        ~gains = nil; ~usable = nil;
        ~measureRows.(modelKey, shifted, { |meas| ~normalise.(meas, modelKey, shifted);
            "".postln;
            "READY. Scroll to the top and put the cursor on:   ~go.()".postln;
            "Then press Cmd+Enter. That raises the gain to 1.0 and plays the family.".postln });
    };

    // Re-measure a SINGLE gradation and renormalise the family from the cached
    // measurements of the other nine. Per-gradation SDM tuning changes one row,
    // so measuring all ten would be nine wasted measurements: about 0.8 s here
    // against about 3 s. Every gain is still recomputed, because the loudness
    // target is derived from the family's worst crest factor and that can move
    // when any single gradation changes.
    ~regrad = { |idx, row|
        if(~lastMeas.isNil or: { ~family.isNil }) {
            ~toPy.('/sdm/error', ["no prior family measurement; send /sdm/family first"]);
            "  regrad refused: nothing measured yet".postln;
        } {
            var rows = ~family.copy;
            rows[idx] = row;
            "".postln;
            "Re-measuring gradation % only. Silent.".format(idx).postln;
            ~measureRows.(~activeModel, [row], { |meas|
                var all = ~lastMeas.copy;
                all[idx] = meas[0];
                ~normalise.(all, ~activeModel, rows);
            }, [idx]);
        };
    };

    // Reordering a family is a PERMUTATION, not a new family. No sound
    // changes, so nothing is re-measured: the rows, the gains, the usable
    // flags, the cached measurements AND the recorded buffers all move
    // together. The buffers are the point. If they stayed put, row 3's play
    // button would play whichever earcon used to be third, and the table would
    // be describing one sound while the ear heard another.
    ~reorder = { |perm|
        var n, bad;
        n = if(~family.isNil) { 0 } { ~family.size };
        bad = (n == 0) or: { ~gains.isNil } or: { ~lastMeas.isNil }
              or: { perm.size != n } or: { perm.asSet.size != n }
              or: { perm.any { |q| (q < 0) or: { q >= n } } };
        if(bad) {
            ~toPy.('/sdm/error', ["reorder refused: measure the family first, and "
                ++ "send a permutation of 0.." ++ (n - 1).asString]);
            "  reorder refused (measured: %, perm: %)".format(~gains.notNil, perm).postln;
        } {
            ~family   = perm.collect { |q| ~family[q] };
            ~gains    = perm.collect { |q| ~gains[q] };
            ~usable   = perm.collect { |q| ~usable[q] };
            ~lastMeas = perm.collect { |q| ~lastMeas[q] };
            ~buf      = perm.collect { |q| ~buf[q] };
            if(~familyRaw.notNil and: { ~familyRaw.size == n }) {
                ~familyRaw = perm.collect { |q| ~familyRaw[q] } };
            "  reordered: new row i takes old row perm[i] = %".format(perm).postln;
            ~toPy.('/sdm/reordered', [n]);
        };
    };

    // ======== DIRECT (NON-SDM) CONTROLS ========
    // Envelope and level are ~evt and ~setMaster. Rhythm and urgency are here.
    //
    // URGENCY is applied in the PARAMETER domain, as a uniform offset added to
    // every latent, exactly as in your demo. It is deliberately not a voice
    // argument: shifting the latents changes the sound the model produces, so
    // the family has to be re-measured at each urgency level. Making it a
    // parameter transform means the measurement always describes what you
    // actually hear.
    ~patterns = IdentityDictionary[
        \single  -> [0.0],                  // one hit
        \info    -> [0.0],
        \warning -> [0.0, 0.14],            // double tap
        \error   -> [0.0, 0.11, 0.22],      // triple tap, urgent
        \success -> [0.0, 0.22]             // long-short
    ];
    ~pattern = \single;
    ~urgency = 0.0;

    ~setPattern = { |name| if(~patterns[name].notNil) { ~pattern = name;
        "  pattern -> % %".format(name, ~patterns[name]).postln }
        { "  no such pattern: %".format(name).postln } };

    ~setUrgency = { |u|
        ~urgency = u;
        "  urgency -> %, re-measuring".format(u).postln;
        if(~familyRaw.notNil) { ~loadFamily.(~activeModel, ~familyRaw) };
    };

    // One gradation, played as the current rhythmic pattern. Hits round-robin
    // across the pool so a hit's release is not cut off by the next one.
    // Play a gradation from its recorded buffer, as the current rhythm pattern.
    // Identical every time, and identical to what was measured.
    //
    // TWO THINGS ABOUT LEVEL, both about not letting the limiter act.
    //
    // 1. A new play FREES the previous one. Clicking faster than the buffer is
    //    long used to stack copies: one play peaks at about -14.6 dBFS, so two
    //    overlapping copies reach about -8.6 dBFS, which is 3.4 dB ABOVE the
    //    -12.04 dBFS limiter. The limiter then engaged, and its gain reduction
    //    persists across the next events until it recovers. That is what
    //    "drifts into different territory, then comes back" sounds like.
    //
    // 2. Rhythm patterns overlap ON PURPOSE (a triple tap of 0.55 s sounds is
    //    supposed to overlap), so per-hit gain is divided by the largest number
    //    of hits that can sound at once. Dividing by the count rather than its
    //    square root is conservative: it assumes the copies add coherently,
    //    which they cannot exceed. Patterns therefore sit lower than single
    //    hits; raise the master gain to compensate, which is safe because the
    //    limiter is above it.
    ~audition = { |i|
        if(~armed.not) { "  REFUSED: self-test did not pass, system disarmed.".postln } {
        if(~gains.isNil) { "  REFUSED: no family measured. Run ~again.() or BLOCK 1.".postln } {
        if(~usable[i].not) { "  REFUSED: gradation % is unusable.".format(i).postln } {
            if(~masterAmp <= 0) {
                "  NOTE: master gain is 0, so this will be silent. ~setMaster.(1.0)".postln };
            ~gVoice.freeAll;          // a new play replaces the previous one
            Routine {
                var times = ~patterns[~pattern] ? [0.0], prev = 0.0, simul, g;
                simul = times.collect { |t0|
                    times.count { |t| (t >= t0) and: { t < (t0 + ~anaDur) } }
                }.maxItem.max(1);
                g = ~gains[i] / simul;
                times.do { |t|
                    (t - prev).wait; prev = t;
                    Synth(\sdmPlayBuf, [\out, ~auditionBus, \bufnum, ~buf[i],
                                        \gain, g], ~gVoice);
                };
            }.play;
        }}};
    };

    ~sweep = { |gap = 0.75|
        Routine {
            if(~masterAmp <= 0) { "  master gain is 0, raise it: ~setMaster.(1.0)".postln };
            ~family.size.do { |i|
                if(~usable[i]) {
                    "  % : %".format(i.asString.padLeft(2), ~family[i].collect(_.round(0.01))).postln;
                    ~audition.(i); gap.wait };
            };
            "  sweep done".postln;
        }.play;
    };

    ~status = {
        "".postln;
        "  build %".format(~build).postln;
        "  armed %   model %   master gain %   pool size %".format(
            ~armed, ~activeModel, ~masterAmp, ~poolSize).postln;
        // The count covers the period since the previous ~status.() call, or
        // since arming. Reading it resets it, so the way to test is:
        // ~status.() to clear, then play, then ~status.() again.
        if(~limitHits > 0) {
            "  LIMITER ENGAGED in % reporting periods since the last ~status.().".format(
                ~limitHits).postln;
            "  It should never act in normal operation: something exceeded % dBFS.".format(
                ~ceiling.ampdb.round(0.01)).postln;
            ~limitHits = 0;
        } { "  limiter has not engaged since the last ~status.()".postln };
        "  family % gradations, % usable".format(
            if(~family.notNil) { ~family.size } { 0 },
            if(~usable.notNil) { ~usable.count(_ == true) } { 0 }).postln;
    };

    // ======== OSC INTERFACE (Python <-> sclang) ========
    // The split: Python is the STATE host and owns the SDM controller and the
    // UI; sclang is the AUDIO host and owns SynthDefs, the model, measurement
    // and the safety layer. Neither reaches into the other's job, and the
    // schema is deliberately small.
    //
    // Python -> sclang, port 57120 (sclang's default langPort):
    //   /sdm/family  <nGrad> <nParam> <v...>   row-major; measures all
    //   /sdm/grad1   <idx> <nParam> <v...>     re-measures ONE gradation
    //   /sdm/evt     <atk> <dur> <rel>
    //   /sdm/master  <amp>
    //   /sdm/pattern <t0> <t1> ...             hit onset times in seconds
    //   /sdm/urgency <offset>                  re-measures the family
    //   /sdm/play    <idx>
    //   /sdm/sweep
    //   /sdm/panic
    //   /sdm/ping
    //
    // sclang -> Python, port 57121:
    //   /sdm/grad       <idx> <peakDb> <krmsDb> <crestDb> <onsetMs> <gainDb> <usable>
    //   /sdm/familyDone <nUsable> <nTotal> <targetK>
    //   /sdm/pong       <armed> <masterAmp> <poolSize>
    //   /sdm/error      <message>
    ~pyPort = 57121;
    ~pyAddr = NetAddr("127.0.0.1", ~pyPort);
    ~toPy   = { |path, args| ~pyAddr.sendMsg(*([path] ++ (args ? []))) };
    // The panel cannot work out how close a gradation is to being refused
    // without knowing the boost cap, and duplicating the constant on the Python
    // side is how the two drift apart. So sclang states it.
    ~sendLimits = { ~toPy.('/sdm/limits', [~peakCeilDb, ~maxBoostDb, ~maxCrestDb]) };

    OSCdef(\sdmOscFamily, { |msg|
        var nG = msg[1].asInteger, nP = msg[2].asInteger, flat = msg.copyToEnd(3);
        if(flat.size < (nG * nP)) {
            ~toPy.('/sdm/error', ["family: expected % values, got %".format(nG * nP, flat.size)]);
        } {
            ~loadFamily.(\rave, nG.collect { |i| nP.collect { |j| flat[(i * nP) + j] } });
        };
    }, '/sdm/family');
    OSCdef(\sdmOscGrad1, { |msg|
        var idx = msg[1].asInteger, nP = msg[2].asInteger, row = msg.copyToEnd(3);
        if(row.size < nP) {
            ~toPy.('/sdm/error', ["grad1: expected % values, got %".format(nP, row.size)]);
        } {
            ~regrad.(idx, row.copyRange(0, nP - 1));
        };
    }, '/sdm/grad1');
    OSCdef(\sdmOscEvt,     { |msg| ~evt = (atk: msg[1], dur: msg[2], rel: msg[3]) }, '/sdm/evt');
    OSCdef(\sdmOscMaster,  { |msg| ~setMaster.(msg[1]) }, '/sdm/master');
    OSCdef(\sdmOscPattern, { |msg| ~patterns[\custom] = msg.copyToEnd(1).asArray;
                                    ~pattern = \custom }, '/sdm/pattern');
    OSCdef(\sdmOscUrgency, { |msg| ~setUrgency.(msg[1]) }, '/sdm/urgency');
    OSCdef(\sdmOscPlay,    { |msg| ~audition.(msg[1].asInteger) }, '/sdm/play');
    OSCdef(\sdmOscSweep,   { |msg| ~sweep.() }, '/sdm/sweep');
    OSCdef(\sdmOscPanic,   { |msg| ~panic.() }, '/sdm/panic');
    OSCdef(\sdmOscPing,    { |msg|
        ~toPy.('/sdm/pong', [~armed.asInteger, ~masterAmp, ~poolSize]);
        ~sendLimits.() }, '/sdm/ping');
    // msg[1] is the count, msg[2..] the permutation.
    OSCdef(\sdmOscReorder, { |msg|
        ~reorder.(msg.copyToEnd(2).asArray.collect(_.asInteger)) }, '/sdm/reorder');

    // ======== DIAGNOSTIC ========
    // Play one gradation n times and MEASURE each play at the audition bus.
    // The analyser sits after ~gVoice and before the safety synth, so this
    // works with the master gain at 0: it reads the signal, not the output.
    //
    // If every play measures identically, playback is deterministic and the
    // difference Adam hears is not in the buffer. If they differ, the buffer is
    // being re-recorded, and the [render] lines above will say when.
    ~verifyPlay = { |i, n = 6|
        Routine {
            var results = [];
            if(~gains.isNil or: { ~usable.isNil } or: { ~usable[i].not }) {
                "  cannot verify gradation %: not measured or unusable".format(i).postln;
            } {
                n.do { |k|
                    // cond, resp, got and finished are declared INSIDE the loop.
                    // The first version declared them outside and only assigned
                    // here, so the timeout fork captured the VARIABLE, not the
                    // value: play k's timeout fired during play k+1 and
                    // signalled its condition before the analyser had answered.
                    // Exactly the stale-closure bug fixed in ~measureRows
                    // (NOTES.md 21.2) and then reintroduced here.
                    var cond = Condition.new, resp, got = nil, finished = false;
                    resp = OSCFunc({ |msg|
                        if((msg[3].asInteger == (900 + k)) and: { finished.not }) {
                            got = (peak: msg[4], rms: msg[5], onset: msg[7]);
                            finished = true; cond.test = true; cond.signal;
                        };
                    }, '/sdmAnalysis');
                    cond.test = false;
                    s.bind {
                        Synth(\sdmAnalyse, [\inBus, ~auditionBus, \replyId, 900 + k,
                                            \dur, ~anaDur, \onsetThr, ~onsetThr], ~gAirA);
                        Synth(\sdmPlayBuf, [\out, ~auditionBus, \bufnum, ~buf[i],
                                            \gain, ~gains[i]], ~gVoice);
                    };
                    fork {
                        (~anaDur + 0.8).wait;
                        if(finished.not) {
                            finished = true; cond.test = true; cond.signal;
                        };
                    };
                    cond.wait; resp.free;
                    // Record WHETHER it answered, separately from what it said.
                    // The first version folded "no reply" into peak 0, which
                    // prints as -inf and is indistinguishable from real silence.
                    results = results.add(
                        got ?? { (peak: 0, rms: 0, onset: 999, noreply: true) });
                    0.4.wait;        // let each play finish, so none overlap
                };
                "".postln;
                "VERIFY gradation %, % plays, measured at the audition bus".format(i, n).postln;
                "    k   peak dBFS    K-rms dB   onset ms   identical to play 0".postln;
                results.do { |r, k|
                    var same;
                    if(r[\noreply] == true) {
                        "    %      -            -          -   *** NO REPLY ***".format(
                            k.asString.padLeft(2)).postln;
                    } {
                        same = ((r[\peak] - results[0][\peak]).abs < 1e-7)
                           and: { (r[\rms] - results[0][\rms]).abs < 1e-7 }
                           and: { results[0][\noreply] != true };
                        "    %  %  %  %   %".format(
                            k.asString.padLeft(2),
                            r[\peak].ampdb.round(0.0001).asString.padLeft(10),
                            r[\rms].ampdb.round(0.0001).asString.padLeft(11),
                            (r[\onset] * 1000).round(0.1).asString.padLeft(9),
                            if(same) { "yes" } { "*** NO ***" }).postln;
                    };
                };
                "  If every row says yes, the buffer is not what varies.".postln;
                "  NO REPLY means the analyser did not answer, which is a fault in".postln;
                "  this diagnostic rather than in the audio.".postln;
            };
        }.play;
    };

    // ======== FAMILIES AND SHORTCUTS ========
    ~lerpFamily = { |a, b, n = 10|
        (0 .. (n - 1)).collect { |i| var t = i / (n - 1);
            a.size.collect { |j| ((1 - t) * a[j]) + (t * b[j]) } }
    };
    // Named from your demo presets. network -> user spans about 23 dB
    // before normalisation, so it is the useful stress case.
    ~famNetworkToUser   = ~lerpFamily.([-1, -1.5, -0.5, 0.5], [2, 1, 1, -1]);
    ~famFileOpsToSystem = ~lerpFamily.([1.5, 0, -1, 0.5], [0, -1.5, 1.5, 0]);
    ~defaultFamily      = ~famNetworkToUser;

    ~useFamily = { |rows| ~loadFamily.(\rave, rows) };
    ~again     = { if(~family.notNil) { ~loadFamily.(\rave, ~family) }
                                      { "  no family loaded yet".postln } };
    ~go        = { ~setMaster.(1.0); ~sweep.() };

    // ========================================================
    // SELF-TEST. Runs now. The system stays disarmed unless it passes.
    // ========================================================
    Routine {
        var check, cond = Condition.new, sm;
        allPass = true;
        "".postln;
        "================ SDM EARCONS SELF-TEST ================".postln;
        "  build: %".format(~build).postln;

        // ---- T1: safety chain. Two-sided checks: a test that passes on
        // silence is not a safety test.
        check = { |label, lo, hi, wantBad|
            var db; ~monPeak = 0; ~monBad = 0; 0.5.wait; db = ~monPeak.ampdb;
            if((db >= lo) and: { db <= hi } and: { ~monBad >= wantBad }) {
                "  T1 %  % dBFS  PASS".format(label.padRight(22),
                    db.round(0.01).asString.padLeft(8)).postln;
            } {
                allPass = false;
                "  T1 %  % dBFS  *** FAIL *** wanted % to %, bad>=%".format(
                    label.padRight(22), db.round(0.01).asString.padLeft(8), lo, hi, wantBad).postln;
            };
        };
        [[0, "normal -13.98", -15.0, -13.0, 0],
         [2, "loud, want ceiling", ~ceiling.ampdb - 0.5, ~ceiling.ampdb + 0.5, 0],
         [1, "NaN, want bad>=1", -inf, -80.0, 1]].do { |spec|
            var v = Synth(\sdmVoiceTest, [\out, ~auditionBus, \p0, spec[0], \gain, 1], ~gVoice);
            v.set(\t_trig, 1);
            check.(spec[1], spec[2], spec[3], spec[4]);
            v.free;
        };
        ~outPeak = 0;
        Synth(\sdmVoiceTest, [\out, 0, \p0, 2, \gain, 1], ~gVoice).set(\t_trig, 1);
        0.5.wait;
        if(~outPeak <= 1e-4) { "  T1 stray-to-bus-0        PASS".postln }
        { allPass = false; "  T1 stray-to-bus-0        *** FAIL ***".postln };
        ~gVoice.freeAll;

        // ---- T2: measurement accuracy against an analytic law.
        // The sine model's peak is exactly 20*log10amp dBFS. If the
        // measurement path cannot recover that, nothing downstream can be
        // trusted. This is also the model-agnosticism check: no part of
        // the path knows anything about the model.
        t2 = nil; ~t2detail = nil;
        thisThread.randSeed = 12345;
        block { |brk|
            var rows = 8.collect { [rrand(4.0, 8.0), rrand(-4.0, 0.0)] };
            cond.test = false;
            ~measureRows.(\sine, rows, { |meas|
                var err = meas.collect { |m, i|
                    if(m[\peak] > 0) { m[\peak].ampdb - (20 * rows[i][1]) } { inf } };
                ~t2detail = [rows, meas, err];
                t2 = err.collect(_.abs).maxItem; cond.test = true; cond.signal });
            cond.wait;
        };
        if(t2.notNil and: { t2 < 0.5 }) {
            "  T2 sine analytic law     max err % dB   PASS".format(t2.round(0.001)).postln
        } {
            allPass = false;
            "  T2 sine analytic law     max err %   *** FAIL ***".format(t2).postln;
            // A bare "inf" tells us nothing about which rows failed or why.
            if(~t2detail.notNil) {
                "       row   analytic    measured        err  bad".postln;
                ~t2detail[0].do { |r, i|
                    var mm = ~t2detail[1][i];
                    "       %  %  %  %  %".format(i.asString.padLeft(3),
                        (20 * r[1]).round(0.01).asString.padLeft(9),
                        if(mm[\peak] > 0) { mm[\peak].ampdb.round(0.01).asString.padLeft(10) }
                                          { "    SILENT" },
                        ~t2detail[2][i].round(0.01).asString.padLeft(9),
                        mm[\bad]).postln;
                };
            };
        };

        // ---- T3: load RAVE and build its voice.
        if(File.exists(~modelPath).not) {
            allPass = false; "  T3 RAVE model NOT FOUND at %  *** FAIL ***".format(~modelPath).postln;
        } {
            NN.load(\rave, ~modelPath);
            3.0.wait;                      // NN.load has no completion callback
            SynthDef(\sdmVoiceRave, { |out = 16, gain = 0.2, t_trig = 0,
                                      atk = 0.005, dur = 0.25, rel = 0.30,
                                      p0 = 0, p1 = 0, p2 = 0, p3 = 0|
                var sig = NN(\rave, \decode).ar([p0, p1, p2, p3]);
                var env = EnvGen.kr(Env([0, 1, 1, 0], [atk, dur, rel], \sin), gate: t_trig);
                Out.ar(out, sig * env * gain);
            }).add;
            s.sync;
            ~registerModel.(\rave, \sdmVoiceRave, ["z1", "z2", "z3", "z4"],
                [[-3, 3], [-3, 3], [-3, 3], [-3, 3]], true);
            "  T3 RAVE loaded and registered   PASS".postln;

            // ---- T4: one RAVE voice actually makes sound, and its onset
            // is near zero, which is what proves the persistent-voice
            // architecture is working.
            t4 = nil; cond.test = false;
            ~measureRows.(\rave, [[1.5, 0, -1, 0.5]], { |meas| t4 = meas[0];
                cond.test = true; cond.signal });
            cond.wait;
            if(t4.notNil and: { t4[\peak] > ~onsetThr }) {
                "  T4 RAVE smoke test       peak % dBFS  onset % ms  PASS".format(
                    t4[\peak].ampdb.round(0.01), (t4[\onset] * 1000).round(0.1)).postln;
                if(t4[\onset] > 0.05) {
                    "     NOTE: onset % ms is higher than expected for a persistent".format(
                        (t4[\onset]*1000).round(0.1)).postln;
                    "     voice. Expect near 0.".postln;
                };
            } { allPass = false; "  T4 RAVE smoke test       *** FAIL: silent ***".postln };

            // ---- T5: how many persistent RAVE voices this machine can run.
            // NOTE: avgCPU turns out to be a poor guard. nn.ar runs its
            // inference on a background thread, so four voices report about
            // 0.2 percent DSP load while ten of them killed the server. The
            // real limit is memory and threads (a 68 MB model per instance),
            // which avgCPU cannot see. The hard cap of 4 below is doing the
            // actual protecting; the CPU check is only a secondary guard.
            block { |brk|
                var vs = [];
                ~poolSize = 1;
                [2, 3, 4].do { |n|
                    if(s.serverRunning and: { s.avgCPU < 40 }) {
                        while({ vs.size < (n - 1) }, {
                            vs = vs.add(Synth(\sdmVoiceRave,
                                [\out, ~anaBus[vs.size + 1], \gain, 0.0], ~gAirV));
                            0.35.wait });
                        0.8.wait;
                        if(s.serverRunning and: { s.avgCPU < 40 }) { ~poolSize = n };
                    };
                };
                vs.do(_.free);
                "  T5 pool size             % voices at % percent CPU".format(
                    ~poolSize, s.avgCPU.round(0.1)).postln;
            };

            // ---- T6: the OSC interface, exercised without Python.
            // ~pyAddr is pointed at sclang's own port and a listener is
            // registered, so a ping must come back as a pong: that tests the
            // outbound path. Then an /sdm/evt message must actually change the
            // envelope state: that tests the inbound path and proves the
            // handler does something, rather than merely not erroring.
            block { |brk|
                var savedAddr = ~pyAddr, savedEvt = ~evt, pong = nil, resp, ok1, ok2, me;
                me = NetAddr("127.0.0.1", NetAddr.langPort);
                ~pyAddr = me;
                resp = OSCFunc({ |m| pong = m.copyToEnd(1) }, '/sdm/pong');
                me.sendMsg('/sdm/ping');
                0.4.wait;
                ok1 = pong.notNil;
                me.sendMsg('/sdm/evt', 0.011, 0.222, 0.333);
                0.3.wait;
                ok2 = ((~evt[\atk] - 0.011).abs < 1e-6) and: { (~evt[\dur] - 0.222).abs < 1e-6 };
                resp.free; ~pyAddr = savedAddr; ~evt = savedEvt;
                if(ok1 and: { ok2 }) {
                    "  T6 OSC loopback          pong %  and /sdm/evt applied  PASS".format(pong).postln
                } {
                    allPass = false;
                    "  T6 OSC loopback          ping->pong %, evt applied %  *** FAIL ***".format(
                        ok1, ok2).postln;
                };
            };
        };

        // ---- arm or refuse ----
        "".postln;
        if(allPass) {
            ~armed = true; ~ready = true;
            ~sendLimits.();
            // T1 drives a +16.90 dBFS signal into the limiter ON PURPOSE, to
            // prove the ceiling holds. Counting that as an anomaly makes the
            // report useless, so the count starts here, after the tests.
            ~limitHits = 0;
            "  SELF-TEST PASSED. System ARMED, master gain 0.".postln;
            "=======================================================".postln;
            // Measuring is silent, so do it now rather than making the user
            // click again. The only step left manual is the one that makes
            // sound, which is the one that must stay manual.
            ~loadFamily.(\rave, ~defaultFamily);
        } {
            ~armed = false;
            "  *** SELF-TEST FAILED. System DISARMED and will refuse to".postln;
            "  *** make sound. Do not continue; send the log.".postln;
        };
        "=======================================================".postln;
    }.play;
};
)


// ############################################################
// BLOCK 2  Optional: measure a family other than the default.
// BLOCK 1 already measures the default family for you.
// Silent while measuring (about 10 s), then tells you what to click.
// The default family interpolates two of your demo presets,
// network -> user, which differ by about 23 dB before normalisation.
// ############################################################
(
var a = [-1, -1.5, -0.5, 0.5], b = [2, 1, 1, -1];
~loadFamily.(\rave, (0 .. 9).collect { |i|
    var t = i / 9; 4.collect { |j| ((1 - t) * a[j]) + (t * b[j]) } });
)

// (audible playback is ~go.() at the top of the file)


// ############################################################
// BLOCK 3  Teardown.
// ############################################################
(
~panic.();
[~gVoice, ~gAirV, ~gAirA, ~gSafety, ~gMon].do(_.free);
~auditionBus.free; ~anaBus.do(_.free); ~buf.do(_.free);
OSCdef(\sdmSafetyMon).free; OSCdef(\sdmOutMon).free;
[\sdmOscFamily, \sdmOscGrad1, \sdmOscEvt, \sdmOscMaster, \sdmOscPattern, \sdmOscUrgency,
 \sdmOscPlay, \sdmOscSweep, \sdmOscPanic, \sdmOscPing, \sdmOscReorder].do { |k|
    if(OSCdef(k).notNil) { OSCdef(k).free } };
~armed = false;
"torn down".postln;
)
