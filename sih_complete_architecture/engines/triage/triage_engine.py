"""Safety-oriented CDC NHAMCS ED triage prototype (2018--2022), offline only.

v6 -- FINAL TRIAGE ENGINE. Changes vs v5:

WHY THIS REWRITE
-----------------
v5 fixed the "fatal vitals -> GREEN" bug (missing-age guard bypass + numeric
values being nulled instead of clipped). Running the trained v5 pipeline
end-to-end exposed a second, different problem, visible directly in the
validation report:

    RED    precision 0.27  recall 0.85
    YELLOW precision 0.72  recall 0.21   <-- collapsed
    GREEN  precision 0.60  recall 0.58
    severe_undertriage_rate (true RED -> predicted GREEN): 9.0%

A single 3-class model, weighted hard toward RED to hit the recall target,
stops being able to tell YELLOW from GREEN (3,257 of 5,429 true-YELLOW cases
were pushed to RED), AND still missed 170 true-RED cases outright, because
physiology-only features can't see danger signals that live in the chief
complaint (e.g. "chest pain", "suicide attempt", "unconscious on arrival")
rather than the vitals.

WHAT CHANGED
------------
1. TWO-STAGE MODEL instead of one 3-class model:
     Stage 1 -- binary RED vs. NOT-RED classifier. Its probability threshold
       is tuned on 2021 validation to hit the prototype RED-recall target
       while maximizing precision (i.e. the highest threshold that still
       clears the recall bar, not the lowest).
     Stage 2 -- YELLOW vs. GREEN classifier, trained ONLY on rows that are
       truly non-RED. Because it never has to compete with an oversampled
       RED class, it can actually learn the YELLOW/GREEN boundary.
   At inference: if stage 1 says RED, the case is RED. Otherwise stage 2
   decides YELLOW vs GREEN. This is the fix for the YELLOW collapse.

2. CHIEF-COMPLAINT (RFV) RISK FLAGS, sourced from the NCHS "Reason for Visit
   Classification for Ambulatory Care" code list (see RFV_FAMILY_LABELS
   below; codes verified against CDC's published NHAMCS format file). Two
   new engineered features, built from the RAW RFV1-RFV5 codes (before the
   lossy 2-digit grouping used for the categorical features):
     - RFV_CRITICAL_FLAG: chief complaint itself is immediately
       life-threatening (e.g. cardiac arrest, DOA, unconscious on arrival,
       suicide attempt) -> safety guard forces RED, same tier as the
       critical-vital backstop.
     - RFV_URGENT_FLAG: chief complaint is a recognized high-risk pattern
       (e.g. chest pain, stroke-type neuro symptoms, GI bleeding, sepsis,
       overdose) -> safety guard blocks GREEN (minimum YELLOW), same tier
       as the NEWS2 "urgent" trigger.
   This directly targets the residual RED-missed-as-GREEN cases that a
   vitals-only guard cannot see (normal vitals + dangerous complaint).
   IMPORTANT: this RFV code list is a prototype heuristic built from the
   public NCHS classification, not a validated clinical decision list. It
   must be reviewed and extended by clinicians before real-world use.

3. Both new RFV flags are also fed into BOTH ML stages as ordinary features
   (not just the guard), so the models themselves can learn from them, not
   only the hard override.

4. Safety guard is now layered as, in this order (RED from ML is never
   downgraded by any layer):
     Layer 0: RFV_CRITICAL_FLAG               -> force RED
     Layer 1: critical_vital_backstop (v5)     -> force RED (age-independent)
     Layer 2: adult NEWS2 partial >= 7         -> force RED
     Layer 3: adult NEWS2 partial >=5 / single
              param==3 / RFV_URGENT_FLAG /
              inconsistent BP                  -> block GREEN (min YELLOW)

5. Reporting: the training run now prints stage-1-only recall/precision
   (RED vs not-RED, pre-guard) alongside the final post-guard 3-class
   report, so it's possible to see how much each layer (ML stage 1, ML
   stage 2, safety guard) is contributing.

Everything else (CDC-specific cleaning bounds during training, NEWS2 Scale-1
ranges, live-inference clipping instead of dropping, 2022 hold-out
reservation, unrecognized-input-key warnings) is unchanged from v5.

IMPORTANT: This is a research/demo decision-support model, not autonomous
clinical triage. All thresholds are engineering choices for this prototype,
not clinical standards, and must be reviewed by clinicians before any
real-world use. RED precision will remain modest by design -- the model is
deliberately biased to over-call RED rather than miss it -- so any interface
built on this should present RED as "needs urgent clinician review", not as
a confident diagnosis.
"""
from __future__ import annotations

import argparse
import itertools
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.utils.class_weight import compute_sample_weight

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "cdc_nhamcs_ed_stata"
OUT = ROOT / "artifacts"
YEARS = [2018, 2019, 2020, 2021, 2022]

RED, YELLOW, GREEN = 0, 1, 2
LABELS = [RED, YELLOW, GREEN]
LABEL_NAMES = ["RED", "YELLOW", "GREEN"]

MIN_RED_RECALL_TARGET = 0.85

NUMERIC_CANDIDATES = [
    "AGE", "TEMPF", "PULSE", "RESPR", "BPSYS", "BPDIAS", "POPCT", "PAINSCALE",
    "TOTCHRON",
]

PRETRIAGE_CATEGORICAL_CANDIDATES = [
    "SEX", "ARREMS", "AMBTRANSFER", "SEEN72", "EPISODE",
]

RFVS = [f"RFV{i}" for i in range(1, 6)]

CHRONIC_CANDIDATES = [
    "ARTHRTIS", "ASTHMA", "CANCER", "CEBD", "CEBVD", "CHF", "CKD", "COPD",
    "DEPRN", "DIABETES", "HYPERLIP", "HYPLIPID", "HYLIPID", "HTN", "IHD",
    "CAD", "OBESITY", "OSA", "OSTEOPOR", "OSTPRSIS", "SUBSTAB",
]

MISSING_TOKEN = "__MISSING__"
UNKNOWN_TOKEN = "__UNKNOWN__"

CRITICAL_VITAL_LIMITS = {
    "POPCT":  (70.0, None),
    "PULSE":  (20.0, 220.0),
    "BPSYS":  (50.0, None),
    "RESPR":  (4.0, 60.0),
    "TEMPF":  (86.0, 107.0),
}

