# Cost & Capability Analysis — 5-Minute Video (Corrected)

Research date: **2026-11-25**. All rates from official pricing pages, cited inline.
Supersedes the cost section and the Video Indexer claim in
[`AZURE-VS-GEMINI-ANALYSIS.md`](AZURE-VS-GEMINI-ANALYSIS.md).

---

## Three corrections I owe you

### Correction 1 — Tensai runs on Vertex, and I framed the Gemini side as AI Studio

You are right. The demo uses the AI Studio free tier; **Tensai production runs on GCP
Vertex AI with a service-account JSON key**, not an API key. That matters for cost, because
Vertex rates differ from AI Studio rates and both have a scheduled increase:

| | Now (through 2026-12-31) | From 2027-01-01 |
|---|---|---|
| Gemini 3.8 Flash on **Vertex**, Global | **$0.75 / $3.75** per 1M in/out | **$1.50 / $7.50** |
| Gemini 3.8 Flash on **AI Studio** | $0.75 / $3.75 | $1.50 / $7.50 |

Source: [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) — "Gemini 3.8
Flash … introductory pricing of $0.75 / $3.75 per 1M tokens input / output through December 31,
2026. Starting January 1, 2027, standard pricing of $1.5 / $7.5 per 1M tokens will apply."

**Both paths double on 1 January 2027.** Budget for $1.50, not $0.75.

Also relevant: Vertex bills **all input modalities at the same rate** — `Input (text, image,
video, audio)` is one price. Audio is not a separate, cheaper meter on Vertex (it is on AI
Studio). Our videos contain audio, so this is the number that applies.

### Correction 2 — You meant the GPT-5 *series*, not GPT-5.0

Fair. But the answer does not change, and now I have it confirmed across the whole family:

| Model | Text | Image | **Video** | Source |
|---|---|---|---|---|
| `gpt-5` | ✅ | ✅ | **❌ Not supported** | OpenAI model page |
| `gpt-5.5` (Global) | ✅ | ✅ | **❌ Not supported** | Azure pricing page (modalities) |
| `gpt-5.6-sol` / `-terra` / `-luna` | ✅ | ✅ | **❌ Not supported** | OpenAI model pages |
| `gpt-6.2-astra` / `-sol` | ✅ | ✅ | **❌ Not supported** | OpenAI model page: "Audio and video are not supported" |

The `gpt-6.2-astra` model page states it plainly:

> "It accepts text and images and responds with text. **Modalities — Audio: Not supported.
> Video: Not supported.**"

So "Sol" and "Terra" are real, current, and good models — they just **cannot take a video
file**. Any GPT-based video pipeline, on any of these, requires you to extract frames yourself.

**What this means practically:** the teammate's idea is not wrong in spirit — a frontier GPT
reading frames *is* a viable architecture. It is just not "attach the video to the model."
It is "build a frame extractor, then send images."

### Correction 3 — Video Indexer *can* understand video. I overstated this.

This is the correction that matters most, and you caught a real error.

I wrote that VI's LLM features "never see pixels." That is too strong. Video Indexer actually
does real visual analysis:

- **OCR on on-screen text**, across the whole timeline, with temporal error-correction
  (it clusters detections across frames so one bad frame reading "Microsolt" is corrected
  by neighbouring frames reading "Microsoft")
- Scene and shot segmentation with keyframes
- Labels identification (visual objects and actions)
- Face detection, topic inference, sentiment from speech *and visual text*
- Generative summaries over the indexed result

