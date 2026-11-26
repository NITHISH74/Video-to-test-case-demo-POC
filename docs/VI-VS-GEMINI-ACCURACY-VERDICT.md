# Verdict: VI vs Gemini for test-case generation — and OCR

Research date: **2026-11-25**. Sources at the end. All rates official.

---

## 1. Your OCR question — you are mostly right, with one catch

**You are right that Gemini reads on-screen text natively.** Gemini is not blind to text; it
sees frames and reads text in them. That is OCR-quality text extraction, done implicitly by the
vision model. So **you do not need a separate OCR service** to produce test cases.

Google confirms the mechanism and the lever:

> "`media_resolution` … determines the **maximum number of tokens allocated per input image or
> video frame.** Higher resolutions improve the model's ability to read **fine text** or identify
> small details, but increase token usage and latency."
> — [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)

> "If `media_resolution` is set to low, frames are tokenized at 66 tokens per frame. Otherwise,
> frames are tokenized at **258 tokens per frame**."
> — same page

**And here is the catch that matters for you:** our app already defaults to `HIGH`
(`DEFAULT_MEDIA_RESOLUTION = "HIGH"`, `models_registry.py:87`), with the sidebar help text
*"HIGH keeps small UI text (toasts, validation) readable."* That is correct. **If you run Gemini
at LOW you get 66 tokens/frame ≈ 68,000 px ≈ 246×276 — too small to read a button label.** So the
OCR replacement only holds **at HIGH**. Anyone demoing Gemini at LOW to make it look cheap is
producing an unfair comparison.

**But there is a second catch, and it is the important one.** Reading text and understanding
*state* are different things:

| Question | Gemini at HIGH | Dedicated OCR (VI/Vision) |
|---|---|---|
| "What does the button say?" | **Yes**, reliably | **Yes**, reliably, at native res |
| "Was the button *disabled*?" | Weak — greyed-out style is subtle at 611×343 | Also weak — OCR returns text only |
| "Did a toast appear?" | **Yes**, if visible at HIGH | Yes, if OCR catches the short window |
| "Which element was clicked?" | No | No |
| "Order of two clicks < 1 s apart" | No (1 fps) | No |

**Verdict: you do not need OCR as a separate pipeline stage. You do need `media_resolution=HIGH`,
and you still cannot reliably get UI *state* from either.** That is what the Feature-2 action log
is for — and unlike both cloud options, it is free and exact.

**One correction to something I said earlier:** I told you VI's OCR is a worthwhile "text
sidecar" for Gemini. Now that I have read Gemini's docs properly, **that sidecar is not worth
building.** Gemini at HIGH reads UI text fine. The marginal gain does not justify a whole second
Azure pipeline, and it would drag Azure into a Gemini deployment for no measurable benefit.
Scratch that recommendation.

---

## 2. The accuracy verdict — and the reason it is not what you expect

### Can VI generate a test case at all?

Let me be precise, because the distinction decides everything:

**VI's own output is an index, not test cases.** From Microsoft's official docs, the VI feature
list is: transcription, translation, OCR, labels, object detection, scenes/shots/keyframes,
people/faces, speakers, keywords, named entities, topics, sentiment, audio events, clapperboard
detection, black frames, and summaries. **There is no "steps," "test case," "intent," or
"expected result" in that catalogue.** VI cannot generate a test case by itself.

**So your PoC must be VI + an LLM.** VI produces the index → you send that text to GPT (or any
LLM) → it writes test cases. If your teammate's PoC produced test cases, that is what they built.

**This means your comparison is not "VI vs Gemini."** It is:

> **A two-stage pipeline (VI index → LLM) vs a one-stage pipeline (Gemini native video).**

That framing is the whole answer to your accuracy question, and it is why I can predict the
result before you run it:

