#!/usr/bin/env python3
"""
timing_report.py — Pipeline timing analysis using pandas DataFrames.

Usage:
    python timing_report.py respons.json
    curl -s -X POST http://localhost:8000/v1/ghibli-qr ... | python timing_report.py
"""

import json
import sys
import pandas as pd

STEP_LABELS = {
    # Validation sub-steps
    "validation_url_check":    "  ↳ URL Check",
    "validation_download":     "  ↳ Image Download",
    "validation_face_detect":  "  ↳ Face Detection",
    "validation":              "Input Validation (total)",
    # Identity check sub-steps
    "identity_src_download":   "  ↳ Source Download",
    "identity_face_detect":    "  ↳ Face & Hue Detect",
    "identity_check":          "Identity Drift Check (total)",
    # Other stages
    "stage1_api_submit":       "Stage 1 — API Submit",
    "qr_generation":           "QR Code Generation",
    "stage1_wait":             "Stage 1 — Model Wait",
    "stage1_rehost":           "Stage 1 — Re-host Image",
    "stage2_api_submit":       "Stage 2 — API Submit",
    "stage2_wait":             "Stage 2 — Model Wait",
    "total":                   "TOTAL",
}

STEP_DESCRIPTIONS = {
    "validation_url_check":    "Regex — public URL, no localhost/private IP",
    "validation_download":     "HTTP download + PIL decode (Layer 2)",
    "validation_face_detect":  "MediaPipe BlazeFace inference (Layer 3A)",
    "validation":              "Sum of url_check + download + face_detect",
    "identity_src_download":   "Download original photo for comparison",
    "identity_face_detect":    "MediaPipe detection + skin-tone hue comparison",
    "identity_check":          "Sum of src_download + face_detect (disabled → 0 s)",
    "stage1_api_submit":       "HTTP submit of Ghibli generation task to KIE API",
    "qr_generation":           "Build QR code, overlay on lock screen, save to disk",
    "stage1_wait":             "Await KIE webhook callback (Ghibli model)",
    "stage1_rehost":           "Download Stage 1 output & re-host on local server",
    "stage2_api_submit":       "HTTP submit of Ghibli+QR composition task to KIE API",
    "stage2_wait":             "Await KIE webhook callback (composition model)",
    "total":                   "End-to-end wall-clock time",
}

ORDERED_STEPS = [
    "validation_url_check",
    "validation_download",
    "validation_face_detect",
    "validation",
    "stage1_api_submit",
    "qr_generation",
    "stage1_wait",
    "stage1_rehost",
    "identity_src_download",
    "identity_face_detect",
    "identity_check",
    "stage2_api_submit",
    "stage2_wait",
    "total",
]

# Sub-step keys (displayed indented, excluded from system/api totals to avoid double-counting)
SUBSTEPS = {
    "validation_url_check", "validation_download", "validation_face_detect",
    "identity_src_download", "identity_face_detect",
}

# Steps that run inside this system (not waiting on external API)
SYSTEM_STEPS   = ["validation", "stage1_api_submit", "qr_generation",
                  "stage1_rehost", "identity_check", "stage2_api_submit"]
API_WAIT_STEPS = ["stage1_wait", "stage2_wait"]


# ─────────────────────────── helpers ────────────────────────────────────────

def load_timings(path: str | None) -> dict:
    if path:
        with open(path) as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)

    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], dict) and "timings" in payload["data"]:
            return payload["data"]["timings"]
        if "timings" in payload:
            return payload["timings"]

    raise ValueError(
        "Could not find a 'timings' key.\n"
        "Expected the full /v1/ghibli-qr API response or a dict with a 'timings' key."
    )


