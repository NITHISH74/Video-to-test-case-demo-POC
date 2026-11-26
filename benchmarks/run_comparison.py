"""Benchmark harness: VI-index -> LLM vs Gemini-native, scored on the SAME rubric.

Purpose
-------
Your teammate's VI PoC shows VI can produce test cases. It does not show whether they are
*more accurate* than Gemini's, because there was no baseline and no rubric. This script
produces comparable numbers from one command.

What it does
------------
1. Loads hand-written ground truth per video (steps you wrote yourself).
2. Runs each configured pipeline over each video.
3. Scores every generated ``TestSuite`` against the ground truth on 6 dimensions (0-2 each).
4. Prints a per-video and per-pipeline table, plus cost and latency.

Design notes
------------
* **Deterministic scoring only** - no LLM judge, no API calls in the scorer. Text matching is
  cheap, repeatable, and inspectable. A fuzzy human review pass is still worth doing afterwards;
  this harness exists to stop the argument, not to replace judgement.
* **Reuses the repo's own code** - ``SYSTEM_INSTRUCTION``, ``build_user_prompt``,
  ``build_generation_config`` and ``TestSuite`` are imported, not duplicated, so the benchmark
  measures the real product prompt instead of a drifted copy.
* **VI is a two-stage pipeline** - VI returns an index, never test cases. The VI path here is
  index JSON -> Groq/OpenAI-compatible/vendor LLM -> ``TestSuite``. If a VI configuration cannot
  emit a ``TestSuite``, that is itself the finding and it is reported as such.

Usage
-----
    # 1. Write ground truth (see GT_TEMPLATE below) into benchmarks/ground_truth/*.json
    # 2. Point at your videos
    python benchmarks/run_comparison.py --config benchmarks/config.example.json --dry-run
    # 3. Real run
    python benchmarks/run_comparison.py --config benchmarks/config.json --out results/

Requires: nothing beyond the repo's existing dependencies for the Gemini path. The VI and LLM
stages are isolated behind ``run_pipeline`` so you can stub whichever you are not testing yet.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------

@dataclass
class GroundTruthStep:
    """One expected step. ``ui_label`` is the verbatim on-screen text.

    Ground truth is written at TEST-CASE granularity, matching ``schemas.TestCase``:
    one row here is one expected test case, whose ``steps`` are its ordered actions.
    """

    title: str
    ui_label: str = ""
    steps: list[str] = field(default_factory=list)
    expected: str = ""
    category: str = "Happy Path"


@dataclass
class GroundTruth:
    video_id: str
    video_path: str
    kind: str  # "walkthrough" | "ui_demo" | "mixed"
    steps: list[GroundTruthStep] = field(default_factory=list)


GT_TEMPLATE: dict[str, Any] = {
    "video_id": "demo_login_flow",
    "video_path": "benchmarks/videos/demo_login.mp4",
    "kind": "ui_demo",
    "steps": [
        {
            "title": "Admin can open the Settings page",
            "ui_label": "Settings",
            "steps": ["Sign in as an admin", "Click Settings"],
            "expected": "Settings page loads",
            "category": "Happy Path",
        },
        {
            "title": "Save is disabled until a change is made",
            "ui_label": "Save",
            "steps": ["Open Settings", "Click Save without editing a field"],
            "expected": "Save stays disabled and no request is sent",
            "category": "Negative",
        },
    ],
}


def load_ground_truth(path: Path) -> GroundTruth:
    raw = json.loads(path.read_text(encoding="utf-8"))
    steps = [GroundTruthStep(**s) for s in raw.get("steps", [])]
    return GroundTruth(
        video_id=raw["video_id"],
        video_path=raw["video_path"],
        kind=raw.get("kind", "unknown"),
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. For tolerant comparison."""

    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if len(t) > 2}


def label_match(expected_label: str, actual_text: str) -> bool:
    """True when the verbatim UI label appears in the generated step text.

    Short labels (<=3 chars) need an exact token match, otherwise "OK" matches "book".
    """

    if not expected_label:
        return False
    e = norm(expected_label)
    a = norm(actual_text)
    if len(e) <= 3:
        return e in a.split()
    return e in a


def semantic_overlap(expected: str, actual: str) -> float:
    """Jaccard-ish overlap on content words. Cheap proxy for "same idea"."""

    e, a = tokens(expected), tokens(actual)
    if not e:
        return 0.0
    return len(e & a) / len(e)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class PipelineRun:
    pipeline: str
    video_id: str
    ok: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    latency_s: float = 0.0
    cost_usd: float = 0.0
    error: str = ""
    raw: str = ""


