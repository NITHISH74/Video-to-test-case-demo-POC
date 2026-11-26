# VI vs CU, and Gemini vs Azure — Plain Explanation

All Azure statements quoted from Microsoft Learn. Sources at the end.

---

## Part 1 — What is VI? What is CU? (short version)

They are **two different Azure services**, both of which accept video. That is where the
similarity ends.

### Video Indexer (VI) — "give me the standard report about this video"

Microsoft's own words:

> "Azure AI Video Indexer is a comprehensive AI solution that enables organizations to extract
> **deep insights** from video (live and uploaded) and audio content. It uses advanced machine
> learning and generative AI models and supports a wide range of capabilities including
> transcription, translation, object detection, and video summarization."
> — [VI overview](https://learn.microsoft.com/en-us/azure/azure-video-indexer/video-indexer-overview)

> "It analyzes video and audio content by running **more than 30 AI models** to generate
> detailed insights."
> — [Azure AI Video Processing Guide](https://learn.microsoft.com/en-us/azure/architecture/data-guide/ai-services/image-video-processing)

**In plain terms:** you upload a video, 30+ prebuilt models run over it, and you get back a large
standard JSON index — transcript, OCR text, scenes, shots, keyframes, objects, faces, speakers,
topics, sentiment, keywords.

**You do not tell it what you want.** The menu is fixed. It is built for *"index and search a
library of media."*

### Content Understanding (CU) — "give me these fields, in my shape"

> "Azure Content Understanding … uses generative AI to process and ingest many types of content,
> including documents, images, videos, and audio, into a **user-defined output format**."
> — [CU overview](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/overview)

> "You define a schema that specifies what to extract, and Azure Content Understanding applies
> generative models to produce structured JSON or RAG-ready Markdown output. It also provides
> **confidence scores and grounding** for each extracted value."
> — [Azure AI Video Processing Guide](https://learn.microsoft.com/en-us/azure/architecture/data-guide/ai-services/image-video-processing)

**In plain terms:** you hand it a schema — "extract these fields from each video segment" — and a
generative model fills it in. You get **your** shape back, with confidence scores.

**You do not get OCR or object bounding boxes from CU.** Microsoft says so explicitly:

> "Don't use Azure Content Understanding for: Standard image analysis, such as object detection
> or OCR. Use Azure Vision for these tasks."

### The one-line difference

| | VI | CU |
|---|---|---|
| Direction | Service decides what to extract → you read its report | **You** decide what to extract → service fills it in |
| Shape of output | Fixed, standard, huge JSON index | **Your** schema, JSON or Markdown |
| Built for | Indexing and searching a media library | Structured data extraction for automation |
| OCR / objects | **Yes** — this is its strength | No — Microsoft says use Vision |
| Video frames | Native resolution | **Resized to 512 × 512** |

### Microsoft's own guidance on when to use which

Straight from the Azure Architecture Center "use / don't use" tables:

**Use CU for:**
- "Generate RAG-ready output from video, including scene descriptions, transcripts, and key
  frames"
- "Segment video into scenes and extract custom metadata for each segment"

**Don't use CU for:**
- "Extract deep video insights, such as celebrity identification, **speaker enumeration**, or
  sentiment analysis across long-form content. **Use Video Indexer** for these tasks."

**Use VI for:**
- "Extract insights from uploaded videos, including transcription, translation, and content
  analysis"
- "Analyze live video streams in real time"
- "Run video analysis on edge devices that have strict data residency requirements … by using
  Azure Arc"

**Don't use VI for:**
- "Extract text from static images. For OCR on images, use Azure Vision."

**Neither one is "the video AI." They are two different products that happen to both take video.**

---

## Part 2 — Advantages and disadvantages

### Gemini (native video)

| ✅ Advantages | ❌ Disadvantages |
|---|---|
| **Video + audio in a single call.** No separate transcription step, no re-alignment. | **Not Azure.** Fails an Azure-only mandate or MACC requirement outright. |
| **Cheapest by far** — $0.078 per 5-min video at 1 fps. | **Doubles 2027-01-01** to $0.156. |
| **Schema-constrained output** already wired in (`response_schema=TestSuite`). | **1 fps misses sub-second actions.** A dialog that flashes for 400 ms may not be sampled. |
| **Reasons about intent**, not just content — can infer "user was blocked by permissions." | **Small UI text degrades** after downscaling to ~611×343. |
| **No fixed taxonomy** — you write the prompt, it answers. | **Basic speaker diarization** compared to VI's 16-speaker enumeration. |
| **No hard resolution cap** — token budget is yours to raise. | **Generative output varies** run to run — harder to defend in a regulated audit. |
| **One API call**, no orchestration, no polling jobs. | Requires GCP org/IAM onboarding for enterprise (Vertex + service account). |
| Already **built, tested, and committed** in this repo. | |

### Azure Video Indexer (VI)

| ✅ Advantages | ❌ Disadvantages |
|---|---|
| **Best OCR available** — native resolution, not downscaled. More reliable than Gemini at LOW/ MEDIUM resolution. | **Fixed taxonomy.** Cannot ask it for "test steps." No schema input. |
| **16-speaker diarization**, 50+ languages, translation, captions. | **Still needs a second LLM call** to produce test cases → you pay twice. |
| **Deep, structured, deterministic** insights — traceable to source. | **Most expensive Azure route.** $0.288 per 5 min at Basic, before any LLM. |
| **Runs inside the client's Azure tenant** — MACC, DPA, residency satisfied. | **Still ~5x Gemini's cost** at any preset. |
| **Azure Arc edge deployment** for strict data residency. | **Async upload → index → poll** pipeline. Slow and stateful to operate. |
| **Live stream** support (irrelevant to us, real for others). | **OCR gives text, not state** — disabled and enabled buttons read identically. |
| | Object/OCR output is a **generic index**, not shaped for `TestSuite`. |
| **Actively developed** — Media Services retired 2024-06-30 and its analysis workloads were *migrated to* VI; it now runs independently, cloud and Arc. | |
| **Actively developed** — Media Services retired 2024-06-30 and its analysis workloads were migrated *to* VI, which now runs independently (cloud + Arc). | |

### Azure Content Understanding (CU)

| ✅ Advantages | ❌ Disadvantages |
|---|---|
| **Schema-driven** — closest Azure product to our `TestSuite` model. | **512 × 512 frame cap.** Worst option for reading small UI text. |
| **Confidence scores + grounding** on every extracted field — good for audit. | **~1 fps sampling** — same temporal blind spot as Gemini, but no way to raise it. |
| **Fastest Azure adapter** (~2–3 days) because it already outputs JSON. | **No OCR, no object detection.** Microsoft routes those to Vision/VI. |
| **One API for documents, images, video, audio.** | **Field extraction billed on top** at the Foundry model's token rate. |
| **Per-unit billing**, predictable. | **Newest service**, thinnest documentation, API still versioning (2026-06-01-preview). |

---

## Part 3 — Cost, 5-minute video, all routes

VI pricing is **per input minute** ([official page](https://azure.microsoft.com/en-us/pricing/details/video-indexer/)):
Basic Video $0.045, Basic Audio $0.0126, Standard Video $0.09, Standard Audio $0.024,
Advanced Video $0.15, Advanced Audio $0.04.

**Note: audio and video are charged separately.** "Indexing is charged for audio analysis, video
analysis, or both."

| Route | Extraction (5 min) | LLM (Sol rate) | **Total** | vs Gemini |
|---|---|---|---|---|
| **Gemini 3.8 Flash, Vertex, 1 fps** | native — included | — | **$0.078** | **1.0×** |
| Gemini 3.5 Flash-Lite, 1 fps | native — included | — | $0.035 | 0.45× |
| CU + GPT-6.2 Sol | $0.083 + $0.167 = $0.250 | $0.120 | **$0.370** | 4.7× |
| **VI Basic (video + audio) + Sol** | 5×$0.045 + 5×$0.0126 = **$0.288** | $0.120 | **$0.408** | **5.2×** |
| VI Standard (video + audio) + Sol | 5×$0.09 + 5×$0.024 = $0.570 | $0.120 | $0.690 | 8.8× |
| VI Advanced (video + audio) + Sol | 5×$0.15 + 5×$0.04 = $0.950 | $0.120 | $1.070 | 13.7× |

**This is the important finding: VI — the option your teammate proposed — is the most expensive
Azure route, not the cheapest.** At Basic it already costs more than Content Understanding, and
it still needs an LLM afterward.

The reason is structural, not a pricing quirk: VI is priced for **indexing a media library once**
and searching it forever. We generate a fresh test suite per video and never search it. VI's
economics assume a reuse pattern we don't have.

---

## Part 4 — Which should we use?

| If the client… | Use | Cost / 5 min |
|---|---|---|
| is fine with GCP (**most clients**) | **Gemini 3.8 Flash**, native video | **$0.078** |
| needs best-in-class UI text reading | Gemini + **VI/Vision OCR as text sidecar** | $0.13 |
| mandates Azure for the reasoning model | **CU + GPT-6.2 Sol** | $0.370 |
| mandates Azure *and* needs speaker analysis | **VI + GPT** (accept the cost) | $0.408 |

**Recommended: Gemini for reasoning + Azure OCR as a sidecar.** Best output, cheapest, and still
gives Tensai a genuine Azure component in the architecture diagram.

**Do not build VI as the primary pipeline.** It is the most expensive option, it cannot express
test steps, and it still needs an LLM. It is a healthy, actively developed service — the Media
Services retirement *fed into* VI rather than endangering it — but it solves indexing, not test
generation.

---

## Sources

- [Azure AI Video Indexer overview](https://learn.microsoft.com/en-us/azure/azure-video-indexer/video-indexer-overview) — "deep insights… 30+ models", cloud vs Arc
- [What is Azure Content Understanding](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/overview) — "user-defined output format", schema, grounding
- [Choose an Azure AI image and video processing technology](https://learn.microsoft.com/en-us/azure/architecture/data-guide/ai-services/image-video-processing) — the official use/don't-use tables for CU and VI, quoted above
- [Video Indexer pricing](https://azure.microsoft.com/en-us/pricing/details/video-indexer/) — per-input-minute rates; audio and video billed separately
- [Content Understanding pricing](https://azure.microsoft.com/en-us/pricing/details/content-understanding/) — video extraction $1/hr; contextualization $1/1M tokens
- [CU pricing explainer](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/pricing-explainer) — 1,000,000 context tokens per hour of video
- [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) — Gemini 3.8 Flash $0.75/$3.75 through 2026-12-31
- [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/) — GPT-6.2 Sol $2/$10
- [CU video overview](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/video/overview) — 512×512 frame cap, ~1 fps
