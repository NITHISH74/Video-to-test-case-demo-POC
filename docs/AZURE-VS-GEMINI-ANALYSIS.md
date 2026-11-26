# Azure-Only Clients: Azure vs Gemini/Vertex — Deep Research & Verdict

Research date: **2026-11-25**. Prices and product status change; re-verify before quoting a client.

Question from the team: *"Most clients have Azure resources, not Vertex AI. Should we build the
Azure method (Video Indexer + GPT-5) instead?"*

---

## Verdict (read this first)

| Claim | Verdict |
|---|---|
| "We must switch to Azure because clients lack Vertex" | **Premise is partly wrong.** See §1 — the app's default backend needs no Vertex at all. |
| Teammate's method: **Video Indexer + GPT-5** | **Not viable as stated.** GPT-5 has no video input, and Video Indexer's LLM features never see pixels. See §2. |
| Is Azure capable of this at all? | **Yes** — but with **Content Understanding**, not Video Indexer, and with a **512×512 frame cap** that hurts UI demos. See §4–5. |
| Output quality: Azure vs Gemini | **Gemini is ahead for screen-recording UI demos.** Not by a landslide, and not for the reasons usually given. See §5. |
| Cost | **Azure ≈ 4× more expensive per hour of video.** See §6. |
| What should we actually build? | **A third backend, not a replacement.** The code is already structured for it. See §8. |

**Bottom line:** the senior's *commercial* instinct is sound — support Azure because that is
where client budget and compliance live. The *technical* plan in the room is wrong on two
counts and would produce a worse product for more money if built as described.

---

## 1. First, correct the premise: Vertex is not the only Gemini path

The app has **two** backends, and Vertex is the *second* one:

| Backend | What the client needs | Auth | Free tier |
|---|---|---|---|
| **Free Pilot (AI Studio)** — *the default* | **Just an API key** | `GEMINI_API_KEY` | Yes |
| Enterprise (Vertex AI) | GCP project, IAM, billing | Service account / ADC | No |

So *"the client has no Vertex AI"* does **not** mean *"the client cannot use Gemini."* They can
use the AI Studio backend today with a key and no GCP project at all.

**The legitimate version of the concern** is different, and worth taking seriously:

- A client may ban Google entirely on compliance grounds, not just Vertex.
- Many enterprises will not sign off on an AI Studio API key as a production credential —
  the **free tier trains on your data**; the paid tier does not. That distinction is exactly
  what pushes enterprise buyers to Vertex or to Azure.
- Clients with an Azure Enterprise Agreement want the spend on the MACC they already committed to.
- Data-residency and DPA requirements are often already satisfied inside the client's Azure tenant.

**So: challenge the premise, but accept the underlying driver.** The real ask is *"support our
cloud,"* not *"abandon Gemini."*

---

## 2. The teammate's method has two technical faults

### Fault 1 — GPT-5 has no video input

From OpenAI's own model page for `gpt-5`:

```
Modalities
  Text    Input and output
  Image   Input only
  Audio   Not supported
  Video   Not supported
```

`gpt-5` is also now marked **Deprecated**. The current flagships (GPT-6.2 Astra / Sol, GPT-6.1
Terra, GPT-6 Luna) are all **text + image input, text output** — no video modality anywhere in
the API.

**Consequence:** "attach the video to GPT-5" is not a thing you can do. Any GPT-based pipeline
requires *you* to extract frames and transcribe audio yourself, then send images + text. That is
a frame-stack workaround, not native video understanding — and it is the single biggest quality
gap versus Gemini, which ingests video natively with interleaved audio.

### Fault 2 — Video Indexer's LLM features never see the video

Azure AI **Video Indexer** is an *insight catalog*: upload → index → poll → get a fixed-taxonomy
JSON (who spoke, what they said, on-screen text, topics, scenes). Its generative features run
**on the extracted insight text, not on the pixels.**

Two practical consequences:

- If the fixed taxonomy didn't capture it, **no later LLM can find it**. You cannot ask VI a
  question its models weren't built to answer.
- Video Indexer is a **legacy-adjacent** product: its Azure Media Services dependency was retired
  (classic accounts retired 2024-06-30), and Microsoft's current guidance points new video work
  at **Content Understanding**, not Video Indexer.

**So "Video Indexer + GPT-5" is the wrong pairing of two wrong parts.** The correct Azure
pairing is **Content Understanding + a current GPT model.**

### The improved version of the teammate's idea

> **Azure Content Understanding** (content extraction: transcript, shot detection, keyframes)
> → **custom analyzer fields** for summaries/actions → **GPT-6.x** used by Content Understanding
> for field extraction → our app consumes the structured JSON and maps it into `TestSuite`.