| What matters for a correct test case | Two-stage VI → LLM | One-stage Gemini |
|---|---|---|
| Reads UI text | **Excellent** (VI OCR, native res) | Good (HIGH only) |
| Sees UI visually | **Lost** — becomes OCR strings in a JSON | **Yes** — sees the pixels |
| Aligns speech to what was on screen | Reconstructed from timestamps | **Native**, same context window |
| Understands *why* an action was blocked | Weak — narration stranded in a separate field | **Strong** — prompt asks for it |
| Notices a disabled button, a validation toast | **Weak** — OCR has no state | **Weak** (better, not solved) |
| Temporal order of fast actions | **Weak** — shot/scene granularity | **Weak** — 1 fps |
| Output shaped as `TestSuite` | Extra prompt layer, lossy | **Native** `response_schema` |

### The verdict, stated plainly

**Gemini will produce the more accurate test cases for screen-recording walkthroughs and demos.**
Not because VI's models are worse — they are excellent, and its OCR beats Gemini's — but because
**generating a test case is a reasoning task over visual + spoken intent on one timeline**, and
the two-stage pipeline throws the visual away and hands the LLM a text report. A test case like
*"Click Save while the button is disabled to verify the error toast appears"* requires seeing that
the button was greyed out. OCR writes `Save` and loses everything else.

**VI's index is not a bad artifact — it is a different artifact.** It is built for *"find the
moment where the presenter mentions pricing,"* not *"write the acceptance criteria for this flow."*
Microsoft routes test-case-shaped output to Content Understanding (schema-driven), not VI — their
own "don't use VI for" column says use Vision, Speech, or Arc for things VI doesn't cover, and
their "use CU for" column literally says *"Generate RAG-ready output from video, including scene
descriptions, transcripts, and key frames."*

**Expected accuracy ordering:**

```
Gemini 3.8 Flash @ HIGH, 1 fps          ← best overall
  > VI Standard + strong LLM            ← best text fidelity, loses visual state
    > VI Basic + strong LLM             ← weakest: no scene semantics, no speakers
      > CU + GPT                        ← schema-good, 512×512 cap hurts UI text
```

**Where VI could actually win:** a video where the *narration* carries the whole flow and the UI
is simple — "first I go to Settings, then I click Users, then I add a name." VI's transcript +
OCR is then genuinely sufficient, and its determinism is a real advantage. **So run the test on
both kinds of video.** That is the deliverable that will settle it.

---

## 3. VI Basic vs Standard vs Advanced — which to test, and which wins value

