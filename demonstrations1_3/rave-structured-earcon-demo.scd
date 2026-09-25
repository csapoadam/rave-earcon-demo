// ============================================================
// RAVE Structured Earcon Demo
// For: "A Taxonomy of Auditory Display Design in the Generative AI Era"
//
// Two-layer earcon architecture:
//   Layer 1 (RAVE): generates timbral material from latent coords
//   Layer 2 (Pbind/Pseq): imposes structural encoding via rhythm,
//           repetition, and pitch — this is what makes them
//           earcons (structural mapping) rather than auditory
//           icons (ecological mapping).
//
// The individual RAVE sounds may be ecological (percussive),
// but the ENCODING SCHEME — rhythm pattern = event type,
// timbre class = category, pitch = urgency — is abstract
// and must be learned, which is the defining property of earcons.
//
// Requires: nn.ar extension + a RAVE Percussion model (.ts)
// ============================================================

// --------------------------------------------------
// 0. SETUP
// --------------------------------------------------
(
~modelPath = PathName(thisProcess.nowExecutingPath).pathOnly +/+ "../RAVE/Percussion.ts";
~numLatent = 4;

s.waitForBoot {
	NN.load(\rave, ~modelPath);

	// SynthDef with individual latent args for Pbind compatibility.
	// Uses susDur (not "sustain") to avoid conflict with SC Event system.
	// No gate arg — envelope runs to completion via doneAction: 2.
	SynthDef(\raveStructured, {
		arg out = 0, amp = 0.5, susDur = 0.15,
		    z1 = 0, z2 = 0, z3 = 0, z4 = 0, pitchShift = 0;
		var latents, sig, env;
		// pitchShift scales all latent coords uniformly,
		// shifting the timbral region (brighter/darker)
		// without the latency issues of PitchShift UGen.
		latents = [z1, z2, z3, z4] + pitchShift;
		sig = NN(\rave, \decode).ar(latents);
		env = EnvGen.kr(
			Env.linen(0.01, susDur, 0.15, 1, \sin),
			doneAction: 2
		);
		Out.ar(out, sig * env * amp);
	}).add;

	"Setup complete. Run Section 1 next.".postln;
};
)


// --------------------------------------------------
// 1. DEFINE TIMBRAL CATEGORIES AND RHYTHMIC PATTERNS
//
//    Latent coordinates define TIMBRAL CLASSES
//    (= conceptual categories in the interface).
//    Rhythmic patterns define EVENT TYPES within
//    each class. Pitch encodes urgency.
//
//    This three-level encoding (timbre × rhythm × pitch)
//    is structural mapping in CogInfoCom terms.
// --------------------------------------------------
(
// --- Layer 1: Timbral categories (RAVE latent coords) ---
// Each coordinate produces a perceptually distinct timbre.
// These are the "instrument families" of our earcon system.
~timbres = IdentityDictionary[
	\fileOps  -> [ 1.5,  0.0, -1.0,  0.5],
	\network  -> [-1.0,  -1.5,  -0.5, 0.5],
	\system   -> [ 0.0, -1.5,  1.5,  0.0],
	\user     -> [ 2.0,  1.0,  1.0, -1.0],
];

// --- Layer 2: Rhythmic patterns (Pseq durations) ---
// Number of hits + timing create distinct rhythmic gestalts.
// The meaning is LEARNED, not ecological — this is what
// makes the encoding abstract (= earcon, not auditory icon).
//
// Each pattern: (durs: inter-onset intervals, susDur: per-hit envelope)
// info:    1 hit   — single tap
// warning: 2 hits  — double tap (attention-getting)
// error:   3 hits  — triple tap (urgent)
// success: 2 hits  — long-short (completion gesture)
~patterns = IdentityDictionary[
	\info    -> (durs: [0.5],              susDur: [0.12]),
	\warning -> (durs: [0.14, 0.36],       susDur: [0.10, 0.10]),
	\error   -> (durs: [0.11, 0.11, 0.28], susDur: [0.08, 0.08, 0.08]),
	\success -> (durs: [0.22, 0.28],       susDur: [0.18, 0.08]),
];

// --- Layer 3: Timbral shift (urgency) ---
// Adding an offset to all latent dims shifts the sound
// into a brighter or darker region of the latent space.
~urgencyShifts = IdentityDictionary[
	\low    -> -0.3,
	\medium ->  0.0,
	\high   ->  0.5,
];

// --- Helper: play one structured earcon ---
// Returns total duration so callers can sequence.
~playEarcon = { |category, meaning, urgency=\medium|
	var t = ~timbres[category];
	var p = ~patterns[meaning];
	var s = ~urgencyShifts[urgency];
	Pbind(
		\instrument, \raveStructured,
		\z1, t[0], \z2, t[1], \z3, t[2], \z4, t[3],
		\dur, Pseq(p[\durs]),
		\susDur, Pseq(p[\susDur]),
		\pitchShift, s,
		\amp, 0.6
	).play;
	p[\durs].sum  // return total duration
};

"Categories, patterns, and helper defined. Run demos below.".postln;
)