# ---------------------------------------------------------------------------
# RFV (Reason for Visit) chief-complaint risk families.
#
# Codes are the 4-digit "RFV3DF" family codes from the NCHS Reason for Visit
# Classification for Ambulatory Care, as published in CDC's NHAMCS SAS
# format documentation (e.g. ftp.cdc.gov/.../nhamcs/sas/opd01for.TXT). NHAMCS
# RFV1-RFV5 fields may store either the 4-digit family code or a 5-digit
# code with one extra specificity digit (family*10 + subcode); both are
# normalized to the 4-digit family below via `_rfv_family_code`.
#
# This list is a PROTOTYPE HEURISTIC covering commonly-taught high-risk
# presentations. It is intentionally not exhaustive (e.g. it does not cover
# every possible dangerous complaint) and must be reviewed/extended by
# clinicians before real-world use.
# ---------------------------------------------------------------------------
RFV_FAMILY_LABELS = {
    # --- CRITICAL: chief complaint alone is immediately life-threatening ---
    5835: "Dead on arrival (DOA)",
    5836: "Respiratory arrest",
    5837: "Cardiac arrest",
    5838: "Drowning",
    5839: "Cardiopulmonary arrest",
    5840: "Unconscious on arrival",
    5842: "Altered level of consciousness",
    5820: "Suicide attempt",
    5818: "Intentional self-mutilation",
    4401: "Cardiopulmonary resuscitation",
    # --- URGENT: recognized high-risk pattern, needs prompt evaluation ---
    1050: "Chest pain and related symptoms",
    1030: "Fainting (syncope)",
    1260: "Abnormal pulsations and palpitations",
    1265: "Heart pain",
    1270: "Other symptoms of the heart",
    1415: "Shortness of breath",
    1420: "Labored or difficult breathing (dyspnea)",
    1430: "Breathing problems, NEC",
    1200: "Abnormal involuntary movements",
    1205: "Convulsions",
    1230: "Weakness (neurologic)",
    1235: "Disorders of speech, speech disturbance",
    1580: "Gastrointestinal bleeding",
    1070: "Bleeding, site unspecified",
    2031: "Sepsis, septicemia",
    2525: "Cerebrovascular disease",
    5900: "Accidental poisoning",
    5905: "Adverse effect of medication",
    5910: "Adverse effect of drug abuse",
    5915: "Adverse effect of alcohol",
    5841: "State of consciousness not specified",
    5815: "Violence, NOS",
    5830: "Rape",
}

RFV_CRITICAL_FAMILIES = {
    5835, 5836, 5837, 5838, 5839, 5840, 5842, 5820, 5818, 4401,
}
RFV_URGENT_FAMILIES = set(RFV_FAMILY_LABELS) - RFV_CRITICAL_FAMILIES


def verify_local_data() -> None:
    missing = [year for year in YEARS if not list((DATA / str(year)).rglob("*.dta"))]
    if missing:
        years_str = ", ".join(str(y) for y in missing)
        raise FileNotFoundError(
            f"Missing local NHAMCS ED .dta files for year(s): {years_str}.\n"
            f"Place extracted CDC Stata files under:\n  {DATA}/<year>/*.dta"
        )
    print("Offline data check passed: found local .dta files for", ", ".join(map(str, YEARS)))


def find_dta(year: int) -> Path:
    files = list((DATA / str(year)).rglob("*.dta"))
    if len(files) != 1:
        raise RuntimeError(f"Expected exactly one .dta for {year}, found: {files}")
    return files[0]


def normalize_column_names(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(c).upper() for c in frame.columns]
    return frame


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def clean_target(series: pd.Series) -> pd.Series:
    values = numeric(series)
    return values.where(values.isin([1, 2, 3, 4, 5]), np.nan)


_LIVE_CLIP_BOUNDS = {
    "AGE": (0, 110),
    "TEMPF": (70.0, 115.0),
    "PULSE": (0, 300),
    "RESPR": (0, 80),
    "BPSYS": (0, 300),
    "BPDIAS": (0, 250),
    "POPCT": (0, 100),
    "PAINSCALE": (0, 10),
    "TOTCHRON": (0, 30),
}


def clean_numeric_feature(name: str, series: pd.Series, for_training: bool = True) -> pd.Series:
    """See v5 docstring for the for_training=True/False distinction."""
    v = numeric(series).mask(lambda x: x < 0, np.nan)

    if not for_training:
        bounds = _LIVE_CLIP_BOUNDS.get(name)
        if name == "TEMPF":
            v = v.where(v < 200, v / 10.0)
        if bounds is not None:
            lo, hi = bounds
            v = v.clip(lower=lo, upper=hi)
        return v

    if name == "AGE":
        return v.where(v.between(0, 110))
    if name == "TEMPF":
        v = v.where(v < 200, v / 10.0)
        return v.where(v.between(80.0, 110.0))
    if name == "PULSE":
        v = v.mask(v.eq(998), np.nan)
        return v.where(v.between(0, 250))
    if name == "RESPR":
        return v.where(v.between(0, 160))
    if name == "BPSYS":
        return v.where(v.between(0, 300))
    if name == "BPDIAS":
        v = v.mask(v.eq(998), np.nan)
        return v.where(v.between(0, 200))
    if name == "POPCT":
        return v.where(v.between(0, 100))
    if name == "PAINSCALE":
        return v.where(v.between(0, 10))
    if name == "TOTCHRON":
        return v.where(v.between(0, 30))
    return v


def clean_allowed_categorical(series: pd.Series, allowed: set[int] | None = None) -> pd.Series:
    v = numeric(series).mask(lambda x: x < 0, np.nan)
    if allowed is not None:
        v = v.where(v.isin(allowed), np.nan)
    return v.astype("Int64").astype("string")


def clean_rfv_grouped(series: pd.Series) -> pd.Series:
    """Broad 2-digit RFV prefix, used as a categorical feature (as in v4/v5)."""
    v = numeric(series).mask(lambda x: x < 0, np.nan)

    def group_code(value):
        if pd.isna(value):
            return pd.NA
        code = str(int(round(float(value))))
        if not code or code.startswith("9"):
            return pd.NA
        return code[:2]

    return v.map(group_code).astype("string")


def _rfv_family_code(value) -> int | None:
    """Normalize a raw RFV code to its 4-digit family code (see RFV_FAMILY_LABELS)."""
    if pd.isna(value):
        return None
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if v <= 0 or v >= 90000:
        return None
    if v >= 10000:
        v = v // 10  # 5-digit code -> 4-digit family (drop the specificity digit)
    return v


def compute_rfv_risk_flags(frame: pd.DataFrame, rfv_cols: list[str]) -> tuple[pd.DataFrame, list[list[str]]]:
    """Build RFV_CRITICAL_FLAG / RFV_URGENT_FLAG from RAW (ungrouped) RFV1-5 codes.

    Must be called on the raw RFV values, before clean_rfv_grouped() collapses
    them to a lossy 2-digit prefix -- the risk families need the full 4-digit
    code to distinguish e.g. "chest pain" (1050) from "fainting" (1030), both
    of which share the 2-digit prefix "10".

    Returns (flags_dataframe, per_row_matched_reasons).
    """
    n = len(frame)
    critical = np.zeros(n, dtype=bool)
    urgent = np.zeros(n, dtype=bool)
    reasons: list[list[str]] = [[] for _ in range(n)]

    available = [c for c in rfv_cols if c in frame]
    for col in available:
        codes = frame[col].map(_rfv_family_code)
        for i, fam in enumerate(codes.to_numpy()):
            if fam is None:
                continue
            if fam in RFV_CRITICAL_FAMILIES:
                critical[i] = True
                reasons[i].append(f"{col}: {RFV_FAMILY_LABELS[fam]} (critical chief complaint)")
            elif fam in RFV_URGENT_FAMILIES:
                urgent[i] = True
                reasons[i].append(f"{col}: {RFV_FAMILY_LABELS[fam]} (urgent chief complaint)")

    flags = pd.DataFrame(
        {
            "RFV_CRITICAL_FLAG": critical.astype(float),
            "RFV_URGENT_FLAG": urgent.astype(float),
        },
        index=frame.index,
    )
    return flags, reasons