Source: [Video Indexer overview](https://learn.microsoft.com/en-us/azure/azure-video-indexer/video-indexer-overview).

**So the accurate statement is this:** VI *does* understand video, through a **fixed catalogue
of models**. It is excellent at "what's in this file" — transcript, OCR, scenes, topics,
faces. It is not built to answer "was the Save button disabled when the presenter clicked it?"

The practical difference for our app is narrower than I implied, and it cuts a specific way:

| VI can give us | VI cannot give us |
|---|---|
| Exact on-screen text (OCR) with timestamps | Whether a control was *enabled* or *disabled* |
| Spoken narration + speaker labels | Focus state / hover state |
| Scene and keyframe boundaries | Which element received a click |
| Topics, keywords, entities | Validation-message wording that appeared for 200 ms |
| | The *order* of two clicks inside one second |

VI's OCR is genuinely useful for our "read the UI" requirement — arguably better than a
downscaled video frame, because OCR runs at full resolution. But OCR gives you **text**, not
**state**. A disabled button and an enabled button have identical labels.

So: **VI is a real option, and better than I said.** It is also not a drop-in replacement for
what native video understanding gives us. It is a different, complementary capability.

---

## The cost calculation — 5-minute video

**Assumptions** (stated so you can check the arithmetic):

- 5 minutes = 300 seconds
- Gemini video tokenisation, `media_resolution = HIGH`: **280 tokens per frame**
  (Microsoft-documented equivalent is 258; I use Google's 280 — the conservative, higher number)
- 1 fps = 300 frames; 2 fps = 600; 3 fps = 900
- Output: a 10–25 case `TestSuite` ≈ **4,000 output tokens** (2,000 visible + 2,000 thinking,
  billed at the output rate)
- Text prompt + `SYSTEM_INSTRUCTION` ≈ **1,500 input tokens**
- Excludes taxes, EA/MACC discounts, retries, storage

### Gemini on Vertex AI — 5-minute video

| Model | Rate (in/out per 1M) | fps | Video tokens | Input cost | Output cost | **Total** |
|---|---|---|---|---|---|---|
| **3.8 Flash** (now) | $0.75 / $3.75 | 1 | 84,000 | $0.0630 | $0.0150 | **$0.0780** |
| | | 2 | 168,000 | $0.1260 | $0.0150 | **$0.1410** |
| | | 3 | 252,000 | $0.1890 | $0.0150 | **$0.2040** |
| **3.8 Flash** (2027) | $1.50 / $7.50 | 1 | 84,000 | $0.1260 | $0.0300 | **$0.1560** |
| | | 3 | 252,000 | $0.3780 | $0.0300 | **$0.4080** |
| **3.5 Flash-Lite** | $0.30 / $2.50 | 1 | 84,000 | $0.0252 | $0.0100 | **$0.0352** |
| **4 Hydrogen** | $1.50 / $7.50 | 1 | 84,000 | $0.1260 | $0.0300 | **$0.1560** |
| **4 Carbon** (now) | $2.00 / $10.00 | 1 | 84,000 | $0.1680 | $0.0400 | **$0.2080** |

Input-cost arithmetic for the first row: `84,000 / 1,000,000 × $0.75 = $0.063`.
Output: `4,000 / 1,000,000 × $3.75 = $0.015`.

**Cheapest credible Gemini path: `3.5 Flash-Lite` at ~$0.035 per 5-minute video.**
**Recommended production path: `3.8 Flash` at 1 fps, ~$0.078 now / ~$0.156 from 2027.**

### Azure Content Understanding — 5-minute video

Published rates ([Azure CU pricing](https://azure.microsoft.com/en-us/pricing/details/content-understanding/)):

- Video content extraction: **$1.00 per hour**
- Standard contextualization: **$1.00 per 1M tokens**, at **1,000,000 tokens per hour of video**
- Field extraction: *"incurs cost from both the Azure OpenAI model and the embedding model"* —
  billed at the model's token rate on your Foundry deployment
- Field extraction token usage: Microsoft's worked example uses **7,500 input / 900 output
  tokens per minute** of video

Per 5 minutes (300 s):

| Component | Calculation | Cost |
|---|---|---|
| Content extraction | 5 min ÷ 60 × $1.00 | **$0.0833** |
| Contextualization | 5 min ÷ 60 × 1,000,000 tokens = 166,667 × $1.00/M | **$0.1667** |
| Field-extraction input | 5 × 7,500 = 37,500 tokens | *depends on model* |
| Field-extraction output | 5 × 900 = 4,500 tokens | *depends on model* |

**Sub-total before the LLM: $0.2500**

Now add the model. This is where the GPT series choice dominates:

| Foundry model | Rate (in/out per 1M) | LLM cost (37.5k in, 4.5k out) | **Azure total** |
|---|---|---|---|
| **GPT-5.6-luna** Global | $0.20 / $1.20 | $0.0075 + $0.0054 | **$0.263** |
| **GPT-6 Luna** Global | $0.10 / $0.50 | $0.0038 + $0.0023 | **$0.256** |
| **GPT-6.1 Terra** Global | $1.00 / $5.00 | $0.0375 + $0.0225 | **$0.310** |
| **GPT-6.2 Sol** Global | $2.00 / $10.00 | $0.0750 + $0.0450 | **$0.370** |
| **GPT-5.5** Global | $5.00 / $30.00 | $0.1875 + $0.1350 | **$0.573** |
| **GPT-6.2 Astra** Global | $8.00 / $40.00 | $0.3000 + $0.1800 | **$0.730** |

Rates: [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/).

### Video Indexer — official per-minute rates (found after the first draft)

VI is billed **per input minute**, with audio and video charged **separately**
([VI pricing](https://azure.microsoft.com/en-us/pricing/details/video-indexer/)):

| Preset | Video / min | Audio / min | **5-min total (both)** |
|---|---|---|---|
| Basic | $0.045 | $0.0126 | **$0.288** |
| Standard | $0.090 | $0.024 | **$0.570** |
| Advanced | $0.150 | $0.040 | **$0.950** |

Add the LLM on top (VI returns an index, not test cases). With GPT-6.2 Sol at $0.120:
**VI Basic + Sol = $0.408 per 5-min video.**

**VI is therefore the most expensive Azure route** — more than Content Understanding, and 5.2×
Gemini. Its pricing assumes indexing a library once and searching it forever; we make a fresh
suite per video and never search it.

### Side by side — 5-minute video, production configuration

| Option | Configuration | **Cost / 5 min** | Relative |
|---|---|---|---|
| **Gemini 3.8 Flash, Vertex, 1 fps** ← recommended | 84k video tokens, native audio | **$0.078** | **1.0×** |
| Gemini 3.8 Flash, Vertex, 3 fps | 252k tokens, best temporal detail | $0.204 | 2.6× |
| Gemini 3.5 Flash-Lite, 1 fps | cheapest credible | $0.035 | 0.45× |
| **Azure CU + GPT-6.2 Sol** | balanced choice | **$0.370** | **4.7×** |
| Azure CU + GPT-6 Luna | cheapest GPT | $0.256 | 3.3× |
| Azure CU + GPT-6.2 Astra | frontier GPT | $0.730 | 9.4× |
| Azure CU + GPT-5.5 (the series you asked about) | — | $0.573 | 7.3× |
| **VI Basic + GPT-6.2 Sol** | teammate's proposed route | **$0.408** | **5.2×** |
| VI Standard + Sol | richer VI insights | $0.690 | 8.8× |
| VI Advanced + Sol | full VI catalogue | $1.070 | 13.7× |

**Headline: Gemini 3.8 Flash on Vertex ≈ $0.078 per 5-minute video.
Azure CU + a comparable GPT ≈ $0.37. Ratio ≈ 4.7×.**

### The 2027 caveat — this is the number that actually matters

Gemini rates **double on 1 January 2027**, and Azure OpenAI prices are not on that schedule.
So the ratio *narrows*:

| | Today | From 2027-01-01 |
|---|---|---|
| Gemini 3.8 Flash, 1 fps | $0.078 | **$0.156** |
| Azure CU + GPT-6.2 Sol | $0.370 | $0.370 |
| **Ratio** | **4.7×** | **2.4×** |

Still cheaper, but **half the advantage disappears in five weeks.** If someone in the room is
making a five-year platform decision on a 4.7× number, tell them it is a 2.4× number from
January.

### What this means at volume

| Volume | Gemini 3.8, 1 fps | Azure CU + Sol | Difference |
|---|---|---|---|
| 1,000 videos (5 min) | **$78** | $370 | $292 |
| 10,000 videos | **$780** | $3,700 | $2,920 |
| 100,000 videos | **$7,800** | $37,000 | $29,200 |

Honest framing: **these are small absolute numbers.** At 1,000 videos a year, the choice is
between $78 and $370 — a rounding error next to engineering time and Azure minimum commits.
**Do not let a $292/year delta drive an architecture decision.** The real drivers are the
capability differences and the client's procurement requirement.

---

## So which produces better output?

Restating with the corrections in place, and with the honest limits stated:

### Where Gemini wins — and this is the decisive one for us

**Native video + audio on one timeline.** Gemini ingests the file directly: frames *and*
the audio track, aligned. Our prompt explicitly requires *"Analyze the visual UI and the
spoken narration together"* and converts spoken caveats into Negative/Boundary cases. When a
model sees the audio natively, "the presenter said 'you can't do this unless you're an admin'"
lands at a specific timestamp tied to what was on screen at that moment.

Azure has no equivalent. Content Understanding extracts the transcript and *then* the model
reasons over text plus downscaled keyframes. The alignment is reconstructed, not native.

**No hard resolution cap.** CU resizes sampled frames to **512 × 512** (Microsoft's own
limitation note). Gemini at HIGH gives us ~611 × 343 for 16:9 — also small, but it is a token
budget we can raise, not a fixed ceiling.

**Everything else — schema conformance, timestamps, structured output — is roughly a draw.**
Both support schema-driven structured extraction; both give segment timestamps.

### Where Azure wins, and I should be clearer about it

1. **OCR at full resolution.** Video Indexer's OCR reads on-screen text from original-resolution
   frames, not 512² downscales. For "what exactly does this button say?", VI's OCR may genuinely
   beat Gemini reading a downscaled frame. That is a real advantage I dismissed too fast.

2. **Speaker diarization and multilingual ASR.** VI detects up to 16 speakers; CU supports the
   full Azure Speech language set with per-phrase locale detection. Gemini's diarization is basic.
   For narrated enterprise demos with multiple presenters, this matters.

3. **Determinism.** VI's extractive insights are repeatable and traceable to source. A generative
   pipeline can vary run to run. For regulated clients, "why did the system say this?" is easier
   to answer from a VI insight than from a model's output.

### Where the teammate's *specific* pipeline loses

Not because the pieces are bad — but because assembling them gives you **text-only
understanding of a visual product**, and UI state is visual. A disabled "Save" button and an
enabled "Save" button are the same OCR string. Neither VI nor a GPT reading frames will reliably
tell you the difference, and "click Save while it is disabled" is exactly the Negative test case
a QA engineer needs.

### Honest scorecard

| Criterion | Gemini (native) | VI (catalogue + OCR) | CU + GPT |
|---|---|---|---|
| Reads UI labels | Good | **Excellent** (full-res OCR) | Fair (512² frames) |
| UI *state* (enabled/disabled/focused) | Fair | **Poor** | Fair |
| Native audio alignment | **Excellent** | Extracted | Extracted |
| Speaker diarization | Basic | **Excellent** | **Excellent** |
| Temporal order of rapid actions | Fair (1 fps limit) | Poor | Poor |
| Structured schema output | **Excellent** | Limited | **Excellent** |
| Determinism / traceability | Fair | **Excellent** | Fair |
| Cost per 5 min | **$0.078** | $0.408 (incl. LLM) | $0.370 |

**Nobody wins outright.** Gemini is the best single-call fit for our task. Azure's OCR is
better at the one thing we care about most (reading UI text). That suggests something better
than either — see below.

---

## The recommendation this analysis actually supports

**Hybrid: Gemini for understanding, VI's OCR as a text sidecar.** Not "Gemini vs Azure."

```
video ─┬─→ Gemini native video+audio  → visual flow, narration, ordering
       └─→ VI / CU OCR (or our own     → exact UI strings, full resolution
           full-res OCR pass)
                    ↓
         both merged into build_user_prompt()
                    ↓
              TestSuite (unchanged)
```

This is cheap: OCR text is a few thousand tokens. It fixes Gemini's weakest point (reading small
UI text after downscaling) using Azure's strongest one, and it keeps native audio+video.

It also gives Tensai a genuine Azure story — "we use Azure AI for UI text extraction" — without
putting the model that reasons about the demo on the wrong cloud.

**If the client mandates Azure-only**, build the CU + GPT path (§ above), accept roughly 4.7×
the token cost (2.4× from 2027) and the 512² cap, and compensate for the state-detection gap
with the Feature-2 action log — which is cloud-independent and solves exactly that weakness.

---

## Summary table — what to tell the room

| Question | Answer |
|---|---|
| Does GPT-5.5 / 5.6 / Sol / Terra accept video? | **No.** Text + image only, across the entire GPT-5.x and 6.x families. |
| Is the teammate's idea viable? | Not as "attach the video." Viable as *our own* frame extractor + GPT vision. |
| Does Video Indexer understand video? | **Yes** — OCR, scenes, transcript, topics. Through a fixed catalogue, not free-form QA. |
| Cost, 5-min video, Gemini on Vertex | **$0.078** at 1 fps ($0.035 on 3.5 Flash-Lite) |
| Cost, 5-min video, Azure CU + GPT | **$0.256–$0.730** depending on GPT tier; $0.37 with Sol |
| Ratio | **~4.7×** today, **~2.4×** from 2027-01-01 |
| Better output? | Gemini for our task overall; Azure wins specifically on OCR and diarization. |
| Best move | Hybrid — Gemini for reasoning, Azure OCR as a text sidecar. |

---

## Sources

- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) — per-model input/output,
  `3.8 Flash` $0.75/$3.75 through 2026, `3.5 Flash-Lite` $0.30/$2.50, `4 Carbon` $2.00/$10.00
- [Vertex AI / Agent Platform pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) —
  `Gemini 3.8 Flash Input (text, image, video, audio) $0.75` Global through 2026-12-31,
  **$1.50 from 2027-01-01**; all input modalities billed at one rate
- [Azure Content Understanding pricing](https://azure.microsoft.com/en-us/pricing/details/content-understanding/) —
  Video content extraction **$1/hour**; standard contextualization **$1/1M tokens**;
  **1,000,000 contextualization tokens per hour of video**
- [Azure CU pricing explainer](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/pricing-explainer) —
  worked video example: 7,500 input / 900 output tokens per minute of video
- [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/) —
  GPT-5.5 $5/$30, GPT-5.6-terra $2/$12, GPT-5.6-luna $0.20/$1.20, GPT-6.2 Sol $2/$10,
  GPT-6.2 Astra $8/$40, GPT-6 Luna $0.10/$0.50
- [OpenAI `gpt-6.2-astra` model page](https://developers.openai.com/api/docs/models/gpt-6.2-astra) —
  "Audio: Not supported. Video: Not supported."
- [OpenAI `gpt-5.6-sol` model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol) —
  "Video: Not supported"
- [Azure Video Indexer overview](https://learn.microsoft.com/en-us/azure/azure-video-indexer/video-indexer-overview) —
  OCR, scene segmentation, labels, speaker enumeration (16 speakers), generative summaries
- [Azure CU video overview](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/video/overview) —
  512 × 512 frame resolution, ~1 fps sampling
- This repo: `models_registry.py` (registry prices), `schemas.py` (`SYSTEM_INSTRUCTION`),
  `docs/FEATURE-2-ANALYSIS.md` (action order / state detection measurements)