// --------------------------------------------------
// 2. DEMO: SAME TIMBRE, FOUR RHYTHMIC MEANINGS
//    All in the "fileOps" timbral category.
//    Listen for how the rhythmic pattern alone
//    distinguishes info / warning / error / success.
// --------------------------------------------------
(
Routine {
	"--- Same timbre (fileOps), four meanings via rhythm ---".postln;
	[\info, \warning, \error, \success].do { |meaning|
		var dur;
		"  Playing: fileOps > %".format(meaning).postln;
		dur = ~playEarcon.(\fileOps, meaning);
		dur.wait;
		0.5.wait;
	};
	"Done. Same timbre, four different rhythmic patterns.".postln;
}.play;
)


// --------------------------------------------------
// 3. DEMO: SAME RHYTHM, FOUR TIMBRAL CATEGORIES
//    All using the "warning" double-tap pattern.
//    Listen for how the timbre alone distinguishes
//    the conceptual category.
// --------------------------------------------------
(
Routine {
	"--- Same rhythm (warning), four categories via timbre ---".postln;
	[\fileOps, \network, \system, \user].do { |cat|
		var dur;
		"  Playing: % > warning".format(cat).postln;
		dur = ~playEarcon.(cat, \warning);
		dur.wait;
		0.5.wait;
	};
	"Done. Same rhythm, four different timbres.".postln;
}.play;
)


// --------------------------------------------------
// 4. DEMO: HIERARCHICAL ENCODING WITH PITCH
//    Same category + same meaning, but three urgency
//    levels via pitch transposition.
//    This gives a three-level hierarchy:
//      category (timbre) > meaning (rhythm) > urgency (pitch)
// --------------------------------------------------
(
Routine {
	"--- Hierarchical: fileOps > warning > 3 urgency levels ---".postln;
	[\low, \medium, \high].do { |urgency|
		var dur;
		"  Urgency: % (latent shift: %)".format(
			urgency, ~urgencyShifts[urgency]
		).postln;
		dur = ~playEarcon.(\fileOps, \warning, urgency);
		dur.wait;
		0.6.wait;
	};
	"Done. Same category + meaning, three urgency levels via pitch.".postln;
}.play;
)


// --------------------------------------------------
// 5. DEMO: FULL SIMULATED EVENT STREAM
//    A realistic sequence of interface events, each
//    encoded as [category, meaning, urgency].
//    The full earcon encoding scheme in action.
// --------------------------------------------------
(
Routine {
	var events = [
		[\fileOps, \info,    \low],
		[\network, \warning, \medium],
		[\fileOps, \success, \low],
		[\system,  \error,   \high],
		[\user,    \info,    \low],
		[\network, \success, \medium],
		[\system,  \warning, \high],
		[\fileOps, \info,    \low],
	];

	"--- Simulated interface event stream ---".postln;
	events.do { |ev|
		var dur;
		"  [%] % (urgency: %)".format(ev[0], ev[1], ev[2]).postln;
		dur = ~playEarcon.(ev[0], ev[1], ev[2]);
		dur.wait;
		0.4.wait;
	};
	"Done.".postln;
	"Each event encoded: category(timbre) x meaning(rhythm) x urgency(pitch).".postln;
}.play;
)


