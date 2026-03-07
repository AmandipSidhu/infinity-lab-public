#!/usr/bin/env python3
"""Strategy Reviewer — AI-powered trading logic critique gate (Phase 0, Step 2).

Reads a strategy spec YAML, sends it to an AI model for review, and outputs a
strict JSON verdict:

    {
        "verdict":    "PASS" | "WARN",
        "risk_level": "low" | "medium" | "high",
        "concerns":   ["...", ...]
    }

Model fallback chain (all-Gemini — ARCHITECTURE v4.5 §1 & §5):
    Tier 1 — gemini/gemini-2.5-flash       (google-generativeai)
    Tier 2 — gemini/gemini-2.5-flash-lite  (google-generativeai)
    Tier 3 — gemini/gemini-2.5-flash       with thinking tokens
    Tier 4 — gemini/gemini-2.5-pro         (google-generativeai)

Only GEMINI_API_KEY (or GOOGLE_API_KEY) is required.
ANTHROPIC_API_KEY and OPENAI_API_KEY are not used.

Caching:
    SHA-256 of the raw YAML text is used as a cache key.
    Cache files live in ~/.cache/strategy_reviewer/ and expire after 7 days.

Exit codes:
    0 — Verdict is PASS or WARN (including SRV-W050 all-tiers-failed fallback)
    2 — Unrecoverable file / YAML parse failure
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.environ.get("STRATEGY_REVIEWER_CACHE_DIR", Path.home() / ".cache" / "strategy_reviewer"))
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days

_SYSTEM_PROMPT = (
    "You are an expert quantitative trading strategy risk analyst. "
    "Your job is to review a trading strategy specification and identify risks, logical flaws, "
    "overfitting signals, or unrealistic assumptions. "
    "Respond ONLY with a valid JSON object — no markdown, no extra text — in this exact schema:\n"
    '{"verdict": "PASS" | "WARN", "risk_level": "low" | "medium" | "high", "concerns": ["..."]}\n'
    "Use WARN if you find any significant concerns; use PASS only if the strategy is sound."
)

_REPAIR_PROMPT_TEMPLATE = (
    "The following text was supposed to be a JSON object with keys 'verdict', 'risk_level', and 'concerns' "
    "but it is not valid JSON. Please fix it and return ONLY the corrected JSON object — no markdown, "
    "no explanation:\n\n{raw}"
)

_FALLBACK_RESULT: dict[str, Any] = {
    "verdict": "WARN",
    "risk_level": "high",
    "concerns": [
        "SRV-W050: All AI review tiers failed. Manual review required before proceeding."
    ],
}

_NO_KEY_RESULT: dict[str, Any] = {
    "verdict": "PASS",
    "risk_level": "low",
    "concerns": [],
    "approved": True,
    "score": 7,
    "reasoning": "API key not configured — automated review skipped.",
}

_VALID_VERDICTS = {"PASS", "WARN"}
_VALID_RISK_LEVELS = {"low", "medium", "high"}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _spec_hash(raw_yaml: str) -> str:
    return hashlib.sha256(raw_yaml.encode("utf-8")).hexdigest()


def _cache_path(spec_hash: str) -> Path:
    return CACHE_DIR / f"{spec_hash}.json"


def _load_cache(spec_hash: str) -> dict[str, Any] | None:
    path = _cache_path(spec_hash)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            entry = json.load(fh)
        cached_at = entry.get("_cached_at", 0)
        if time.time() - cached_at > CACHE_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return None
        result = {k: v for k, v in entry.items() if not k.startswith("_")}
        return result
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(spec_hash: str, result: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(spec_hash)
    entry = dict(result)
    entry["_cached_at"] = time.time()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)


# ---------------------------------------------------------------------------
# JSON validation / repair
# ---------------------------------------------------------------------------


def _extract_json_block(text: str) -> str:
    """Try to extract a JSON object from text that may contain surrounding prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        return brace.group(0)
    return text