def category_allowed_values(name: str) -> set[int] | None:
    if name == "SEX":
        return {1, 2}
    if name in {"ARREMS", "AMBTRANSFER", "SEEN72", "EPISODE"}:
        return {1, 2}
    return None


def map_triage_target(immedr: pd.Series) -> pd.Series:
    out = pd.Series(index=immedr.index, dtype="int8")
    out.loc[immedr.isin([1, 2])] = RED
    out.loc[immedr.eq(3)] = YELLOW
    out.loc[immedr.isin([4, 5])] = GREEN
    return out.astype("int8")


NEWS2_ADULT_MIN_AGE = 16


def _news2_component_score(series: pd.Series, component: str) -> pd.Series:
    v = numeric(series)
    score = pd.Series(np.nan, index=v.index, dtype=float)
    present = v.notna()

    if component == "RESPR":
        score.loc[present & (v <= 8)] = 3
        score.loc[present & v.between(9, 11)] = 1
        score.loc[present & v.between(12, 20)] = 0
        score.loc[present & v.between(21, 24)] = 2
        score.loc[present & (v >= 25)] = 3
    elif component == "POPCT":
        score.loc[present & (v <= 91)] = 3
        score.loc[present & v.between(92, 93)] = 2
        score.loc[present & v.between(94, 95)] = 1
        score.loc[present & (v >= 96)] = 0
    elif component == "BPSYS":
        score.loc[present & (v <= 90)] = 3
        score.loc[present & v.between(91, 100)] = 2
        score.loc[present & v.between(101, 110)] = 1
        score.loc[present & v.between(111, 219)] = 0
        score.loc[present & (v >= 220)] = 3
    elif component == "PULSE":
        score.loc[present & (v <= 40)] = 3
        score.loc[present & v.between(41, 50)] = 1
        score.loc[present & v.between(51, 90)] = 0
        score.loc[present & v.between(91, 110)] = 1
        score.loc[present & v.between(111, 130)] = 2
        score.loc[present & (v >= 131)] = 3
    elif component == "TEMPF":
        c = (v - 32.0) * 5.0 / 9.0
        score.loc[present & (c <= 35.0)] = 3
        score.loc[present & (c > 35.0) & (c <= 36.0)] = 1
        score.loc[present & (c > 36.0) & (c <= 38.0)] = 0
        score.loc[present & (c > 38.0) & (c <= 39.0)] = 1
        score.loc[present & (c > 39.0)] = 2
    else:
        raise ValueError(f"Unsupported NEWS2 component: {component}")

    return score


def add_news2_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "AGE" in out:
        age_known = out["AGE"].notna()
        adult = age_known & out["AGE"].ge(NEWS2_ADULT_MIN_AGE)
    else:
        age_known = pd.Series(False, index=out.index)
        adult = pd.Series(False, index=out.index)
    out["_AGE_KNOWN"] = age_known
    out["_ADULT"] = adult

    component_scores = {}
    for component in ["RESPR", "POPCT", "BPSYS", "PULSE", "TEMPF"]:
        if component not in out:
            continue
        s = _news2_component_score(out[component], component)
        s = s.where(adult, np.nan)
        if component == "BPSYS" and "BP_INCONSISTENT" in out:
            s = s.where(out["BP_INCONSISTENT"].ne(1.0), np.nan)
        component_scores[component] = s
        out[f"NEWS2_{component}_SCORE"] = s

    if component_scores:
        score_frame = pd.DataFrame(component_scores, index=out.index)
        out["NEWS2_PARTIAL_SCORE"] = score_frame.sum(axis=1, min_count=1)
        out["NEWS2_RED_PARAMETER_COUNT"] = score_frame.eq(3).sum(axis=1).astype(float)
        out["NEWS2_COMPONENTS_MEASURED"] = score_frame.notna().sum(axis=1).astype(float)
    else:
        out["NEWS2_PARTIAL_SCORE"] = np.nan
        out["NEWS2_RED_PARAMETER_COUNT"] = 0.0
        out["NEWS2_COMPONENTS_MEASURED"] = 0.0

    return out


def critical_vital_backstop(frame: pd.DataFrame) -> tuple[np.ndarray, list[list[str]]]:
    index = frame.index
    force = np.zeros(len(index), dtype=bool)
    reasons: list[list[str]] = [[] for _ in range(len(index))]

    for name, (low, high) in CRITICAL_VITAL_LIMITS.items():
        if name not in frame:
            continue
        v = numeric(frame[name])
        if low is not None:
            hit = v.le(low).to_numpy()
            if hit.any():
                force |= hit
                for i in np.where(hit)[0]:
                    reasons[i].append(f"{name}={v.iloc[i]:g} is at/below critical floor {low:g}")
        if high is not None:
            hit = v.ge(high).to_numpy()
            if hit.any():
                force |= hit
                for i in np.where(hit)[0]:
                    reasons[i].append(f"{name}={v.iloc[i]:g} is at/above critical ceiling {high:g}")

    return force, reasons


def apply_safety_guard(frame: pd.DataFrame, predicted: np.ndarray) -> tuple[np.ndarray, dict, list[list[str]]]:
    """Layered, auditable safety guard. RED from the ML pipeline is never downgraded.

    Layer 0: RFV_CRITICAL_FLAG (chief complaint itself life-threatening) -> force RED
    Layer 1: critical_vital_backstop (age-independent vitals)            -> force RED
    Layer 2: adult NEWS2 partial >= 7                                    -> force RED
    Layer 3: adult NEWS2 partial >=5 / single param==3 / RFV_URGENT_FLAG /
             inconsistent BP                                             -> block GREEN
    """
    final = np.asarray(predicted, dtype=np.int8).copy()
    index = frame.index

    # Layer 0: RFV critical chief complaint.
    rfv_critical = frame.get("RFV_CRITICAL_FLAG", pd.Series(0.0, index=index)).eq(1.0).to_numpy()
    forced_red_rfv = rfv_critical & (final != RED)
    final[rfv_critical] = RED

    # Layer 1: age-independent vital backstop.
    backstop_red, backstop_reasons = critical_vital_backstop(frame)
    forced_red_backstop = backstop_red & (final != RED)
    final[backstop_red] = RED

    # Layer 2 & 3: adult NEWS2 guard + RFV urgent + BP inconsistency.
    adult = frame.get("_ADULT", pd.Series(False, index=index)).to_numpy()
    partial = frame.get("NEWS2_PARTIAL_SCORE", pd.Series(np.nan, index=index))
    red_param_count = frame.get("NEWS2_RED_PARAMETER_COUNT", pd.Series(0.0, index=index))
    bp_bad = frame.get("BP_INCONSISTENT", pd.Series(0.0, index=index)).eq(1.0)
    rfv_urgent = frame.get("RFV_URGENT_FLAG", pd.Series(0.0, index=index)).eq(1.0)

    high = (adult & partial.ge(7).to_numpy())
    urgent_news2 = (adult & (partial.ge(5) | red_param_count.ge(1)).to_numpy())

    forced_red_news2 = high & (final != RED)
    final[high] = RED

    block_green = (urgent_news2 | bp_bad.to_numpy() | rfv_urgent.to_numpy()) & (final == GREEN)
    final[block_green] = YELLOW

    info = {
        "forced_red_rfv_critical_count": int(forced_red_rfv.sum()),
        "forced_red_critical_vital_backstop_count": int(forced_red_backstop.sum()),
        "forced_red_news2_count": int(forced_red_news2.sum()),
        "green_blocked_to_yellow_count": int(block_green.sum()),
        "adult_news2_high_count": int(high.sum()),
        "adult_news2_urgent_count": int(urgent_news2.sum()),
        "bp_inconsistent_count": int(bp_bad.sum()),
        "rfv_critical_count": int(rfv_critical.sum()),
        "rfv_urgent_count": int(rfv_urgent.sum()),
        "age_unknown_count": int((~frame.get("_AGE_KNOWN", pd.Series(True, index=index))).sum()),
    }
    return final, info, backstop_reasons