// --------------------------------------------------
// 6. INTERACTIVE EXPLORER WITH MEMORY SLOTS
//    Sliders for latent dims + dropdowns for pattern.
//    10 memory slots with disk persistence.
//    Click a slot to recall; toggle "Store" first
//    to save current settings to a slot.
// --------------------------------------------------
(
var win, latentVals, currentMeaning, currentUrgency;
var sliders, meaningMenu, urgencyMenu;
var presets, storeMode, storeModeBtn;
var savePresets, loadPresets;
var meaningSyms, urgencySyms;

meaningSyms = [\info, \warning, \error, \success];
urgencySyms = [\low, \medium, \high];

latentVals = 0 ! ~numLatent;
currentMeaning = \info;
currentUrgency = \medium;
storeMode = false;
sliders = [];

// --- Preset persistence (saved next to the .scd file) ---
~presetsPath = PathName(thisProcess.nowExecutingPath).pathOnly +/+ "earcon-presets.txt";

loadPresets = {
	if(File.exists(~presetsPath)) {
		var f, str;
		f = File(~presetsPath, "r");
		str = f.readAllString;
		f.close;
		presets = str.interpret;
		"Loaded presets from %".format(~presetsPath).postln;
	} {
		presets = nil ! 10;
		"No presets file found. Starting with empty slots.".postln;
	};
};

savePresets = {
	var f;
	f = File(~presetsPath, "w");
	f.write(presets.asCompileString);
	f.close;
	"Presets saved to %".format(~presetsPath).postln;
};

loadPresets.();

win = Window("Structured Earcon Explorer", Rect(200, 200, 780, 400));
win.layout_(
	VLayout(
		// --- Timbral layer: latent space sliders ---
		StaticText().string_("TIMBRAL LAYER: Latent Space Coordinates")
		    .font_(Font.default.bold_(true)),
		HLayout(
			*~numLatent.collect { |i|
				var sl;
				sl = Slider().value_(0.5).action_{ |s|
					latentVals[i] = s.value * 6 - 3;
				};
				sliders = sliders.add(sl);
				VLayout(
					StaticText().string_("z" ++ (i+1)).align_(\center),
					sl
				)
			}
		),

		// --- Structural layer: meaning + urgency ---
		StaticText().string_("STRUCTURAL LAYER: Rhythm + Urgency")
		    .font_(Font.default.bold_(true)),
		HLayout(
			StaticText().string_("Meaning:"),
			meaningMenu = PopUpMenu()
			    .items_(["info", "warning", "error", "success"])
			    .action_{ |menu|
				    currentMeaning = meaningSyms[menu.value];
			    },
			StaticText().string_("  Urgency:"),
			urgencyMenu = PopUpMenu()
			    .items_(["low", "medium", "high"])
			    .value_(1)
			    .action_{ |menu|
				    currentUrgency = urgencySyms[menu.value];
			    },
		),

		// --- Play + Copy ---
		HLayout(
			Button().states_([["Play Structured Earcon"]])
			.action_{
				var t, p, sh;
				t = latentVals;
				p = ~patterns[currentMeaning];
				sh = ~urgencyShifts[currentUrgency];
				"Playing: % | % | % (shift %)".format(
					latentVals.collect(_.round(0.01)),
					currentMeaning, currentUrgency, sh
				).postln;
				Pbind(
					\instrument, \raveStructured,
					\z1, t[0], \z2, t[1], \z3, t[2], \z4, t[3],
					\dur, Pseq(p[\durs]),
					\susDur, Pseq(p[\susDur]),
					\pitchShift, sh,
					\amp, 0.6
				).play;
			},
			Button().states_([["Copy coordinates"]])
			.action_{
				"[ %, %, %, % ]".format(
					*latentVals.collect { |v| v.round(0.01) }
				).postln;
			}
		),

		// --- Memory slots ---
		StaticText().string_("MEMORY SLOTS  (click = recall · toggle Store first to save)")
		    .font_(Font.default.bold_(true)),
		HLayout(
			*(
				// Store mode toggle button
				[
					storeModeBtn = Button()
					.states_([
						["Store: OFF", Color.black, Color.white],
						["Store: ON",  Color.white, Color.red]
					])
					.action_{ |btn|
						storeMode = btn.value == 1;
					}
				]
				++
				// 10 memory slot buttons
				10.collect { |i|
					var btn;
					btn = Button()
					.states_([
						[(i+1).asString, Color.black, Color.white]
					])
					.action_{
						if(storeMode) {
							// STORE current settings
							presets[i] = latentVals.copy ++ [
								meaningMenu.value,
								urgencyMenu.value
							];
							savePresets.();
							// Mark slot as occupied (blue)
							btn.states_([
								[(i+1).asString, Color.white, Color(0.2, 0.4, 0.8)]
							]);
							// Turn off store mode
							storeModeBtn.value_(0);
							storeMode = false;
							"Stored slot %: %".format(i+1, presets[i].collect(_.round(0.01))).postln;
						} {
							// RECALL saved settings
							if(presets[i].notNil) {
								4.do { |j|
									latentVals[j] = presets[i][j];
									sliders[j].value_((presets[i][j] + 3) / 6);
								};
								meaningMenu.value_(presets[i][4].asInteger);
								currentMeaning = meaningSyms[presets[i][4].asInteger];
								urgencyMenu.value_(presets[i][5].asInteger);
								currentUrgency = urgencySyms[presets[i][5].asInteger];
								"Recalled slot %: %".format(i+1, presets[i].collect(_.round(0.01))).postln;
							} {
								"Slot % is empty.".format(i+1).postln;
							};
						};
					};
					// Mark occupied slots blue on startup
					if(presets[i].notNil) {
						btn.states_([
							[(i+1).asString, Color.white, Color(0.2, 0.4, 0.8)]
						]);
					};
					btn
				}
			)
		)
	)
);
win.front;
)