def _validate_result(data: Any) -> dict[str, Any]:
    """Raise ValueError if data does not conform to the expected schema."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
    verdict = data.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"Invalid verdict {verdict!r}; expected one of {_VALID_VERDICTS}")
    risk_level = data.get("risk_level")
    if risk_level not in _VALID_RISK_LEVELS:
        raise ValueError(f"Invalid risk_level {risk_level!r}; expected one of {_VALID_RISK_LEVELS}")
    concerns = data.get("concerns")
    if not isinstance(concerns, list):
        raise ValueError(f"'concerns' must be a list, got {type(concerns).__name__}")
    return {
        "verdict": verdict,
        "risk_level": risk_level,
        "concerns": [str(c) for c in concerns],
    }


def _parse_and_validate(raw_text: str) -> dict[str, Any]:
    """Parse raw text to a validated result dict, raising ValueError on failure."""
    candidate = _extract_json_block(raw_text)
    parsed = json.loads(candidate)
    return _validate_result(parsed)


# ---------------------------------------------------------------------------
# AI tier callers — all Gemini (ARCHITECTURE v4.5 §1)
# ---------------------------------------------------------------------------


def _call_gemini(model_name: str, spec_yaml: str, thinking: bool = False) -> str:
    """Call Google Gemini via google-generativeai. Returns raw text."""
    import google.generativeai as genai  # type: ignore[import-untyped]

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY / GOOGLE_API_KEY is not set")
    genai.configure(api_key=api_key)

    generation_config: dict[str, Any] = {}
    if thinking:
        # Enable thinking tokens for deeper reasoning on Tier 3
        generation_config["thinking_config"] = {"thinking_budget": 2048}

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=generation_config if generation_config else None,
    )
    response = model.generate_content(spec_yaml)
    return response.text


# ---------------------------------------------------------------------------
# Repair helper
# ---------------------------------------------------------------------------


def _repair_json(raw_text: str, model_name: str, thinking: bool = False) -> dict[str, Any]:
    """Ask the same Gemini model to repair broken JSON. Raises on failure."""
    repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(raw=raw_text)
    repaired_raw = _call_gemini(model_name, repair_prompt, thinking=thinking)
    return _parse_and_validate(repaired_raw)


# ---------------------------------------------------------------------------
# Fallback chain — all-Gemini ladder
# ---------------------------------------------------------------------------

# (label, model_name, thinking_tokens)
_TIERS: list[tuple[str, str, bool]] = [
    ("Tier 1: gemini-2.5-flash",      "gemini-2.5-flash",      False),
    ("Tier 2: gemini-2.5-flash-lite", "gemini-2.5-flash-lite", False),
    ("Tier 3: gemini-2.5-flash+think","gemini-2.5-flash",      True),
    ("Tier 4: gemini-2.5-pro",        "gemini-2.5-pro",        False),
]


def _run_fallback_chain(spec_yaml: str) -> dict[str, Any]:
    """
    Try each Gemini tier in order. For each tier:
      1. Call the model.
      2. If the JSON is invalid, attempt one repair call on the same model.
      3. If repair also fails, move to the next tier.
    Returns the first successful validated result, or _FALLBACK_RESULT if all fail.
    """
    for tier_label, model_name, thinking in _TIERS:
        try:
            raw = _call_gemini(model_name, spec_yaml, thinking=thinking)
        except Exception as exc:
            print(f"[strategy_reviewer] {tier_label} call failed: {exc}", file=sys.stderr)
            continue

        try:
            return _parse_and_validate(raw)
        except (json.JSONDecodeError, ValueError) as parse_err:
            print(
                f"[strategy_reviewer] {tier_label} returned invalid JSON ({parse_err}); "
                "attempting repair…",
                file=sys.stderr,
            )
            try:
                return _repair_json(raw, model_name, thinking=thinking)
            except Exception as repair_err:
                print(
                    f"[strategy_reviewer] {tier_label} repair failed: {repair_err}",
                    file=sys.stderr,
                )
                continue

    print(
        "[strategy_reviewer] All tiers exhausted. Returning SRV-W050 fallback.",
        file=sys.stderr,
    )
    return _FALLBACK_RESULT


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def review_spec(spec_yaml: str) -> dict[str, Any]:
    """Review a raw YAML string. Checks cache first; on miss runs fallback chain."""
    h = _spec_hash(spec_yaml)
    cached = _load_cache(h)
    if cached is not None:
        return cached
    result = _run_fallback_chain(spec_yaml)
    _save_cache(h, result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    spec_path: str | None = None
    output_path: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--spec" and i + 1 < len(args):
            spec_path = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if spec_path is None:
        if len(positional) == 1:
            spec_path = positional[0]
        else:
            print(
                json.dumps({"error": "Usage: strategy_reviewer.py --spec <path/to/spec.yaml> [--output <path>]"}),
                file=sys.stderr,
            )
            return 2

    if not os.path.isfile(spec_path):
        print(json.dumps({"error": f"File not found: {spec_path}"}), file=sys.stderr)
        return 2

    with open(spec_path, "r", encoding="utf-8") as fh:
        raw_yaml = fh.read()

    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        print(json.dumps({"error": f"YAML parse error: {exc}"}), file=sys.stderr)
        return 2

    if not isinstance(parsed, dict):
        print(json.dumps({"error": f"YAML file did not parse to a mapping: {spec_path}"}), file=sys.stderr)
        return 2

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not gemini_key:
        print(
            "[strategy_reviewer] No GEMINI_API_KEY configured — writing stub review.",
            file=sys.stderr,
        )
        result_data = dict(_NO_KEY_RESULT)
    else:
        result_data = review_spec(raw_yaml)

    output = dict(result_data)
    output["spec_file"] = spec_path
    output["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    if "approved" not in output:
        output["approved"] = output.get("verdict") == "PASS"
    if "score" not in output:
        output["score"] = 8 if output.get("verdict") == "PASS" else 5
    if "reasoning" not in output:
        concerns = output.get("concerns", [])
        output["reasoning"] = concerns[0] if concerns else "Strategy reviewed."

    json_output = json.dumps(output, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as out_fh:
            out_fh.write(json_output)
    else:
        print(json_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
