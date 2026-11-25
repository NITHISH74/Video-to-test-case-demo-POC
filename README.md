# 🎬 Video-to-Test-Case — Streamlit POC

Turn a screen-recorded **software demo / walkthrough video** into a structured, editable, exportable **QA test suite** — using Gemini video understanding that reads **both the visual UI** (screens, toasts, validation, empty states) **and the spoken presenter audio** (caveats, limits, role rules).

| | |
|---|---|
| **UI** | Streamlit (`>=1.40`) |
| **AI** | `google-genai` SDK (the new SDK — `from google import genai`) |
| **Structured output** | Pydantic v2 `response_schema` → guaranteed JSON |
| **Backends** | Free Pilot POC (AI Studio) **or** Enterprise (Vertex AI) |
| **Exports** | CSV (TestRail/Jira/Excel) · JSON · Gherkin `.feature` |

---

## Table of contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Project structure](#project-structure)
4. [Local setup — step by step](#local-setup--step-by-step)
5. [Running the app](#running-the-app)
6. [Using the app](#using-the-app)
7. [Model registry & pricing](#model-registry--pricing)
8. [Gemini 3.x video rules (and how this app follows them)](#gemini-3x-video-rules-and-how-this-app-follows-them)
9. [Dual-backend execution explained](#dual-backend-execution-explained)
10. [Data contract (Pydantic schema)](#data-contract-pydantic-schema)
11. [Troubleshooting](#troubleshooting)
12. [POC limitations & production notes](#poc-limitations--production-notes)

---

## What it does

Upload a `.mp4` / `.mov` / `.webm` / `.mkv` walkthrough, and the app returns:

- A **feature summary** of what the demo shows.
- **Business rules extracted from the presenter's audio** (limits, caveats, role restrictions).
- **Test cases** with category (`Happy Path`, `Negative`, `Boundary`, `Permissions`, `UI/UX`), priority, a `MM:SS-MM:SS` **video evidence window**, the **source basis** (`Observed in UI`, `Spoken by Presenter`, `Inferred Edge Case`), preconditions and full step-by-step actions / test data / expected results.

Everything is editable in an interactive table, survives Streamlit reruns, and exports to CSV, JSON or Gherkin.

```
Videos in  ──▶  Gemini (video + audio)  ──▶  Pydantic TestSuite  ──▶  Edit ──▶  CSV / JSON / .feature
```

---

## Architecture

### High-level flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Streamlit UI  (app.py)                            │
│                                                                              │
│  Sidebar                      Main workspace                                 │
│  ┌────────────────────┐      ┌────────────────────────────────────────────┐   │
│  │ Backend toggle     │      │ st.file_uploader (.mp4/.mov/.webm/.mkv)    │   │
│  │ API key / GCP creds│      │ st.video preview + config summary table    │   │
│  │ Model dropdown     │      │ [ Generate QA Test Cases ]                 │   │
│  │ fps slider         │      └───────────────────┬────────────────────────┘   │
│  │ Media resolution   │                          │                            │
│  │ Thinking level     │                          ▼                            │
│  │ Context inputs     │      ┌────────────────────────────────────────────┐   │
│  └────────────────────┘      │ st.status() pipeline (4 visible stages)    │   │
└──────────────────────────────┴───────────────────┬────────────────────────┴───┘
                                                   │ generate_test_suite(...)
                                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      Caller layer  (models_registry.py)                       │
│                                                                              │
│  MODELS_REGISTRY  ──▶ AnalysisConfig + credentials validation                │
│  build_generation_config()  ──▶  types.GenerateContentConfig                 │
│        • system_instruction      • media_resolution (HIGH/MEDIUM/LOW)        │
│        • thinking_config         • response_mime_type="application/json"     │
│        • response_schema=TestSuite  (NO temperature/top_p/top_k)             │
│                                                                              │
│           ┌────────────────────────────┴────────────────────────────┐        │
│           ▼                                                         ▼        │
│   Mode A: AI Studio                                   Mode B: Vertex AI     │
│   client.files.upload(tmp)                            Part.from_uri(gs://)   │
│   poll client.files.get()                             or Part.from_bytes()   │
│   until ACTIVE / FAILED                               genai.Client(          │
│   finally: files.delete() + tmp.unlink()                vertexai=True, ...)  │
│           │                                                         │        │
│           └────────────────────────────┬────────────────────────────┘        │
│                                        ▼                                     │
│   generate_content(model, contents=[VIDEO_PART, PROMPT], config=...)         │
└────────────────────────────────────────┬─────────────────────────────────────┘
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Gemini API (3.x)                                  │
│   video understanding: frames at `fps` + audio track                         │
│   returns strict JSON conforming to the TestSuite schema                     │
└────────────────────────────────────────┬─────────────────────────────────────┘
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Validation & normalization  (schemas.py)                  │
│   TestSuite.model_validate → normalize_suite()                                │
│   • sequential TC-001… ids        • MM:SS-MM:SS timestamp canonicalization    │
│   • dedupe business rules         • reject empty suites                       │
└────────────────────────────────────────┬─────────────────────────────────────┘
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                 Session state + results UI (app.py / exporters.py)            │
│   st.session_state["analysis_result"]  ── survives every rerun                │
│   5 telemetry metrics: cases · input tokens · output+thinking tokens ·       │
│                        est. cost · latency                                   │
│   editable st.data_editor  ──▶ exports regenerated from the edited table      │
│               CSV (flattened)   JSON (full Pydantic)   Gherkin (.feature)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Design decisions

| Concern | Decision | Why |
|---|---|---|
| **Separation of concerns** | `app.py` = pure UI · `models_registry.py` = Gemini I/O + registry · `schemas.py` = contract + prompts · `exporters.py` = pure transforms | UI changes never touch the Gemini layer and vice-versa |
| **Structured output** | Pydantic `TestSuite` as `response_schema` | No JSON parsing hacks; the model *must* conform |
| **Model switching** | Single `MODELS_REGISTRY` dict → dropdown + pricing + telemetry | Add a model in one place; UI and cost math follow |
| **Dual backend** | One `generate_test_suite()` entry point, two internal runners | Mode A now (free), Mode B later (enterprise) without UI rewrite |
| **Cleanup** | `finally` in both the caller *and* the app layer | Remote Gemini file and local temp copy always released, even on failure |
| **Persistence** | `st.session_state` + versioned widget keys | Editing/downloading/rerunning never clears results |
| **Editable table** | `st.data_editor` (dynamic rows) → re-parsed into Pydantic | Users fix model output in place; exports always match the screen |

### The 4-stage progress contract

`models_registry.ExecutionProgress` emits stage transitions that `app.py` renders inside `st.status()`:

| Stage key | UI label | What happens |
|---|---|---|
| `upload` | Uploading video | AI Studio: temp file → `client.files.upload` · Vertex: `gs://` URI or `from_bytes` |
| `processing` | Processing video | Poll `client.files.get()` while `PROCESSING`; fail fast on `FAILED` |
| `analyzing` | Analyzing video + audio | One `generate_content` call with video part first, prompt second |
| `validating` | Validating schema | JSON → Pydantic `TestSuite` → `normalize_suite()` |

---

## Project structure

```
Video-to-test-case-demo-POC/
├── app.py                      # Streamlit UI: sidebar, upload/preview, progress, results, exports
├── models_registry.py          # MODELS_REGISTRY, AnalysisConfig, backends, Gemini caller, pricing
├── schemas.py                  # Pydantic models, SYSTEM_INSTRUCTION, prompt builder, normalization
├── exporters.py                # TestSuite ⇄ DataFrame, CSV / JSON / Gherkin generation
├── requirements.txt            # streamlit, google-genai, pydantic, pandas
├── .env.example                # credential template
├── .streamlit/config.toml      # theme + 2 GB upload limit
└── README.md
```

---

## Local setup — step by step

### 0. Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended)
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey) for Mode A
- *(Optional, Mode B)* Google Cloud SDK with a project where the Vertex AI API is enabled

### 1. Clone the repository

```bash
git clone https://github.com/NITHISH74/Video-to-test-case-demo-POC.git
cd Video-to-test-case-demo-POC
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (CMD)
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Verify the new SDK (not the deprecated `google-generativeai`):

```bash
python -c "import google.genai, streamlit, pydantic, pandas; print('google-genai OK')"
```

### 4. Configure credentials

**Mode A — Free Pilot POC (AI Studio)** — pick **one** of these three, easiest first:

**Option 1 (recommended, no shell syntax at all):** just start the app and paste the key into the **sidebar** masked password field. Nothing is written to disk and every setting in this step becomes optional.

**Option 2 (environment variable):**

```bash
# macOS / Linux (bash, zsh)
export GEMINI_API_KEY="AIza...your-key"

# Windows PowerShell
$env:GEMINI_API_KEY = "AIza...your-key"

# Windows CMD (not PowerShell)
set GEMINI_API_KEY=AIza...your-key
```

> PowerShell variables are **session-only**. Each new terminal window needs the
> command again — or use Option 1 / Option 3.

**Option 3 (`.env` file):** copy `.env.example` to `.env`, paste your key, then load it from the terminal you will run Streamlit in:

```powershell
# Windows PowerShell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
```

```bash
# macOS / Linux
set -a; source .env; set +a
```

**Mode B — Enterprise Vertex AI (optional)**

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="my-gcp-project"
export GOOGLE_CLOUD_LOCATION="global"
```

Then choose **Enterprise Vertex AI** in the sidebar and enter the same project ID (plus a `gs://` URI if your video is in Cloud Storage).

#### Windows / PowerShell notes

Windows terminals differ, and pasting the wrong shell's syntax produces errors that
look like file problems rather than syntax problems.

**Which shell am I in?** Look at the prompt:

| Prompt starts with | Shell | Use this for the key |
|---|---|---|
| `PS C:\...>` | PowerShell | `$env:GEMINI_API_KEY = "AIza..."` |
| `C:\...>` | CMD | `set GEMINI_API_KEY=AIza...` |
| `user@host:~$` / `MINGW64` | Git Bash | `export GEMINI_API_KEY="AIza..."` |

**Activate the venv first (PowerShell):**

```powershell
cd C:\Users\2000150947\Downloads\Video-to-test-case-demo-POC
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, allow it for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**Common mistakes and the exact errors they produce:**

| What you typed / did | Error you see | Fix |
|---|---|---|
| `export GEMINI_API_KEY=...` in PowerShell | `export : The term 'export' is not recognized...` | Use `$env:GEMINI_API_KEY = "..."` |
| `$env:GEMINI_API_KEY="..."` in CMD | `'$env:GEMINI_API_KEY' is not recognized...` | Use `set GEMINI_API_KEY=...` |
| `set GEMINI_API_KEY = "key"` (spaces around `=`) | `The filename, directory name, or volume label syntax is incorrect.` | No spaces: `set GEMINI_API_KEY=AIza...` |
| Pasted `$env:...` into a plain `cmd.exe` window | `The filename, directory name, or volume label syntax is incorrect.` | Confirm the shell (`PS >` vs `C:\>`) or just use **Option 1** |
| Ran `streamlit run app.py` without activating the venv | `streamlit : The term 'streamlit' is not recognized...` | Activate the venv, or run `python -m streamlit run app.py` |

> **The error `The filename, directory name, or volume label syntax is incorrect.`
> means the command was executed by a shell that does not understand it** — almost
> always PowerShell syntax (`$env:`) pasted into CMD, or a key pasted directly at
> the prompt instead of after the variable name. The app itself is fine; verify in
> the app with `python -c "import os; print(bool(os.environ.get('GEMINI_API_KEY')))"`
> which should print `True`.

### 5. Run the app

```bash
streamlit run app.py
```

Streamlit prints a local URL (usually **http://localhost:8501**) and opens your browser.

---

## Running the app

```bash
# dev with auto-reload
streamlit run app.py

# custom port
streamlit run app.py --server.port 8502
```

> **Note:** `requirements.txt` pins `streamlit>=1.40`, while this POC uses modern `width=`/`height=` layout arguments and `st.data_editor` selectbox columns; install the latest Streamlit (`pip install -U "streamlit>=1.40"`) to get all of them.

---

## Using the app

1. **Pick a backend** in the sidebar (Free Pilot POC is the default).
2. **Paste / provide the API key** — the sidebar shows a warning until a key is found.
3. **Choose a model** from the dropdown (prices are shown inline).
4. **Tune sampling**: `fps` 0.5–3.0 (1.0 is the free-tier-safe default), media resolution `HIGH|MEDIUM|LOW`, thinking level `low|medium|high`.
5. *(Optional)* fill **Module / Feature Name**, **Target User Roles**, **Output Focus** and **Extra PRD Notes** in the sidebar.
6. *(Optional)* open **2. Add briefing or instructions** on the main canvas and type anything the model should know or focus on (see below).
7. **Upload** the video, check the preview + config summary, then click **Generate QA Test Cases**.
8. Watch the four-stage progress panel, then review the metrics row, the business-rules expander, the editable test-case matrix and the detailed step-by-step view.
9. Edit anything you like, then **Download CSV / JSON / Gherkin**.

### Additional instructions box

Step **2** on the main canvas is an optional free-text box (2 000 characters) for anything
you want the model to know: focus areas, known-bug zones, must-cover scenarios, or the
names you use for parts of the UI. Four one-click presets (**Security**, **Error paths**,
**Accessibility**, **Regression**) fill it for you, and **Clear** empties it.

It is sent in its own fenced block:

```
USER INSTRUCTIONS (optional; supplied by the person who uploaded the video)
<<<
...your text...
>>>
```

The block is framed as **guidance, never evidence**. It can steer emphasis, coverage and
vocabulary, but it cannot make a UI element exist that the video does not show — if an
instruction conflicts with the recording, the video wins and the conflict is reported in
`feature_summary`. The box is length-capped, whitespace-normalised, and any `<<<` / `>>>`
inside your text is stripped so it cannot break out of the fence.

Leaving the box empty produces a prompt **byte-identical** to the previous release, so the
feature is fully opt-in.

### Output focus presets

| Focus | Behaviour |
|---|---|
| `Balanced` | Covers happy path, one negative per validated input, stated boundaries, permissions, UI/UX |
| `Negative & Boundary Heavy` | ≥ 60 % Negative/Boundary: invalid inputs, empty states, min/max limits, repeated submissions |
| `Security & Permissions` | Role escalation, unauthenticated access, cross-user data, session expiry, access caveats |

---

## Model registry & pricing

`models_registry.py` → `MODELS_REGISTRY` is the single source of truth for the dropdown, the config summary and the **Est. Cost** metric.

| Model ID | Label | Input $/1M | Output $/1M | Role |
|---|---|---|---|---|
| `gemini-3.8-flash` | Gemini 3.8 Flash | **$0.75** | **$3.75** | **Default** — free AI Studio POC |
| `gemini-4-hydrogen` | Gemini 4 Hydrogen | $1.50 | $7.50 | Free AI Studio Gemini 4 alternative |
| `gemini-4-neon` | Gemini 4 Neon | $3.00 | $15.00 | Paid escalation for complex demos |
| `gemini-3.5-flash-lite` | Gemini 3.5 Flash-Lite | $0.30 | $2.50 | High-volume free fallback |

**Cost formula** (thinking tokens are billed as output):

```
cost = input_tokens / 1e6 × input_price
     + (output_tokens + thinking_tokens) / 1e6 × output_price
```

Token counts come straight from `response.usage_metadata` (`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`). Vertex pricing defaults to the same table until `*_price_per_million_vertex` overrides are filled in.

**Add a model** — one dict entry, nothing else:

```python
MODELS_REGISTRY["gemini-next"] = {
    "label": "Gemini Next",
    "description": "Experimental",
    "input_price_per_million": 1.0,
    "output_price_per_million": 4.0,
    "input_price_per_million_vertex": None,
    "output_price_per_million_vertex": None,
    "supports_thinking": True,
    "is_default": False,
}
```

---

## Gemini 3.x video rules (and how this app follows them)

| Rule | Implementation |
|---|---|
| Video part **before** the text prompt | `build_contents()` → `[video_part, Part.from_text(prompt)]` |
| Frame sampling via metadata | `types.VideoMetadata(fps=fps)` attached to the video `Part` |
| `system_instruction` | `SYSTEM_INSTRUCTION` in `schemas.py` (analyze UI **and** audio, convert caveats to Negative/Boundary/Permissions, cite `MM:SS-MM:SS`, never invent UI) |
| Media resolution selectable | `media_resolution=types.MediaResolution.MEDIA_RESOLUTION_{HIGH,MEDIUM,LOW}` (default HIGH) |
| Thinking level | `thinking_config=types.ThinkingConfig(thinking_level=...)` with `low`/`medium`/`high` — **`minimal` is never offered or sent** |
| Structured output | `response_mime_type="application/json"` + `response_schema=TestSuite` |
| **No legacy sampling params** | `temperature`, `top_p`, `top_k` are never set (Gemini 3.8 Flash ignores them) |
| **Transient-failure retries** | `build_http_options()` passes `HttpRetryOptions` (5 attempts, exponential backoff with jitter, max 30 s) for `408/429/500/502/503/504`. The SDK never retries unless this is supplied, so a single `503 UNAVAILABLE` would otherwise abort the run |
| **User instructions block** | Optional main-canvas box → `PromptContext.user_instructions` → fenced `USER INSTRUCTIONS` section in `build_user_prompt()`; framed as guidance-only so it can never fabricate UI. |
| **New SDK only** | `from google import genai` / `from google.genai import types`; no `google-generativeai`, no `genai.configure()` |

All of the above live in one place — `build_generation_config()` and `build_contents()` in `models_registry.py` — so the rules are easy to audit.

---

## Dual-backend execution explained

### Mode A — Free Pilot POC (Google AI Studio)

```
uploaded file ──▶ tempfile.NamedTemporaryFile(delete=False)
              ──▶ client.files.upload(file=tmp_path, config=UploadFileConfig(mime_type, display_name))
              ──▶ poll client.files.get(name)  while state == PROCESSING   (2 s interval, 5 min cap)
                    └─ state == FAILED  ──▶ VideoTestSuiteError (fail fast)
              ──▶ Part(file_data=FileData(file_uri=file.uri), video_metadata=VideoMetadata(fps))
              ──▶ client.models.generate_content(...)
              ──▶ finally:  client.files.delete(name)  +  tmp_path.unlink()
```

`app.py` keeps a `finally` of its own for the temp file, so the local copy is removed even if the SDK raises before its own cleanup runs.

### Mode B — Enterprise Vertex AI

```
gs://bucket/video.mp4     ──▶ Part.from_uri(file_uri=..., mime_type=...)
   or uploaded bytes       ──▶ Part.from_bytes(data=...)          (< 256 MB, else clear error)
                           ──▶ genai.Client(vertexai=True, project=..., location=...)  # ADC
                           ──▶ client.models.generate_content(...)
```

Auth uses Application Default Credentials; the Files API upload path is AI-Studio-only by design and Vertex never creates temp files.

---

## Data contract (Pydantic schema)

```python
class TestStep(BaseModel):
    step_number: int
    action: str
    test_data: str
    expected_result: str

class TestCase(BaseModel):
    id: str                                   # "TC-001"
    title: str
    category: Literal["Happy Path", "Negative", "Boundary", "Permissions", "UI/UX"]
    priority: Literal["High", "Medium", "Low"]
    video_timestamp: str                      # "MM:SS-MM:SS" (normalized)
    source_basis: Literal["Observed in UI", "Spoken by Presenter", "Inferred Edge Case"]
    preconditions: list[str]
    steps: list[TestStep]                     # ≥ 1, renumbered sequentially

class TestSuite(BaseModel):
    feature_summary: str
    presenter_business_rules: list[str]
    test_cases: list[TestCase]
```

`normalize_suite()` renumbers ids (`TC-001`, `TC-002`, …), canonicalizes timestamps (`0:12–0:41` → `00:12-00:41`), de-duplicates rules and rejects empty suites with an actionable message.

### Export formats

| Export | Shape | Notes |
|---|---|---|
| **CSV** | One row per test case: `ID, Title, Category, Priority, Video Timestamp, Source Basis, Preconditions, Steps, Step Count` | Steps flattened as `1. action \| test data \| expected`; drop-in friendly for TestRail / Jira / Excel |
| **JSON** | `suite.model_dump(mode="json")`, indented | Full nested structure, re-editable by automation |
| **Gherkin** | `Feature` + `@tags` + `Scenario` + preconditions table + `Given/When/Then` | Tags encode id, category, priority and source basis; evidence windows as comments |

---

## Verification before release

The pipeline was verified end to end before this repository was trimmed down to
the runtime application:

| Area | What was verified |
|---|---|
| **Schema contract** | Literal enforcement for category / priority / source basis, timestamp normalization (`0:12–0:41`, `HH:MM:SS`, invalid windows rejected), step ordering, prompt content |
| **Model registry** | Registry contents & prices, `minimal` thinking level rejected, config never sets `temperature` / `top_p` / `top_k`, video part ordered before the prompt |
| **Gemini wire format** | Real `google-genai` SDK against a local fake Gemini server: resumable upload, polling until `ACTIVE`, fail-fast on `FAILED`, `files.delete()` + temp-file cleanup in `finally`, 429 propagation, `videoMetadata.fps` / `mediaResolution` / `thinkingConfig` / `responseSchema` present |
| **Editable table** | DataFrame ⇄ Pydantic round-trip, dynamic rows with blank/NaN cells, CSV flattening, JSON equality with `model_dump_json`, Gherkin structure |
| **UI** | All sidebar controls & defaults, backend toggle, upload preview, generate-button state, telemetry row, session persistence, error states, download payloads |
| **Full stack** | App widgets → real SDK → HTTP: sidebar choices reach the request body, Files API lifecycle, telemetry rendered from reported usage, reruns do not re-call the API |

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No API key was provided` | Easiest: paste it in the sidebar password field. Or set the env var for **your** shell (`$env:GEMINI_API_KEY="..."` in PowerShell, `set GEMINI_API_KEY=...` in CMD, `export GEMINI_API_KEY=...` in bash) **before** launching Streamlit |
| `The filename, directory name, or volume label syntax is incorrect.` | Shell syntax mismatch — you pasted `$env:...` into CMD (or `set`/`export` into the wrong shell). See [Windows / PowerShell notes](#windows--powershell-notes), or just paste the key into the sidebar |
| `Gemini rejected the uploaded video during processing: …` | Corrupt/unsupported codec or audio-less weirdness — re-encode with `ffmpeg -i in.mov -c:v libx264 -c:a aac out.mp4` |
| `Timed out waiting for Gemini to finish processing` | Very long video; lower `fps`, trim the clip, or retry |
| `429 RESOURCE_EXHAUSTED` | Free-tier quota; switch to `gemini-3.5-flash-lite` or wait |
| `503 UNAVAILABLE` / "This model is currently experiencing high demand" | **Not your code — Google is overloaded.** The app already auto-retries (5 attempts, exponential backoff, up to 30 s apart). If all attempts fail you get a **Retry now** button that re-runs with the same video. Also try `gemini-3.5-flash-lite`, lowering `fps`, or `LOW`/`MEDIUM` media resolution to shrink the request |
| `The model returned JSON that does not match the TestSuite schema` | Retry, raise thinking level, or switch model (the raw preview is included in the message) |
| `zero test cases` | Video too short / no visible UI; try `Balanced` focus or a higher thinking level |
| `Direct inline upload is limited to 256 MB` | Use a `gs://` URI for the Vertex backend |
| `Project or API key must be set when using the Vertex AI API` | Run `gcloud auth application-default login` or enter the project ID |
| Streamlit warns about unknown kwargs | Upgrade Streamlit: `pip install -U "streamlit>=1.40"` |

---

## POC limitations & production notes

- **Vertex pricing** defaults to the AI Studio table; fill the `*_per_million_vertex` fields with your contracted rates.
- **Token/cost** are estimates based on `usage_metadata`, not billing statements.
- The **Files API** files are deleted after each run; nothing is persisted server-side by this app.
- **No video storage** is performed locally beyond the request lifetime (`.gitignore` also excludes `*.mp4/.mov/.webm/.mkv` so customer walkthroughs are never committed).
- For production, move to **Vertex AI**, add per-user quotas, structured logging (prompt/response ids), a job queue for long videos, and pin model versions.

---

Built as a POC to show the **Free AI Studio now → Enterprise Vertex AI later** path with one codebase.