def add_engineered_features(
    frame: pd.DataFrame,
    chronic_cols: list[str],
    rfv_cols: list[str],
    raw_rfv_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """raw_rfv_frame: the UNGROUPED RFV1-5 values (before clean_rfv_grouped), used
    only to compute RFV_CRITICAL_FLAG/RFV_URGENT_FLAG. If omitted, the flags default
    to 0 (e.g. when raw codes are unavailable)."""
    out = frame.copy()

    available_vitals = [c for c in ["TEMPF", "PULSE", "RESPR", "BPSYS", "BPDIAS", "POPCT"] if c in out]
    if available_vitals:
        out["VITALS_MISSING_COUNT"] = out[available_vitals].isna().sum(axis=1).astype(float)

    if rfv_cols:
        out["RFV_COUNT"] = out[rfv_cols].notna().sum(axis=1).astype(float)

    if "BPSYS" in out and "BPDIAS" in out:
        both_bp = out["BPSYS"].notna() & out["BPDIAS"].notna()
        bp_consistent = both_bp & out["BPSYS"].gt(out["BPDIAS"])
        out["BP_INCONSISTENT"] = (both_bp & ~bp_consistent).astype(float)
        out["PULSE_PRESSURE"] = (out["BPSYS"] - out["BPDIAS"]).where(bp_consistent)
        out["MAP_APPROX"] = ((out["BPSYS"] + 2.0 * out["BPDIAS"]) / 3.0).where(bp_consistent)
    else:
        out["BP_INCONSISTENT"] = 0.0

    if "PULSE" in out and "BPSYS" in out:
        valid_sys = out["BPSYS"].gt(0)
        if "BP_INCONSISTENT" in out:
            valid_sys &= out["BP_INCONSISTENT"].ne(1.0)
        denom = out["BPSYS"].where(valid_sys)
        out["SHOCK_INDEX"] = (out["PULSE"] / denom).clip(lower=0, upper=10)

    if chronic_cols:
        out["CHRONIC_POSITIVE_COUNT"] = sum(
            # Live web inputs may leave a condition blank.  Treat that as
            # "not recorded" for the count rather than attempting to cast a
            # nullable boolean to int (which raises on recent pandas).
            out[c].eq("1").fillna(False).astype(int) for c in chronic_cols
        ).astype(float)

    if raw_rfv_frame is not None and rfv_cols:
        flags, _ = compute_rfv_risk_flags(raw_rfv_frame, rfv_cols)
        out["RFV_CRITICAL_FLAG"] = flags["RFV_CRITICAL_FLAG"].to_numpy()
        out["RFV_URGENT_FLAG"] = flags["RFV_URGENT_FLAG"].to_numpy()
    else:
        out["RFV_CRITICAL_FLAG"] = 0.0
        out["RFV_URGENT_FLAG"] = 0.0

    out = add_news2_features(out)
    return out


def fit_category_maps(frame: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, int]]:
    maps: dict[str, dict[str, int]] = {}
    for col in columns:
        values = sorted(str(v) for v in frame[col].dropna().unique().tolist())
        mapping = {v: i for i, v in enumerate(values)}
        mapping[MISSING_TOKEN] = len(mapping)
        mapping[UNKNOWN_TOKEN] = len(mapping) + 1
        maps[col] = mapping
    return maps


def transform_categories(frame: pd.DataFrame, maps: dict[str, dict[str, int]]) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for col, mapping in maps.items():
        miss = mapping[MISSING_TOKEN]
        unk = mapping[UNKNOWN_TOKEN]

        def encode(v):
            if pd.isna(v):
                return miss
            return mapping.get(str(v), unk)

        out[col] = frame[col].map(encode).astype("int32")
    return out


def build_matrix(
    frame: pd.DataFrame,
    numeric_features: list[str],
    category_maps: dict[str, dict[str, int]],
) -> pd.DataFrame:
    num = frame[numeric_features].astype(float)
    cat = transform_categories(frame, category_maps)
    return pd.concat([num, cat], axis=1)


def two_stage_predict(
    stage1_model,
    stage2_model,
    X: pd.DataFrame,
    threshold: float,
) -> np.ndarray:
    """Combine stage1 (RED vs not-RED) + stage2 (YELLOW vs GREEN) into 3-class preds."""
    n = len(X)
    final = np.empty(n, dtype=np.int8)

    stage1_proba = stage1_model.predict_proba(X)
    class_to_col = {int(c): i for i, c in enumerate(stage1_model.classes_)}
    red_col = class_to_col[1]  # stage1 classes are {0: not-RED, 1: RED}
    is_red = stage1_proba[:, red_col] >= threshold
    final[is_red] = RED

    remaining_idx = np.where(~is_red)[0]
    if len(remaining_idx):
        X_remaining = X.iloc[remaining_idx]
        stage2_proba = stage2_model.predict_proba(X_remaining)
        s2_class_to_col = {int(c): i for i, c in enumerate(stage2_model.classes_)}
        yellow_col = s2_class_to_col[YELLOW]
        green_col = s2_class_to_col[GREEN]
        stage2_pred = np.where(
            stage2_proba[:, yellow_col] >= stage2_proba[:, green_col], YELLOW, GREEN
        )
        final[remaining_idx] = stage2_pred

    return final


def calculate_metrics(y_true: np.ndarray, predicted: np.ndarray) -> dict:
    cm = confusion_matrix(y_true, predicted, labels=LABELS)
    red_total = max(1, int(cm[RED, :].sum()))
    red_pred_total = max(1, int(cm[:, RED].sum()))

    return {
        "confusion_matrix": cm.tolist(),
        "macro_f1": float(f1_score(y_true, predicted, labels=LABELS, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, predicted, labels=LABELS, average="weighted", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, predicted)),
        "red_recall": float(cm[RED, RED] / red_total),
        "red_precision": float(cm[RED, RED] / red_pred_total),
        "yellow_recall": float(recall_score(y_true, predicted, labels=[YELLOW], average=None, zero_division=0)[0]),
        "green_recall": float(recall_score(y_true, predicted, labels=[GREEN], average=None, zero_division=0)[0]),
        "red_to_yellow_count": int(cm[RED, YELLOW]),
        "red_to_green_count": int(cm[RED, GREEN]),
        "severe_undertriage_rate": float(cm[RED, GREEN] / red_total),
    }