// --------------------------------------------------
// NOTES FOR THE PAPER
// --------------------------------------------------
// This demo addresses the earcon vs. auditory icon concern:
//
// The RAVE model produces percussive sounds that are inherently
// ECOLOGICAL (they sound like real-world impacts). On their own,
// these would be auditory icons, not earcons.
//
// However, the TWO-LAYER ARCHITECTURE makes them earcons:
//   - The timbral material (Layer 1) comes from RAVE's latent space
//   - The structural encoding (Layer 2) comes from Pbind/Pseq patterns
//
// What makes these earcons (per Blattner et al. 1989):
//   1. ABSTRACT encoding: the mapping from rhythm to meaning
//      (1 hit = info, 2 hits = warning, 3 hits = error) is
//      arbitrary and must be LEARNED — not ecologically intuitive.
//   2. HIERARCHICAL structure: category (timbre) > type (rhythm)
//      > urgency (pitch) mirrors Blattner's hierarchical earcons.
//   3. SYSTEMATIC variation: each dimension varies independently,
//      producing a combinatorial space of distinct earcons.
//
// From the CogInfoCom channels perspective, this is STRUCTURAL
// MAPPING: the dimensionality of the concept space (category,
// event type, urgency) is mapped onto independent dimensions
// of the auditory message (timbre, rhythm, pitch).
//
// The generative model's contribution is providing the timbral
// substrate — a palette of perceptually distinct source sounds
// that can be structurally organized without manual synthesis.