That is a real, GA-supported pipeline. It still has the quality problems in §5.

---

## 3. There is a third option nobody raised

**Frame extraction + GPT vision, fully under our control.** We already prototyped the frame
extraction for Feature 2 (~3.9× realtime, no ffmpeg needed). The pipeline would be:

```
video → our frame extractor (choose fps + resolution freely)
      → Whisper/Azure Speech transcript with timestamps
      → GPT-6.x vision with frames + transcript + our TestSuite schema
```

**Why it may beat Content Understanding for this specific job:**

- **We control frame resolution.** No 512×512 cap. We can send 1280×720 crops of the UI region.
- **We control sampling.** No forced ~1 fps.
- **No shot detection.** Critical — see §5.
- **Cheap.** Pay only GPT tokens; no $1/hr extraction + $1/hr contextualization tax.

**Why it is worse:** it is *our* pipeline to maintain and tune; no managed audio diarization; more
code. But it is strictly more capable than Content Understanding for UI demos, and it is the
option I would actually benchmark first.

---

## 4. What Azure's current video stack actually is

**Azure AI Content Understanding** (GA API `2025-11-01`; preview `2026-06-01-preview`).

Two stages:

1. **Content extraction** — transcript (WebVTT, sentence timestamps, speaker diarization,
   multilingual), shot detection (`cameraShotTimesMs`), keyframe extraction (`keyFrameTimesMs`).
2. **Field extraction** — a generative model (your Foundry deployment) fills custom fields per
   segment. You define the schema in natural language; it returns structured JSON, with
   confidence and grounding.

Strengths that are genuinely useful to us:

- Schema-driven structured extraction (similar in spirit to our Pydantic `response_schema`).
- Segment-level output with timestamps → maps naturally onto our `video_timestamp` field.
- Speaker diarization and multilingual transcription are stronger than ours today.
- RAG-ready Markdown output.
- Enterprise governance, regional data residency, existing Azure DPA.

---

## 5. Output quality — where Gemini wins, and why

This is the core of your question, so here is the evidence rather than an opinion.

### 5.1 The 512×512 frame cap is the decisive limitation

Microsoft's own documentation, under "Technical constraints and limitations" for video:

> **Frame sampling (~1 FPS)**: The analyzer inspects about one frame per second. Rapid motions
> or single-frame events might be missed.
>
> **Frame resolution (512 × 512 px)**: Sampled frames are resized to 512 pixels square. Small
> text or distant objects can be lost.

Compare with what Gemini gives us today at `HIGH`:

| | Effective frame size (16:9 source) | Pixels | Verdict for UI text |
|---|---|---|---|
| Azure Content Understanding | 512 × 288 (fit into 512²) | ~147 k | 14 px UI text → **~3.7 px: illegible** |
| Gemini `HIGH` (280 tok/frame) | ~611 × 343 | ~210 k | 14 px UI text → **~4.5 px: marginal** |
| Gemini `LOW` (70 tok/frame) | ~305 × 171 | ~52 k | clearly worse |

Both are bad for small UI labels — but **Azure is materially worse**, and it is a *hard cap*
you cannot raise. Gemini's is a token budget you can tune.

**This matters specifically for our use case.** Test-case generation needs to read button
labels, field names, validation copy and table headers. That is exactly "small text" by
Content Understanding's own admission.

### 5.2 Shot detection is designed for camera footage, not screen recordings

Content Understanding's keyframe logic:

> Keyframes are uniformly selected from each camera shot. Each shot includes at least one
> sampled keyframe, even for short shots.

A **screen recording of a software demo is one continuous shot** — there are no camera cuts.
So the keyframe extractor's main signal is absent, and the frames you get are essentially
uniform ~1 fps samples. You pay for a shot-detection pipeline that has nothing to detect.

Gemini has no such assumption: it samples by time and reasons about UI state changes directly.

### 5.3 No native video or audio for GPT

Already covered in §2. In the Content Understanding path this is solved for you (CU does the
extraction), but it is not *native video understanding* — the model sees extracted text plus a
handful of downscaled stills. Gemini sees frames and audio on a shared timeline.

### 5.4 Honest counterpoints — where Azure could win

Do not overstate the Gemini advantage:

- **Audio-heavy demos** where the presenter explains rules: Azure's diarization and multilingual
  ASR may transcribe *better than* Gemini, and our Negative/Boundary cases lean heavily on narration.
- **Long recordings**: CU's segmentation is built for hour-long content with chapters.
- **Structured field extraction** is CU's home turf — arguably cleaner than asking a model for a
  big JSON blob at once.