INPUT_ALIASES = {
    "AGE": "AGE",
    "TEMPERATURE": "TEMPF", "TEMPERATURE_F": "TEMPF", "TEMP": "TEMPF", "TEMPF": "TEMPF",
    "PULSE": "PULSE", "HEART_RATE": "PULSE", "HR": "PULSE",
    "RESPIRATORY_RATE": "RESPR", "RESP_RATE": "RESPR", "RR": "RESPR", "RESPR": "RESPR",
    "SYSTOLIC_BP": "BPSYS", "SBP": "BPSYS", "BPSYS": "BPSYS",
    "DIASTOLIC_BP": "BPDIAS", "DBP": "BPDIAS", "BPDIAS": "BPDIAS",
    "O2_SATURATION": "POPCT", "OXYGEN_SATURATION": "POPCT", "SPO2": "POPCT", "POPCT": "POPCT",
    "PAIN": "PAINSCALE", "PAIN_SCALE": "PAINSCALE", "PAINSCALE": "PAINSCALE",
    "TOTCHRON": "TOTCHRON", "CHRONIC_CONDITIONS_COUNT": "TOTCHRON",
    "ASTHMA": "ASTHMA", "SEX": "SEX", "ARREMS": "ARREMS", "AMBTRANSFER": "AMBTRANSFER",
    "SEEN72": "SEEN72", "EPISODE": "EPISODE",
    "RFV1": "RFV1", "RFV2": "RFV2", "RFV3": "RFV3", "RFV4": "RFV4", "RFV5": "RFV5",
    "ARTHRTIS": "ARTHRTIS", "CANCER": "CANCER", "CEBD": "CEBD", "CEBVD": "CEBVD",
    "CHF": "CHF", "CKD": "CKD", "COPD": "COPD", "DEPRN": "DEPRN",
    "DIABETES": "DIABETES", "HYPERLIP": "HYPERLIP", "HYPLIPID": "HYPLIPID",
    "HYLIPID": "HYLIPID", "HTN": "HTN", "IHD": "IHD", "CAD": "CAD",
    "OBESITY": "OBESITY", "OSA": "OSA", "OSTEOPOR": "OSTEOPOR",
    "OSTPRSIS": "OSTPRSIS", "SUBSTAB": "SUBSTAB",
    "CHIEF_COMPLAINT_CODE": "RFV1", "REASON_FOR_VISIT": "RFV1", "REASON_FOR_VISIT_1": "RFV1",
}

_KNOWN_TARGET_FIELDS = set(INPUT_ALIASES.values())


def _canonicalize_patient_input(patient: dict) -> tuple[dict, list[str]]:
    canonical = {}
    unrecognized = []
    for key, value in patient.items():
        normalized = str(key).strip().upper().replace(" ", "_").replace("-", "_")
        if normalized in INPUT_ALIASES:
            target = INPUT_ALIASES[normalized]
        elif normalized in _KNOWN_TARGET_FIELDS:
            target = normalized
        else:
            target = normalized
            unrecognized.append(key)
        if value is not None and value != "":
            canonical[target] = value
    return canonical, unrecognized


def _prepare_single_patient(patient: dict, bundle: dict) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    values, unrecognized = _canonicalize_patient_input(patient)
    frame = pd.DataFrame([values])

    raw_numeric = bundle.get("raw_numeric_features", [c for c in NUMERIC_CANDIDATES if c in frame.columns])
    pretriage = bundle.get("pretriage_categorical", [])
    rfv_cols = bundle.get("rfv_cols", [])
    chronic_cols = bundle.get("chronic_cols", [])

    cleaned = pd.DataFrame(index=frame.index)
    for col in raw_numeric:
        source = frame[col] if col in frame else pd.Series([np.nan], index=frame.index)
        cleaned[col] = clean_numeric_feature(col, source, for_training=False)

    for col in pretriage:
        source = frame[col] if col in frame else pd.Series([np.nan], index=frame.index)
        cleaned[col] = clean_allowed_categorical(source, category_allowed_values(col))

    raw_rfv_frame = pd.DataFrame(index=frame.index)
    for col in rfv_cols:
        source = frame[col] if col in frame else pd.Series([np.nan], index=frame.index)
        raw_rfv_frame[col] = numeric(source)
        cleaned[col] = clean_rfv_grouped(source)

    for col in chronic_cols:
        source = frame[col] if col in frame else pd.Series([np.nan], index=frame.index)
        cleaned[col] = clean_allowed_categorical(source, {0, 1})

    cleaned = add_engineered_features(cleaned, chronic_cols, rfv_cols, raw_rfv_frame=raw_rfv_frame)

    for col in bundle["numeric_features"] + bundle["categorical_features"]:
        if col not in cleaned:
            cleaned[col] = np.nan

    X = build_matrix(cleaned, bundle["numeric_features"], bundle["category_maps"])
    return cleaned, X, unrecognized