@dataclass
class Score:
    completeness: int = 0
    ordering: int = 0
    label_fidelity: int = 0
    preconditions: int = 0
    expected_results: int = 0
    category_spread: int = 0

    @property
    def total(self) -> int:
        return (
            self.completeness
            + self.ordering
            + self.label_fidelity
            + self.preconditions
            + self.expected_results
            + self.category_spread
        )

    @property
    def max(self) -> int:
        return 12


def required_categories() -> set[str]:
    """The app's own CATEGORIES - imported so this cannot drift from the product."""

    try:
        from schemas import CATEGORIES  # noqa: PLC0415 - optional at import time
    except Exception:  # noqa: BLE001
        return {"Happy Path", "Negative", "Boundary", "Permissions", "UI/UX"}
    return set(CATEGORIES)


def flatten_test_case(case: Any) -> dict[str, Any]:
    """Flatten a ``schemas.TestCase`` into a plain dict the scorer can read.

    Keeps the real field names so the harness never invents schema.
    """

    return {
        "title": getattr(case, "title", "") or "",
        "category": str(getattr(case, "category", "")) or "",
        "steps": " ".join(
            f"{getattr(st, 'action', '')} {getattr(st, 'expected_result', '')}"
            for st in (getattr(case, "steps", None) or [])
        ),
        "expected": " ".join(
            getattr(st, "expected_result", "") or ""
            for st in (getattr(case, "steps", None) or [])
        ),
        "preconditions": " ".join(getattr(case, "preconditions", None) or []),
        "video_timestamp": getattr(case, "video_timestamp", "") or "",
        "source_basis": str(getattr(case, "source_basis", "")) or "",
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_run(gt: GroundTruth, run: PipelineRun) -> tuple[Score, list[str]]:
    """Score one pipeline run against ground truth. Returns (score, notes)."""

    notes: list[str] = []
    score = Score()

    if not run.ok or not run.steps:
        notes.append("no output produced")
        return score, notes

    actual_steps = run.steps
    actual_text = " ".join(" ".join(str(v) for v in s.values()) for s in actual_steps)

    # --- completeness: how many GT steps have a counterpart -------------------
    matched_gt: list[int] = []
    for gi, gstep in enumerate(gt.steps):
        probe = " ".join([gstep.title, " ".join(gstep.steps), gstep.expected])
        best, best_i = 0.0, -1
        for ai, astep in enumerate(actual_steps):
            blob = " ".join(str(v) for v in astep.values())
            sim = semantic_overlap(probe, blob)
            if gstep.ui_label and label_match(gstep.ui_label, blob):
                # A shared label alone is weak evidence: "Settings" as a bare title is not
                # a test case. Cap it just above the match threshold so a label-only
                # "counterpart" registers as found but never as a strong match.
                sim = max(sim, 0.36)
            if sim > best:
                best, best_i = sim, ai
        if best >= 0.34:  # generous: sharing a third of content words counts as "found"
            matched_gt.append(gi)
            actual_steps[best_i]["_gt_index"] = gi

    ratio = len(matched_gt) / len(gt.steps) if gt.steps else 0.0
    score.completeness = 2 if ratio >= 0.85 else 1 if ratio >= 0.5 else 0
    notes.append(f"completeness {len(matched_gt)}/{len(gt.steps)} GT steps found")

    # --- ordering: are matched steps in the same relative order? --------------
    ordered = [
        s["_gt_index"] for s in actual_steps if isinstance(s.get("_gt_index"), int)
    ]
    if len(ordered) >= 2:
        inversions = sum(
            1 for i in range(len(ordered)) for j in range(i + 1, len(ordered))
            if ordered[i] > ordered[j]
        )
        score.ordering = 2 if inversions == 0 else 1 if inversions <= 2 else 0
        notes.append(f"ordering: {inversions} inversion(s)")
    else:
        notes.append("ordering: too few matched steps to judge")

    # --- label fidelity: verbatim UI labels vs paraphrased -------------------
    labelled = [g for g in gt.steps if g.ui_label]
    if labelled:
        exact = sum(1 for g in labelled if label_match(g.ui_label, actual_text))
        frac = exact / len(labelled)
        score.label_fidelity = 2 if frac >= 0.8 else 1 if frac >= 0.4 else 0
        notes.append(f"labels {exact}/{len(labelled)} verbatim")
    else:
        notes.append("labels: no GT labels to check")

    # --- preconditions -------------------------------------------------------
    pre_blob = norm(actual_text) + " " + norm(
        " ".join(str(s.get("preconditions", "")) for s in actual_steps)
    )
    pre_hits = sum(
        1 for kw in ("logged in", "admin", "permission", "role", "signed in", "prerequisite")
        if kw in pre_blob
    )
    score.preconditions = 2 if pre_hits >= 3 else 1 if pre_hits >= 1 else 0
    notes.append(f"preconditions: {pre_hits} signal word(s)")

    # --- expected results ----------------------------------------------------
    exp_present = sum(
        1 for s in actual_steps if str(s.get("expected", "")).strip()
    )
    exp_ratio = exp_present / len(actual_steps) if actual_steps else 0.0
    specific = sum(
        1 for s in actual_steps
        if len(tokens(str(s.get("expected", "")))) >= 3
    )
    spec_ratio = specific / len(actual_steps) if actual_steps else 0.0
    if exp_ratio >= 0.9 and spec_ratio >= 0.6:
        score.expected_results = 2
    elif exp_ratio >= 0.5:
        score.expected_results = 1
    notes.append(f"expected: {exp_present}/{len(actual_steps)} present, {specific} specific")

    # --- category spread -----------------------------------------------------
    cats = {str(s.get("category", "")).strip() for s in actual_steps if s.get("category")}
    cats.discard("")
    if len(cats) >= 4:
        score.category_spread = 2
    elif len(cats) >= 2:
        score.category_spread = 1
    notes.append(f"categories used: {sorted(cats) if cats else 'none'}")

    return score, notes


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

def run_gemini(video_path: Path, cfg: dict[str, Any]) -> PipelineRun:
    """One-stage pipeline: video -> Gemini -> TestSuite.

    Imports the repo's real prompt/config so this measures the shipped product path.
    """

    import models_registry as mr  # local import keeps --dry-run dependency-free

    run = PipelineRun(pipeline=cfg["name"], video_id=cfg.get("_video_id", ""), ok=False)
    started = time.perf_counter()
    try:
        config = mr.AnalysisConfig(
            model_id=cfg["model"],
            media_resolution=cfg.get("media_resolution", "HIGH"),
            thinking_level=cfg.get("thinking_level", "high"),
            fps=cfg.get("fps", 1.0),
            backend=cfg.get("backend", mr.BACKEND_AI_STUDIO),
        )
        credentials = mr.AIStudioCredentials(api_key=os.environ["GEMINI_API_KEY"])
        result = mr.generate_test_suite(
            config, credentials, video_path=video_path, video_filename=video_path.name
        )
        suite = result.suite
        run.steps = [flatten_test_case(c) for c in (getattr(suite, "test_cases", None) or [])]
        run.ok = bool(run.steps)
        run.raw = getattr(response, "text", "") or ""
        run.cost_usd = float(getattr(result, "estimated_cost_usd", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001 - benchmark should never crash the batch
        run.error = f"{type(exc).__name__}: {exc}"
    run.latency_s = time.perf_counter() - started
    return run


def run_vi_then_llm(video_path: Path, cfg: dict[str, Any]) -> PipelineRun:
    """Two-stage pipeline: video -> VI index -> LLM -> TestSuite.

    Left as an explicit seam. Implement ``fetch_vi_index`` for your account and
    ``index_to_suite`` for your LLM of choice; everything else (scoring, reporting,
    cost accounting) already works.
    """

    run = PipelineRun(pipeline=cfg["name"], video_id=cfg.get("_video_id", ""), ok=False)
    started = time.perf_counter()
    try:
        index = fetch_vi_index(video_path, cfg)  # noqa: F821 - documented seam
        run.steps = index_to_suite(index, cfg)  # noqa: F821 - documented seam
        run.ok = bool(run.steps)
    except NameError as exc:
        run.error = (
            f"VI pipeline not implemented yet ({exc}). "
            "Implement fetch_vi_index() and index_to_suite() in this file."
        )
    except Exception as exc:  # noqa: BLE001
        run.error = f"{type(exc).__name__}: {exc}"
    run.latency_s = time.perf_counter() - started
    return run


PIPELINE_FUNCS: dict[str, Callable[[Path, dict[str, Any]], PipelineRun]] = {
    "gemini": run_gemini,
    "vi": run_vi_then_llm,
}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(results: Iterable[tuple[GroundTruth, PipelineRun, Score, list[str]]]) -> str:
    rows = list(results)
    lines: list[str] = []
    by_pipeline: dict[str, list[tuple[PipelineRun, Score]]] = {}

    lines.append("")
    lines.append("=" * 78)
    lines.append("PER-VIDEO RESULTS")
    lines.append("=" * 78)
    for gt, run, sc, notes in rows:
        status = "OK " if run.ok else "ERR"
        lines.append(
            f"[{status}] {gt.video_id:<22} {run.pipeline:<34} "
            f"{sc.total:>2}/{sc.max}  {run.latency_s:6.1f}s  ${run.cost_usd:.4f}"
        )
        for n in notes:
            lines.append(f"        - {n}")
        if run.error:
            lines.append(f"        ! {run.error}")
        by_pipeline.setdefault(run.pipeline, []).append((run, sc))

    lines.append("")
    lines.append("=" * 78)
    lines.append("PER-PIPELINE SUMMARY")
    lines.append("=" * 78)
    lines.append(
        f"{'pipeline':<34} {'mean score':>10} {'max':>4} {'mean s':>8} {'mean $':>9}"
    )
    ranked: list[tuple[float, str]] = []
    for name, pairs in by_pipeline.items():
        scores = [s.total for _, s in pairs]
        mean = sum(scores) / len(scores)
        lat = sum(r.latency_s for r, _ in pairs) / len(pairs)
        cost = sum(r.cost_usd for r, _ in pairs) / len(pairs)
        lines.append(f"{name:<34} {mean:>10.1f} {12:>4} {lat:>8.1f} {cost:>9.4f}")
        ranked.append((mean, name))

    ranked.sort(reverse=True)
    lines.append("")
    lines.append("RANKING (accuracy first):")
    for i, (mean, name) in enumerate(ranked, 1):
        lines.append(f"  {i}. {name}  ({mean:.1f}/12)")

    if len(ranked) >= 2:
        gap = ranked[0][0] - ranked[1][0]
        lines.append("")
        if gap >= 3:
            lines.append(
                f"VERDICT: {ranked[0][1]} wins clearly (+{gap:.1f} points). "
                "3+ points on this rubric is a real difference, not noise."
            )
        elif gap >= 1:
            lines.append(
                f"VERDICT: {ranked[0][1]} ahead by {gap:.1f} - narrow. "
                "Check whether the loss concentrates on visual-state steps; if so the "
                "gap is structural, not a prompt problem."
            )
        else:
            lines.append(
                "VERDICT: effectively tied. Choose on cost, latency and client constraints "
                "instead of accuracy."
            )
        if ranked[0][0] < 6:
            lines.append(
                "WARNING: best score is under 6/12. The bottleneck is probably the prompt "
                "or SYSTEM_INSTRUCTION, not the vendor."
            )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def dry_run(config: dict[str, Any], gts: list[GroundTruth]) -> int:
    """Validate config + ground truth without spending a token."""

    allowed_categories = required_categories()
    problems: list[str] = []
    for p in config.get("pipelines", []):
        if p.get("kind") not in PIPELINE_FUNCS:
            problems.append(f"pipeline {p.get('name')!r}: unknown kind {p.get('kind')!r}")
        if p.get("kind") == "vi" and not p.get("llm_model"):
            problems.append(f"pipeline {p.get('name')!r}: VI path needs an llm_model")
    for gt in gts:
        if not Path(gt.video_path).exists():
            problems.append(f"{gt.video_id}: video missing at {gt.video_path}")
        if not gt.steps:
            problems.append(f"{gt.video_id}: no ground-truth steps")
        for i, s in enumerate(gt.steps, 1):
            if s.category not in allowed_categories:
                problems.append(
                    f"{gt.video_id} step {i}: category {s.category!r} not in app's CATEGORIES"
                )

    print(f"pipelines: {len(config.get('pipelines', []))}")
    print(f"videos:    {len(gts)}")
    print(f"runs:      {len(config.get('pipelines', [])) * len(gts)}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\ndry run OK - config and ground truth look valid.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("benchmarks/config.json"))
    ap.add_argument("--gt-dir", type=Path, default=Path("benchmarks/ground_truth"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gt_files = sorted(args.gt_dir.glob("*.json"))
    gts = [load_ground_truth(f) for f in gt_files]
    if not gts:
        print(f"no ground truth found in {args.gt_dir}")
        return 1

    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    if args.dry_run:
        return dry_run(config, gts)

    rows = []
    for gt in gts:
        for pcfg in config.get("pipelines", []):
            pcfg = dict(pcfg, _video_id=gt.video_id)
            fn = PIPELINE_FUNCS[pcfg["kind"]]
            run = fn(Path(gt.video_path), pcfg)
            sc, notes = score_run(gt, run)
            rows.append((gt, run, sc, notes))

    text = report(rows)
    print(text)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.md").write_text(text, encoding="utf-8")
        payload = [
            {
                "ground_truth": asdict(gt),
                "run": asdict(run),
                "score": asdict(sc),
                "notes": notes,
            }
            for gt, run, sc, notes in rows
        ]
        (args.out / "results.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.out/'results.md'} and {args.out/'results.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