- **Nobody has benchmarked our actual task.** My assessment is an inference from documented
  limits, not a head-to-head on your videos. §9 is how to settle it.

### 5.5 Quality scorecard (documented capability, not benchmarks)

| Criterion | Gemini (native video) | Azure CU (512², 1 fps) | Frames + GPT (ours) |
|---|---|---|---|
| UI text legibility | **Good** (tunable) | **Poor** (hard cap) | **Best** (we choose) |
| Temporal sampling | Tunable 0.5–3 fps (24 via API) | ~1 fps, fixed | **We choose** |
| Native audio on timeline | **Yes** | No (extracted separately) | No (separate ASR) |
| Speaker diarization | Basic | **Strong** | Depends on ASR choice |
| Structured output | **Excellent** (`response_schema`) | **Excellent** (analyzer schema) | Good |
| Screen-recording fit | **Good** | Poor (shot logic wasted) | **Good** |
| Setup effort for us | Done | High | Medium |

---

## 6. Cost — Azure is roughly 4× more expensive

All figures are **per 1 hour of video**, input-side only, using published list prices on
2026-11-25. Excludes taxes, EA discounts and retries.

### Gemini (this app's registry prices)

`gemini-3.8-flash` at **$0.75 / M input**, `HIGH` = 280 tokens/frame:

| Sampling | Frames | Tokens | Cost / hour |
|---|---|---|---|
| 1 fps (app default) | 3,600 | 1.008 M | **$0.76** |
| 3 fps (app max) | 10,800 | 3.024 M | **$2.27** |
| 1 fps, `LOW` (70 tok) | 3,600 | 0.252 M | **$0.19** |

Cheaper models: `gemini-3.5-flash-lite` $0.30/M → **$0.30/hour** at 1 fps.

### Azure Content Understanding (Microsoft's own worked example)

1 hour of video, GPT-5.2 global, East US:

| Component | Usage | Rate | Cost |
|---|---|---|---|
| Content extraction | 60 min | $1.00 / hour | $1.00 |
| Contextualization | 1 M tokens | $1.00 / M | $1.00 |
| Input tokens | 450 k | $2.00 / M | $0.90 |
| Output tokens | 54 k | $8.00 / M | $0.43 |
| **Total** | | | **$3.33** |

With `gpt-5` rates ($1.25 / $10.00) instead of GPT-5.2: **≈ $3.10 / hour**.

### The comparison

| Option | Cost per hour | Relative |
|---|---|---|
| Gemini 3.8 Flash, 1 fps | **$0.76** | 1.0× |
| Gemini 3.8 Flash, 3 fps | **$2.27** | 3.0× |
| **Azure CU + GPT** | **$3.10 – $3.33** | **~4.1–4.4×** |

**The sting:** Azure at ~$3.33 samples **~1 fps at 512×512**. Gemini at **$2.27** samples
**3 fps at ~611×343** — three times the temporal detail, bigger frames, still 30 % cheaper.
You pay Azure ~4× for **less** visual information.

To be fair: Azure's flat $1.00/hr extraction includes transcription and diarization, which we
would otherwise pay for separately. It is not a pure markup — but it is not close either.

**At scale:** 1,000 hours of demo video = **$760** (Gemini 1 fps) vs **$3,330** (Azure).
Roughly **$2,570 difference per 1,000 hours.**

---

## 7. So is the senior right?

**Right about the commercial reality.** Most enterprise clients do sit in Azure. Ignoring that
loses deals. A vendor-neutral story ("we run on your cloud") is a genuine competitive advantage.

**Wrong if the conclusion is "replace Gemini with Azure."** On the documented evidence:

- Azure's video analyzer gives **less visual detail** for **~4× the cost** on exactly the task
  we do (reading small UI text in a continuous screen recording).
- The teammate's specific proposal (VI + GPT-5) is **not implementable as described**.

**The reconciliation is the third option:** ship **both**, behind one interface. Then the
argument becomes "we already support Azure" instead of "which cloud do we bet the product on."

---

## 8. Recommended architecture — a third backend, not a rewrite

The existing code already isolates the provider. `generate_test_suite()` dispatches to
`_run_ai_studio()` or `_run_vertex()`, and both share `build_user_prompt()`,
`build_generation_config()` and the `TestSuite` schema.

**Add a third runner.** Everything downstream — schema validation, editable table, CSV/JSON/
Gherkin exports, retry handling, the briefing box — stays identical.

```
                      ┌── AI Studio  (API key)          ← today
generate_test_suite ──┼── Vertex AI   (GCP project)     ← today
                      └── Azure       (CU or frames+GPT) ← proposed
                             ↓
                    shared: PromptContext → build_user_prompt()
                            → TestSuite (Pydantic) → exporters
```