def _bar(fraction: float, width: int = 22) -> str:
    filled = round(min(fraction, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def _divider(char: str = "─", width: int = 100) -> None:
    print(char * width)


# ─────────────────────────── DataFrame builders ─────────────────────────────

def build_pipeline_df(timings: dict) -> pd.DataFrame:
    total_s = timings.get("total") or 1
    rows = []
    for key in ORDERED_STEPS:
        if key not in timings or key == "total":
            continue
        val_s  = timings[key]
        frac   = val_s / total_s
        rows.append({
            "Stage":       STEP_LABELS[key],
            "Description": STEP_DESCRIPTIONS[key],
            "Time (s)":    round(val_s, 3),
            "Time (ms)":   round(val_s * 1000),
            "% of Total":  round(frac * 100, 2),
            "Chart":       _bar(frac),
            "_substep":    key in SUBSTEPS,
        })
    return pd.DataFrame(rows)


def build_validation_df(timings: dict) -> pd.DataFrame:
    total_s    = timings.get("total") or 1
    val_s      = timings.get("validation", 0)
    system_s   = sum(timings.get(k, 0) for k in SYSTEM_STEPS)
    api_wait_s = sum(timings.get(k, 0) for k in API_WAIT_STEPS)

    rows = [
        {
            "Category":   "Input Validation only",
            "Time (s)":   round(val_s, 3),
            "Time (ms)":  round(val_s * 1000),
            "% of Total": round(val_s / total_s * 100, 2),
            "Note":       "validation step alone",
        },
        {
            "Category":   "System Processing (all internal steps)",
            "Time (s)":   round(system_s, 3),
            "Time (ms)":  round(system_s * 1000),
            "% of Total": round(system_s / total_s * 100, 2),
            "Note":       "everything running inside this service",
        },
        {
            "Category":   "External API Wait (KIE model calls)",
            "Time (s)":   round(api_wait_s, 3),
            "Time (ms)":  round(api_wait_s * 1000),
            "% of Total": round(api_wait_s / total_s * 100, 2),
            "Note":       "blocked waiting for remote model",
        },
        {
            "Category":   "Total (wall clock)",
            "Time (s)":   round(total_s, 3),
            "Time (ms)":  round(total_s * 1000),
            "% of Total": 100.0,
            "Note":       "end-to-end",
        },
    ]
    return pd.DataFrame(rows)


def build_kie_df(timings: dict) -> pd.DataFrame | None:
    s1_ms = timings.get("stage1_model_ms")
    s2_ms = timings.get("stage2_model_ms")
    if s1_ms is None and s2_ms is None:
        return None

    rows = []
    if s1_ms is not None:
        rows.append({"Model Stage": "Stage 1 — Ghibli Generation",
                     "Time (ms)": s1_ms, "Time (s)": round(s1_ms / 1000, 3)})
    if s2_ms is not None:
        rows.append({"Model Stage": "Stage 2 — QR Composition",
                     "Time (ms)": s2_ms, "Time (s)": round(s2_ms / 1000, 3)})
    if s1_ms is not None and s2_ms is not None:
        rows.append({"Model Stage": "Combined",
                     "Time (ms)": s1_ms + s2_ms,
                     "Time (s)":  round((s1_ms + s2_ms) / 1000, 3)})
    return pd.DataFrame(rows)


# ─────────────────────────── renderer ───────────────────────────────────────

def render_report(timings: dict) -> None:
    pd.set_option("display.max_colwidth", None)
    pd.set_option("display.width", 160)
    pd.set_option("display.colheader_justify", "left")
    pd.set_option("display.float_format", "{:.3f}".format)

    total_s = timings.get("total") or 1

    # ── 1. Pipeline step table ───────────────────────────────────────────────
    print()
    _divider("═", 110)
    print("  GHIBLI QR PIPELINE  —  TIMING REPORT")
    _divider("═", 110)

    df = build_pipeline_df(timings)
    df_steps = df[df["Stage"] != "TOTAL"].copy().reset_index(drop=True)

    display_df = df_steps.drop(columns=["_substep"]).copy()
    display_df["Time (s)"]   = display_df["Time (s)"].map(lambda x: f"{x:.3f} s")
    display_df["Time (ms)"]  = display_df["Time (ms)"].map(lambda x: f"{x:,} ms")
    display_df["% of Total"] = display_df["% of Total"].map(lambda x: f"{x:.2f} %")

    print()
    print(display_df.to_string(index=True))
    print()

    _divider("─", 110)
    print(f"  TOTAL  │  {total_s:.3f} s  │  {round(total_s * 1000):,} ms  │  100.00 %")
    _divider("═", 110)

    # ── 2. Validation service breakdown ─────────────────────────────────────
    print()
    _divider("─", 110)
    print("  VALIDATION SERVICE  —  Per-layer timing breakdown")
    _divider("─", 110)
    print()

    val_keys = ["validation_url_check", "validation_download", "validation_face_detect", "validation"]
    val_rows = []
    for k in val_keys:
        val_s = timings.get(k, 0)
        label = STEP_LABELS.get(k, k)
        is_total = k == "validation"
        val_rows.append({
            "Layer":       ("► " + label.strip()) if is_total else label,
            "Description": STEP_DESCRIPTIONS.get(k, ""),
            "Time (s)":    f"{val_s:.3f} s",
            "Time (ms)":   f"{round(val_s * 1000):,} ms",
            "% of Total":  f"{val_s / total_s * 100:.2f} %",
        })
    val_df = pd.DataFrame(val_rows)
    print(val_df.to_string(index=False))

    # ── 3. Identity check breakdown ──────────────────────────────────────────
    print()
    _divider("─", 110)
    print("  IDENTITY CHECK SERVICE  —  Per-step timing breakdown")
    _divider("─", 110)
    print()

    id_keys = ["identity_src_download", "identity_face_detect", "identity_check"]
    id_rows = []
    for k in id_keys:
        id_s = timings.get(k, 0)
        label = STEP_LABELS.get(k, k)
        is_total = k == "identity_check"
        id_rows.append({
            "Step":        ("► " + label.strip()) if is_total else label,
            "Description": STEP_DESCRIPTIONS.get(k, ""),
            "Time (s)":    f"{id_s:.3f} s",
            "Time (ms)":   f"{round(id_s * 1000):,} ms",
            "% of Total":  f"{id_s / total_s * 100:.2f} %",
        })
    id_df = pd.DataFrame(id_rows)
    print(id_df.to_string(index=False))

    # ── 4. System vs external summary ────────────────────────────────────────
    print()
    _divider("─", 110)
    print("  SYSTEM vs EXTERNAL  —  Where is the time going?")
    _divider("─", 110)
    print()

    val_df2 = build_validation_df(timings)
    display_val = val_df2.copy()
    display_val["Time (s)"]   = display_val["Time (s)"].map(lambda x: f"{x:.3f} s")
    display_val["Time (ms)"]  = display_val["Time (ms)"].map(lambda x: f"{x:,} ms")
    display_val["% of Total"] = display_val["% of Total"].map(lambda x: f"{x:.2f} %")
    print(display_val.to_string(index=False))
    print()

    # ── 5. KIE model processing times ────────────────────────────────────────
    kie_df = build_kie_df(timings)
    if kie_df is not None:
        _divider("─", 110)
        print("  KIE API  —  Reported Model Processing Times (external service)")
        _divider("─", 110)
        print()
        display_kie = kie_df.copy()
        display_kie["Time (ms)"] = display_kie["Time (ms)"].map(lambda x: f"{x:,} ms")
        display_kie["Time (s)"]  = display_kie["Time (s)"].map(lambda x: f"{x:.3f} s")
        print(display_kie.to_string(index=False))
        print()

    _divider("═", 110)
    print()


# ─────────────────────────── entry point ────────────────────────────────────

def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        timings = load_timings(path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    render_report(timings)


if __name__ == "__main__":
    main()