def predict_patient(patient: dict, bundle_path: str | Path | None = None) -> dict:
    if bundle_path is None:
        bundle_path = OUT / "triage_safety_pipeline_v6.pkl"
    bundle_path = Path(bundle_path)

    with open(bundle_path, "rb") as f:
        bundle = pickle.load(f)

    cleaned, X, unrecognized_keys = _prepare_single_patient(patient, bundle)
    raw_pred = two_stage_predict(
        bundle["stage1_model"], bundle["stage2_model"], X, float(bundle["stage1_threshold"])
    )
    final_pred, guard_info, backstop_reasons = apply_safety_guard(cleaned, raw_pred)

    stage1_proba = bundle["stage1_model"].predict_proba(X)
    s1_class_to_col = {int(c): i for i, c in enumerate(bundle["stage1_model"].classes_)}
    red_probability = float(stage1_proba[0, s1_class_to_col[1]])

    # Always expose Stage 2's two probabilities, even when Stage 1 selected
    # RED.  The UI can then show why a non-RED case is YELLOW versus GREEN;
    # these values are model scores, not calibrated clinical risk estimates.
    stage2_proba = bundle["stage2_model"].predict_proba(X)
    s2_class_to_col = {int(c): i for i, c in enumerate(bundle["stage2_model"].classes_)}
    yellow_probability = float(stage2_proba[0, s2_class_to_col[YELLOW]])
    green_probability = float(stage2_proba[0, s2_class_to_col[GREEN]])

    # Requested prototype display rule.  These are raw, two-stage model scores:
    # RED is stage 1, while YELLOW/GREEN are stage 2.  They are not a single
    # calibrated three-class probability distribution, therefore this value is
    # deliberately returned as a *demo rule* and never replaces final_class.
    display_scores = {
        "RED": red_probability * 100,
        "YELLOW": yellow_probability * 100,
        "GREEN": green_probability * 100,
    }
    score_values = list(display_scores.values())
    scores_between_36_and_60 = sum(36 <= score <= 60 for score in score_values)
    red_highest = display_scores["RED"] == max(score_values)
    yellow_highest = display_scores["YELLOW"] == max(score_values)
    if red_highest or (display_scores["RED"] >= 70 and display_scores["YELLOW"] >= 70):
        custom_demo_class = "RED"
        custom_demo_reason = "RED has the highest score, or both RED and YELLOW are at least 70%."
    elif scores_between_36_and_60 >= 2:
        custom_demo_class = "GREEN"
        custom_demo_reason = "At least two raw model scores are between 36% and 60%."
    elif yellow_highest or (
        display_scores["YELLOW"] >= 60
        and display_scores["RED"] >= 60
        and display_scores["GREEN"] <= 40
    ):
        custom_demo_class = "YELLOW"
        custom_demo_reason = "YELLOW has the highest score, or the requested YELLOW threshold condition matched."
    else:
        custom_demo_class = "YELLOW"
        custom_demo_reason = "Default YELLOW rule."

    row = cleaned.iloc[0]
    label_mapping = {RED: "RED", YELLOW: "YELLOW", GREEN: "GREEN"}
    raw_name = label_mapping[int(raw_pred[0])]
    final_name = label_mapping[int(final_pred[0])]

    reasons = list(backstop_reasons[0])

    rfv_cols = bundle.get("rfv_cols", [])
    if rfv_cols:
        raw_rfv_frame = pd.DataFrame(
            {col: [numeric(pd.Series([patient.get(col.lower(), patient.get(col))])).iloc[0]] for col in rfv_cols}
        )
        _, rfv_reasons = compute_rfv_risk_flags(raw_rfv_frame, rfv_cols)
        reasons.extend(rfv_reasons[0])

    age = row.get("AGE", np.nan)
    age_known = bool(row.get("_AGE_KNOWN", False))
    partial = row.get("NEWS2_PARTIAL_SCORE", np.nan)
    red_count = row.get("NEWS2_RED_PARAMETER_COUNT", 0.0)
    bp_bad = row.get("BP_INCONSISTENT", 0.0) == 1.0

    if not age_known:
        reasons.append(
            "AGE was missing or blank: adult NEWS2 scoring could NOT run. "
            "The age-independent critical-vital backstop and RFV chief-complaint "
            "flags still ran, but treat this prediction as less reliable and supply age."
        )
    elif age < NEWS2_ADULT_MIN_AGE:
        reasons.append(
            f"Patient age {age:g} is under {NEWS2_ADULT_MIN_AGE}: adult NEWS2 scoring "
            "does not apply. Use a pediatric-specific triage pathway."
        )
    else:
        if pd.notna(partial) and partial >= 7:
            reasons.append(f"adult partial NEWS2={partial:.0f} >= 7: project RED safety override")
        elif (pd.notna(partial) and partial >= 5) or red_count >= 1:
            reasons.append("adult NEWS2 urgent trigger: GREEN is not permitted")

    if bp_bad:
        reasons.append("systolic BP <= diastolic BP: measurement is internally inconsistent; re-measure BP")

    if unrecognized_keys:
        reasons.append(
            f"WARNING: these input keys were not recognized and were NOT used: {unrecognized_keys}."
        )

    return {
        "final_class": final_name,
        "ml_class_before_safety_guard": raw_name,
        "stage1_red_probability": red_probability,
        "stage1_red_threshold": float(bundle["stage1_threshold"]),
        "stage2_yellow_probability": yellow_probability,
        "stage2_green_probability": green_probability,
        "custom_demo_rule_class": custom_demo_class,
        "custom_demo_rule_reason": custom_demo_reason,
        "custom_demo_rule_scores": {name: round(score, 2) for name, score in display_scores.items()},
        "safety_guard_changed_prediction": bool(final_pred[0] != raw_pred[0]),
        "safety_reasons": reasons,
        "safety_guard_summary": guard_info,
        "unrecognized_input_keys": unrecognized_keys,
        "derived": {
            "age_known": age_known,
            "bp_inconsistent": bool(bp_bad),
            "pulse_pressure": None if pd.isna(row.get("PULSE_PRESSURE", np.nan)) else float(row["PULSE_PRESSURE"]),
            "map_approx": None if pd.isna(row.get("MAP_APPROX", np.nan)) else float(row["MAP_APPROX"]),
            "shock_index": None if pd.isna(row.get("SHOCK_INDEX", np.nan)) else float(row["SHOCK_INDEX"]),
            "news2_partial_score": None if pd.isna(partial) else float(partial),
            "news2_red_parameter_count": int(red_count),
            "news2_components_measured": int(row.get("NEWS2_COMPONENTS_MEASURED", 0.0)),
            "rfv_critical_flag": bool(row.get("RFV_CRITICAL_FLAG", 0.0) == 1.0),
            "rfv_urgent_flag": bool(row.get("RFV_URGENT_FLAG", 0.0) == 1.0),
        },
        "warning": (
            "Research/demo decision support only, not a validated clinical tool. "
            "RED precision is intentionally traded off for RED recall -- treat every "
            "RED as 'needs urgent clinician review', not as a confirmed diagnosis. "
            "NEWS2 and the RFV risk list are prototype heuristics and require "
            "clinician review before any real-world use. Probability fields are "
            "model scores, not calibrated clinical risk estimates. The custom "
            "demo rule is an experimental display only and does not replace Final triage."
        ),
    }