**Implementation notes:**

- **Do not fork the prompt.** `SYSTEM_INSTRUCTION` and `build_user_prompt()` must stay shared, or
  output quality will diverge between clouds and you will not be able to compare them.
- **Map, don't restructure.** Azure's segment timestamps map onto `video_timestamp`; analyzer
  fields map onto `TestCase`. Define the mapping once, in the adapter.
- **Start with frames + GPT, not Content Understanding**, if you want the best Azure-side quality
  (§3). CU is the faster path to something working; frames+GPT is the better product.
- **Keep the schema as the contract.** `response_schema` for Gemini; Azure's analyzer schema or
  OpenAI structured outputs for the GPT path. Same Pydantic models behind both.

**Effort estimate:** ~3–5 days for a frames+GPT adapter with the shared schema and exports;
~2–3 days for a Content Understanding adapter. Either is a small multiple of what the
Cursor/Feature-2 work cost — and far cheaper than maintaining two codebases.

---

## 9. Settle the quality question with a benchmark, not an argument

My quality verdict rests on **documented limits**, not a head-to-head on your recordings. That
is an honest weakness. Close it in about a day:

1. Pick **5 real demo videos** — 2 narrated/slow, 2 rapid/silent, 1 error-path.
2. Run each through: **(a)** Gemini 1 fps, **(b)** Gemini 3 fps, **(c)** Azure CU, **(d)** frames+GPT.
3. Blind-score the four suites on:
   - correct **control names** (did it read the button label right?) ← the 512² test
   - correct **step order** (the Feature-2 problem)
   - **timestamp accuracy** (does `MM:SS` point at the real evidence?)
   - **no invented UI** (`source_basis` discipline)
4. Add cost per run and wall-clock time.

That produces a table you can put in front of the client and the senior, with your own videos
as the evidence. **I would not commit the product direction before running it.**

---

## 10. What to say in the meeting

> "You're right that most of our clients are on Azure, and we should support that — but the
> current proposal won't work as described. GPT-5 has no video input at all, and Video Indexer
> never lets the LLM see the footage. The real Azure path is Content Understanding, and it caps
> frames at 512×512 at about 1 fps — which is worse than what we get from Gemini today, at
> roughly four times the price per hour. So the answer isn't Azure *instead of* Gemini. It's a
> third backend behind the same interface, so we can sell to both. Give me five real videos and
> a day, and I'll bring back a side-by-side with quality scores and cost per run. If Azure wins
> on our videos, we lead with Azure."

That is defensible, it respects the senior's concern, and it moves the decision onto evidence.

---

## Sources (checked 2026-11-25)

- OpenAI model page, `gpt-5` — modalities show **Video: Not supported**; model marked *Deprecated*.
  `https://developers.openai.com/api/docs/models/gpt-5`
- OpenAI model catalog — current flagships (GPT-6.2 Astra/Sol, 6.1 Terra, 6 Luna) list text+image
  input only. `https://developers.openai.com/api/docs/models`
- Microsoft Learn — *Content Understanding video overview*, "Technical constraints and
  limitations": ~1 FPS sampling, **512 × 512 px** frame resolution.
  `https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/video/overview`
- Microsoft Learn — *Pricing for Azure Content Understanding*, worked video example ($1.00/hr
  extraction, $1.00/hr contextualization, 1 M contextualization tokens/hour, GPT-5.2 tokens).
  `https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/pricing-explainer`
- Microsoft Learn — *AudioVisual analysis: extracting structured content* (`keyFrameTimesMs`,
  `cameraShotTimesMs`, `transcriptPhrases`).
  `https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/video/elements`
- Microsoft Learn — *Video Indexer account types* (classic accounts retired 2024-06-30; AMS
  dependency removed).
  `https://learn.microsoft.com/en-us/azure/azure-video-indexer/accounts-overview`
- Microsoft Learn — *Choose an Azure AI image and video processing technology* (CU vs Video
  Indexer responsibilities).
  `https://learn.microsoft.com/en-us/azure/architecture/data-guide/ai-services/image-video-processing`
- Gemini API pricing references for `gemini-3.8-flash` ($0.75/$3.75 per M through 2026-12-31) and
  `gemini-3.5-flash-lite` ($0.30/$2.50) — matches this repo's `MODELS_REGISTRY`.
- This repo: `models_registry.py` `MODELS_REGISTRY`, `schemas.py` `SYSTEM_INSTRUCTION`,
  `docs/FEATURE-2-ANALYSIS.md` (the fps/frame-resolution measurements Azure's cap is compared against).