Official preset contents, both audio and video indexing enabled
([indexing configuration guide](https://learn.microsoft.com/en-us/azure/azure-video-indexer/indexing-configuration-guide)):

| | **Basic** | **Standard** | **Advanced** |
|---|---|---|---|
| Price / min (video) | $0.045 | $0.090 | $0.150 |
| Price / min (audio) | $0.0126 | $0.024 | $0.040 |
| **5-min total** | **$0.288** | **$0.570** | **$0.950** |
| Transcription, translation, captions | ✅ | ✅ | ✅ |
| **OCR** | ✅ | ✅ | ✅ |
| Scenes / shots / keyframes | ✅ | ✅ | ✅ |
| Object detection | ✅ | — | — |
| Labels (visual objects, actions) | ✅ | OCR-derived only | OCR-derived only |
| People / faces | — | ✅ | ✅ |
| **Speakers (diarization)** | — | ✅ | ✅ |
| Keywords, named entities, topics | — | ✅ | ✅ |
| Emotions, sentiment | — | ✅ | ✅ |
| Automatic language detection | — | ✅ | ✅ |
| **Audio event detection** | — | — | ✅ |
| Observed people, matched person | — | — | ✅ |
| Clapperboard / slate / digital pattern | — | — | ✅ |
| Textual logo detection | — | — | ✅ |

### The two facts that decide this

**Fact 1 — Advanced is worthless for us.** Look at what Advanced adds *over Standard*: clapperboard
detection, slate detection, digital patterns, observed people, featured clothing, textual logo
detection, audio events. **Every one of those is film/TV post-production or security-camera
tooling.** A UI walkthrough has no clapperboard, no slate, no featured clothing. You would pay
**$0.950 instead of $0.570 — 67% more — for capabilities that literally cannot fire on your
content.** Advanced is built for broadcasters.

**Fact 2 — Basic is a trap that looks like a bargain.** Basic gives you OCR, scenes, and objects
but **no speakers, no keywords, no named entities, no topics, and no sentiment.** For test-case
generation those are the *most valuable* fields:
- "Speakers" tells you which utterances came from the demoed user vs the narrator explaining.
- "Keywords/named entities" pull out feature names and role names.
- Standard also adds *"Labels (OCR)"* and *"Named entities (OCR)"* — it reasons over the OCR text,
  not just dumps it. Basic just dumps the strings.

So Basic saves $0.28 per video and destroys the semantic layer your LLM needs. **Do not choose Basic.**

### Price-to-performance verdict

| | Cost / 5 min | What you get | Value |
|---|---|---|---|
| **VI Basic + LLM** | $0.408 | OCR strings + scenes, no semantics | **Poor** — cheaped out on the field that matters |
| **VI Standard + LLM** | **$0.690** | Full semantic index: speakers, topics, entities, sentiment, OCR | **Best VI value** — this is the right VI config |
| **VI Advanced + LLM** | $1.070 | Standard + broadcast tooling that cannot fire | **Worst** — money for nothing |
| Gemini 3.8 Flash @ HIGH | **$0.078** | Native video+audio, schema output | **Best overall** |

**If you test VI, test Standard.** Basic will lose to Gemini and you will not know whether the loss
was VI's fault or the preset's. Advanced will cost you $1.07 to learn that clapperboard detection
does not help with a Salesforce walkthrough.

### Token cost note — this surprises people

VI's price is **per minute of input video**, so a 5-minute video costs $0.570 **regardless of how
much text comes back.** Gemini's price is **per token**, so a video with little on-screen text and
sparse narration costs less; a dense one costs more. **For our content — sparse narration, modest
UI text — Gemini's token economics are far better.** VI charges you for 5 minutes of dense visual
analysis even when the frames contain one login form.

---

## 4. Run this test — and it will settle the argument

Your teammate's PoC on VI alone proves VI can produce *something*. It does not prove which is more
*accurate*, because there is no baseline. Here is the minimum fair test.

### Design

```
Same 3 videos × 2 pipeline configurations, scored against hand-written ground truth
```

**Videos** (pick 3, deliberately different):
1. **Feature walkthrough** — 3–5 min, narrated, clicking through one flow → the "narrator tells you
   everything" case where VI should do well.
2. **UI demo** — 3–5 min, narration sparse, lots of state changes (disabled buttons, validation
   errors, toasts) → the case where Gemini should win.
3. **Mixed / settings-heavy** — 5–10 min, tabs, dropdowns, dense small text → the adversarial case.

**Configurations:**
- `gemini-3.8-flash`, `media_resolution=HIGH`, fps=1, `thinking_level=high`
- `gemini-3.8-flash`, `media_resolution=LOW`, fps=1 *(this row proves the resolution claim and
  stops anyone from winning an argument with a deliberately crippled Gemini)*
- **VI Standard** (video+audio) → index JSON → GPT-6.2 Sol → `TestSuite`
- **VI Basic** (video+audio) → same → `TestSuite` *(optional, but it will confirm Basic is a trap)*

**Ground truth:** hand-write the expected steps for each video yourself, once. 10–15 steps each.
No shortcuts — this is the only thing that makes the comparison real.

### Scoring rubric — 6 dimensions, 0–2 each (12 max per video)

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| **Step completeness** | Misses a whole flow | Misses steps | All major steps |
| **Step order** | Wrong order | Minor swaps | Correct |
| **UI labels exact** | Wrong/generic names | Paraphrased | Verbatim as on screen |
| **Preconditions** | Absent | Vague | Correct role/state captured |
| **Expected results** | Absent | Generic | Specific, verifiable |
| **Category spread** | All "Happy Path" | 2 categories used | Negative/Boundary/Edge all justified |

Record per video: **score /12, wall-clock latency, cost, and a one-line failure note.**
Then also note *category counts* — our app requires Happy Path, Negative, Boundary, Edge, Error
Handling, so a pipeline that can only produce Happy Path is failing the schema even if its steps
are good.

### What each outcome means

| Result | Conclusion |
|---|---|
| Gemini wins by ≥3 points on 2 of 3 videos | Ship Gemini. VI PoC stands as a capability demo, not a product path. |
| VI wins on the walkthrough, Gemini on the demo | **Most likely.** Report both honestly; it means pipeline choice depends on video type. |
| VI Standard wins overall | Surprising — re-check that Gemini ran at HIGH and thinking=high before believing it. |
| Both score < 6 | The problem is not the model. Check `SYSTEM_INSTRUCTION` and the prompt, not the vendor. |

### The thing to watch for

**Both pipelines will score 0–1 on UI state.** Neither reliably detects a disabled button. If your
teammate's PoC looks impressive, check whether it is actually asserting state or just restating
narration as steps. *"Click Save"* is not a test case. *"Click Save while disabled → expect error
toast"* is. That difference is where the real accuracy gap will show up, and it is where neither
cloud wins — which is exactly why the Feature-2 action log matters.

---

## 5. Bottom line

| Question | Answer |
|---|---|
| Is OCR needed with Gemini? | **No** — Gemini reads on-screen text natively. **But only at `media_resolution=HIGH`**, which our app already defaults to. |
| Which is more accurate for test cases? | **Gemini**, for walkthroughs and demos. Two-stage VI→LLM discards the visual context the test case needs. |
| Does VI generate test cases? | **No.** It generates an index. Any VI result is VI + an LLM, which is a two-stage pipeline. |
| VI Basic / Standard / Advanced — which wins value? | **Standard.** Basic drops the semantic fields that matter; Advanced adds broadcast tooling that cannot fire on UI video. |
| Price to performance, overall | **Gemini $0.078** (best) → VI Standard $0.690 → VI Basic $0.408 → VI Advanced $1.070 |
| So should we still test VI? | **Yes, but it is a two-hour test with a rubric**, done to close the argument — not a new product direction. |

**Practical move:** run the 3-video test above with VI **Standard** and Gemini **HIGH**. Add the
Basic row only if you want proof for the teammate. Do not spend anything on Advanced.

---

## Sources

- [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) —
  258 tok/frame at non-low resolution, 66 at low; `media_resolution` "maximum number of tokens
  allocated per frame… higher resolutions improve the model's ability to read fine text";
  1 fps static mode; frames + audio included by default
- [Gemini tokens guide](https://ai.google.dev/gemini-api/docs/tokens) — ~300 tokens/second at high
  resolution
- [VI indexing configuration guide](https://learn.microsoft.com/en-us/azure/azure-video-indexer/indexing-configuration-guide) —
  exact Basic / Standard / Advanced insight lists quoted in the table above
- [VI pricing](https://azure.microsoft.com/en-us/pricing/details/video-indexer/) — per-input-minute
  rates; audio and video billed separately
- [Azure AI image and video processing guide](https://learn.microsoft.com/en-us/azure/architecture/data-guide/ai-services/image-video-processing) —
  official use/don't-use tables
- [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/) —
  GPT-6.2 Sol $2/$10 per 1M
- [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) — Gemini 3.8 Flash
  $0.75/$3.75 through 2026-12-31
- This repo: `models_registry.py:87` (`DEFAULT_MEDIA_RESOLUTION = "HIGH"`), `:295`
  (`media_resolution` passed to `GenerateContentConfig`), `schemas.py` (`CATEGORIES`),
  `docs/FEATURE-2-ANALYSIS.md` (state-detection gap measurements)