def main() -> None:
    verify_local_data()

    raw: dict[int, pd.DataFrame] = {}
    schemas: dict[int, list[str]] = {}
    for year in YEARS:
        df = normalize_column_names(pd.read_stata(find_dta(year), convert_categoricals=False))
        raw[year] = df
        schemas[year] = sorted(df.columns.tolist())
        print(f"{year}: raw rows={len(df):,}, columns={len(df.columns)}")

    common = set.intersection(*(set(raw[y].columns) for y in YEARS))
    if "IMMEDR" not in common:
        raise RuntimeError("IMMEDR is not consistently available across all five years.")

    raw_numeric = [c for c in NUMERIC_CANDIDATES if c in common]
    pretriage_categorical = [c for c in PRETRIAGE_CATEGORICAL_CANDIDATES if c in common]
    rfv_cols = [c for c in RFVS if c in common]
    chronic_cols = []
    for c in CHRONIC_CANDIDATES:
        if c in common and c not in chronic_cols:
            chronic_cols.append(c)

    cleaned_years = []
    for year, df in raw.items():
        target = clean_target(df["IMMEDR"])
        out = pd.DataFrame({"YEAR": year, "IMMEDR": target})

        for col in raw_numeric:
            out[col] = clean_numeric_feature(col, df[col], for_training=True)
        for col in pretriage_categorical:
            out[col] = clean_allowed_categorical(df[col], category_allowed_values(col))

        raw_rfv_frame = pd.DataFrame(index=df.index)
        for col in rfv_cols:
            raw_rfv_frame[col] = numeric(df[col])
            out[col] = clean_rfv_grouped(df[col])

        for col in chronic_cols:
            out[col] = clean_allowed_categorical(df[col], {0, 1})

        out = out[out["IMMEDR"].notna()].copy()
        out["IMMEDR"] = out["IMMEDR"].astype(int)
        out["TRIAGE_CLASS"] = map_triage_target(out["IMMEDR"])
        out = add_engineered_features(
            out, chronic_cols, rfv_cols, raw_rfv_frame=raw_rfv_frame.loc[out.index]
        )
        cleaned_years.append(out)

    cohort = pd.concat(cleaned_years, ignore_index=True)

    engineered_numeric = [
        c for c in [
            "VITALS_MISSING_COUNT", "RFV_COUNT", "BP_INCONSISTENT",
            "PULSE_PRESSURE", "MAP_APPROX", "SHOCK_INDEX",
            "CHRONIC_POSITIVE_COUNT", "NEWS2_PARTIAL_SCORE",
            "NEWS2_RED_PARAMETER_COUNT", "NEWS2_COMPONENTS_MEASURED",
            "NEWS2_RESPR_SCORE", "NEWS2_POPCT_SCORE", "NEWS2_BPSYS_SCORE",
            "NEWS2_PULSE_SCORE", "NEWS2_TEMPF_SCORE",
            "RFV_CRITICAL_FLAG", "RFV_URGENT_FLAG",
        ] if c in cohort.columns
    ]
    numeric_features = raw_numeric + engineered_numeric
    categorical_features = pretriage_categorical + rfv_cols + chronic_cols

    train = cohort[cohort.YEAR.isin([2018, 2019, 2020])].copy()
    validation = cohort[cohort.YEAR.eq(2021)].copy()
    test = cohort[cohort.YEAR.eq(2022)].copy()  # RESERVED

    print("\nNumeric features:", ", ".join(numeric_features))
    print("Categorical features:", ", ".join(categorical_features))
    print("\nUsable rows per year:")
    print(cohort.groupby("YEAR").size().to_string())
    print("\n3-class target distribution:")
    display_counts = cohort["TRIAGE_CLASS"].map(dict(enumerate(LABEL_NAMES))).value_counts()
    print(display_counts.reindex(LABEL_NAMES).to_string())
    print(f"\nTemporal sizes: train={len(train):,}; validation={len(validation):,}; test RESERVED={len(test):,}")
    print("\nRFV risk flag prevalence (train):")
    print(f"  RFV_CRITICAL_FLAG=1: {int(train['RFV_CRITICAL_FLAG'].sum()):,} rows")
    print(f"  RFV_URGENT_FLAG=1:   {int(train['RFV_URGENT_FLAG'].sum()):,} rows")

    category_maps = fit_category_maps(train, categorical_features)
    for col in categorical_features:
        n_unique = train[col].nunique(dropna=True)
        if n_unique + 2 > 255:
            raise RuntimeError(f"{col} has {n_unique} train categories, exceeding HGB's limit.")

    X_train = build_matrix(train, numeric_features, category_maps)
    X_val = build_matrix(validation, numeric_features, category_maps)
    y_train = train["TRIAGE_CLASS"].to_numpy(dtype=np.int8)
    y_val = validation["TRIAGE_CLASS"].to_numpy(dtype=np.int8)
    categorical_mask = [False] * len(numeric_features) + [True] * len(categorical_features)

    # ---------------- STAGE 1: binary RED vs NOT-RED ----------------
    y_train_bin = (y_train == RED).astype(np.int8)
    y_val_bin = (y_val == RED).astype(np.int8)

    stage1_grid = {
        "learning_rate": [0.05, 0.10],
        "max_leaf_nodes": [15, 31],
        "max_iter": [300],
        "l2_regularization": [0.5, 2.0],
        "min_samples_leaf": [30, 60],
    }
    red_weight_multipliers = [1.0, 1.5, 2.0]
    thresholds = np.round(np.arange(0.15, 0.90, 0.025), 3)

    keys1 = list(stage1_grid.keys())
    combos1 = list(itertools.product(*stage1_grid.values()))
    total_fits1 = len(combos1) * len(red_weight_multipliers)
    print(f"\n[Stage 1] Searching {total_fits1} configs for RED vs NOT-RED "
          f"(target recall >= {MIN_RED_RECALL_TARGET:.0%}, then maximize precision)...")

    base_weight = compute_sample_weight("balanced", y_train_bin)
    best1 = None
    fit_n = 0
    for combo in combos1:
        params = dict(zip(keys1, combo))
        for red_mult in red_weight_multipliers:
            fit_n += 1
            sw = base_weight.copy()
            sw[y_train_bin == 1] *= red_mult

            model = HistGradientBoostingClassifier(
                categorical_features=categorical_mask,
                early_stopping=True, n_iter_no_change=20, validation_fraction=0.10,
                random_state=42, **params,
            )
            model.fit(X_train, y_train_bin, sample_weight=sw)
            proba = model.predict_proba(X_val)
            class_to_col = {int(c): i for i, c in enumerate(model.classes_)}
            red_col = class_to_col[1]

            # Pick the HIGHEST threshold that still clears the recall target
            # (maximizes precision subject to the recall floor).
            chosen = None
            for t in thresholds[::-1]:
                pred_bin = (proba[:, red_col] >= t).astype(int)
                recall = recall_score(y_val_bin, pred_bin, zero_division=0)
                if recall >= MIN_RED_RECALL_TARGET:
                    precision = precision_score(y_val_bin, pred_bin, zero_division=0)
                    chosen = (t, recall, precision)
                    break
            if chosen is None:
                # No threshold clears target; fall back to best-recall threshold.
                best_t, best_recall = None, -1.0
                for t in thresholds:
                    pred_bin = (proba[:, red_col] >= t).astype(int)
                    recall = recall_score(y_val_bin, pred_bin, zero_division=0)
                    if recall > best_recall:
                        best_recall, best_t = recall, t
                precision = precision_score(
                    y_val_bin, (proba[:, red_col] >= best_t).astype(int), zero_division=0
                )
                chosen = (best_t, best_recall, precision)

            t, recall, precision = chosen
            score = (recall >= MIN_RED_RECALL_TARGET, precision, recall)
            if best1 is None or score > best1["score"]:
                best1 = {
                    "score": score, "model": model, "params": params,
                    "red_weight_multiplier": float(red_mult), "threshold": float(t),
                    "recall": float(recall), "precision": float(precision),
                }
            if fit_n % 8 == 0 or fit_n == total_fits1:
                print(f"  [stage 1] completed {fit_n}/{total_fits1} fits")

    assert best1 is not None
    print(f"\n[Stage 1] best: recall={best1['recall']:.4f} precision={best1['precision']:.4f} "
          f"threshold={best1['threshold']} red_weight={best1['red_weight_multiplier']} "
          f"params={best1['params']}")

    # ---------------- STAGE 2: YELLOW vs GREEN, trained on TRUE non-RED only ----------------
    non_red_train_mask = y_train != RED
    X_train_s2 = X_train[non_red_train_mask]
    y_train_s2 = y_train[non_red_train_mask]  # values are YELLOW or GREEN

    non_red_val_mask = y_val != RED
    X_val_s2 = X_val[non_red_val_mask]
    y_val_s2 = y_val[non_red_val_mask]

    stage2_grid = {
        "learning_rate": [0.05, 0.10],
        "max_leaf_nodes": [15, 31],
    }
    keys2 = list(stage2_grid.keys())
    combos2 = list(itertools.product(*stage2_grid.values()))
    print(f"\n[Stage 2] Searching {len(combos2)} configs for YELLOW vs GREEN "
          f"(trained only on true non-RED rows)...")

    base_weight_s2 = compute_sample_weight("balanced", y_train_s2)
    best2 = None
    for combo in combos2:
        params = dict(zip(keys2, combo))
        model = HistGradientBoostingClassifier(
            categorical_features=categorical_mask,
            max_iter=300, l2_regularization=1.0, min_samples_leaf=30,
            early_stopping=True, n_iter_no_change=20, validation_fraction=0.10,
            random_state=42, **params,
        )
        model.fit(X_train_s2, y_train_s2, sample_weight=base_weight_s2)
        pred_s2 = model.predict(X_val_s2)
        macro_f1 = f1_score(y_val_s2, pred_s2, labels=[YELLOW, GREEN], average="macro", zero_division=0)
        if best2 is None or macro_f1 > best2["macro_f1"]:
            best2 = {"model": model, "params": params, "macro_f1": float(macro_f1)}

    assert best2 is not None
    print(f"[Stage 2] best: macro_f1(YELLOW/GREEN)={best2['macro_f1']:.4f} params={best2['params']}")

    # ---------------- Combine + apply safety guard on validation ----------------
    raw_predicted = two_stage_predict(best1["model"], best2["model"], X_val, best1["threshold"])
    predicted, guard_info, _ = apply_safety_guard(validation, raw_predicted)
    metrics = calculate_metrics(y_val, predicted)
    cm = np.asarray(metrics["confusion_matrix"])

    pre_guard_metrics = calculate_metrics(y_val, raw_predicted)

    print("\n=== FINAL PIPELINE: 2021 VALIDATION (stage1 + stage2 + safety guard) ===")
    print("Safety-guard overrides:", guard_info)
    print("\nConfusion matrix BEFORE guard (rows actual, columns predicted):")
    print("                 RED  YELLOW  GREEN")
    for name, row in zip(LABEL_NAMES, np.asarray(pre_guard_metrics["confusion_matrix"])):
        print(f"actual {name:6s} {row}")
    print("\nConfusion matrix AFTER guard (rows actual, columns predicted):")
    print("                 RED  YELLOW  GREEN")
    for name, row in zip(LABEL_NAMES, cm):
        print(f"actual {name:6s} {row}")

    print("\nClassification report (after guard):")
    print(classification_report(y_val, predicted, labels=LABELS, target_names=LABEL_NAMES, zero_division=0))

    print("Key safety/quality metrics (after guard):")
    for key in [
        "macro_f1", "weighted_f1", "balanced_accuracy", "red_recall", "red_precision",
        "yellow_recall", "green_recall", "red_to_yellow_count", "red_to_green_count",
        "severe_undertriage_rate",
    ]:
        value = metrics[key]
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    DATA.mkdir(parents=True, exist_ok=True)
    cohort.to_csv(DATA / "cleaned_triage_cohort_v6.csv.gz", index=False, compression="gzip")
    with open(DATA / "schemas.json", "w", encoding="utf-8") as f:
        json.dump(schemas, f, indent=2)

    results = {
        "source": "Official CDC NHAMCS ED Stata files, 2018-2022",
        "target": {
            "source_variable": "IMMEDR",
            "project_mapping": {"1-2": "RED", "3": "YELLOW", "4-5": "GREEN"},
        },
        "split": {"train": "2018-2020", "validation": "2021", "test_reserved": "2022"},
        "test_rows_reserved_and_not_predicted": int(len(test)),
        "numeric_features": numeric_features,
        "categorical_features": categorical_features,
        "architecture": "two_stage: stage1 RED-vs-rest (HGB) -> stage2 YELLOW-vs-GREEN (HGB) -> safety guard",
        "stage1": {
            "hyperparameters": best1["params"],
            "red_weight_multiplier": best1["red_weight_multiplier"],
            "threshold": best1["threshold"],
            "validation_recall": best1["recall"],
            "validation_precision": best1["precision"],
        },
        "stage2": {"hyperparameters": best2["params"], "validation_macro_f1_yellow_green": best2["macro_f1"]},
        "prototype_min_red_recall_target": MIN_RED_RECALL_TARGET,
        "validation_before_guard": pre_guard_metrics,
        "validation_after_guard": metrics,
        "safety_guard": {
            "layer_0_rfv_critical": "RFV chief complaint in RFV_CRITICAL_FAMILIES -> force RED",
            "layer_1_critical_vital_backstop": CRITICAL_VITAL_LIMITS,
            "layer_2_scope": "adult age >= 16 only for NEWS2 logic",
            "news2_partial_ge_7": "force project RED",
            "news2_partial_ge_5_or_single_parameter_3_or_rfv_urgent_or_bp_inconsistent": "block GREEN; minimum YELLOW",
            "rfv_family_labels": RFV_FAMILY_LABELS,
            "note": "Prototype policy; requires clinician review before real-world use.",
            "validation_overrides": guard_info,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    bundle_path = OUT / "triage_safety_pipeline_v6.pkl"
    result_path = OUT / "triage_validation_results_v6.json"

    with open(bundle_path, "wb") as f:
        pickle.dump(
            {
                "stage1_model": best1["model"],
                "stage2_model": best2["model"],
                "stage1_threshold": best1["threshold"],
                "numeric_features": numeric_features,
                "categorical_features": categorical_features,
                "raw_numeric_features": raw_numeric,
                "pretriage_categorical": pretriage_categorical,
                "rfv_cols": rfv_cols,
                "chronic_cols": chronic_cols,
                "category_maps": category_maps,
                "categorical_mask": categorical_mask,
                "label_mapping": {RED: "RED", YELLOW: "YELLOW", GREEN: "GREEN"},
                "version": "v6_two_stage_plus_rfv_risk_flags_plus_news2_plus_critical_backstop",
                "safety_policy": {
                    "rfv_critical_families": sorted(RFV_CRITICAL_FAMILIES),
                    "rfv_urgent_families": sorted(RFV_URGENT_FAMILIES),
                    "critical_vital_limits": CRITICAL_VITAL_LIMITS,
                    "news2_adult_min_age": NEWS2_ADULT_MIN_AGE,
                    "partial_news2_force_red": 7,
                    "partial_news2_block_green": 5,
                },
            },
            f,
        )

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved model bundle: {bundle_path}")
    print(f"Saved validation results: {result_path}")
    print("2022 test set remains untouched. Do NOT evaluate it until model design is frozen.")


def cli() -> None:
    parser = argparse.ArgumentParser(description="CDC NHAMCS triage prototype v6 (final)")
    parser.add_argument("--predict-json", help='Predict one patient from JSON.')
    parser.add_argument("--bundle", default=str(OUT / "triage_safety_pipeline_v6.pkl"))
    args = parser.parse_args()

    if args.predict_json:
        patient = json.loads(args.predict_json)
        print(json.dumps(predict_patient(patient, args.bundle), indent=2))
    else:
        main()


if __name__ == "__main__":
    cli()
