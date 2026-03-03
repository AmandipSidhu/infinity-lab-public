#!/usr/bin/env python3
"""Strategy Reviewer — AI-powered trading logic critique gate (Phase 0, Step 2).

Reads a strategy spec YAML, sends it to an AI model for review, and outputs a
strict JSON verdict:

    {
        "verdict":    "PASS" | "WARN",
        "risk_level": "low" | "medium" | "high",
        "concerns":   ["...", ...]
    }

Model fallback chain (in order):
    Tier 1 — Gemini 2.0 Flash    (google-generativeai)
    Tier 2 — Gemini 1.5 Pro      (google-generativeai)
    Tier 3 — gpt-4o-mini         (openai)
    Tier 4 — Claude Opus          (anthropic)

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
    # Try to find a JSON block wrapped in ```json ... ``` fences
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    # Fall back to finding the first {...} block
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
# AI tier callers
# ---------------------------------------------------------------------------


def _call_gemini(model_name: str, spec_yaml: str) -> str:
    """Call Google Gemini via google-generativeai. Returns raw text."""
    import google.generativeai as genai  # type: ignore[import-untyped]

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY / GOOGLE_API_KEY is not set")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=_SYSTEM_PROMPT,
    )
    response = model.generate_content(spec_yaml)
    return response.text


def _call_openai(model_name: str, spec_yaml: str) -> str:
    """Call OpenAI via the openai SDK. Returns raw text."""
    from openai import OpenAI  # type: ignore[import-untyped]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": spec_yaml},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _call_anthropic(model_name: str, spec_yaml: str) -> str:
    """Call Anthropic Claude via the anthropic SDK. Returns raw text."""
    import anthropic  # type: ignore[import-untyped]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model_name,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": spec_yaml}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Repair helper — ask the same model to fix broken JSON
# ---------------------------------------------------------------------------


def _repair_json(raw_text: str, tier_caller: Any, *caller_args: Any) -> dict[str, Any]:
    """Ask the tier's caller to repair the broken JSON. Raises on failure."""
    repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(raw=raw_text)
    repaired_raw = tier_caller(*caller_args[:-1], repair_prompt)
    return _parse_and_validate(repaired_raw)


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

# Tier definitions: (label, caller_attribute_name, model_name)
_TIERS: list[tuple[str, str, str]] = [
    ("Tier 1: Gemini 2.0 Flash", "_call_gemini", "gemini-2.0-flash"),
    ("Tier 2: Gemini 1.5 Pro", "_call_gemini", "gemini-1.5-pro"),
    ("Tier 3: gpt-4o-mini", "_call_openai", "gpt-4o-mini"),
    ("Tier 4: Claude Opus", "_call_anthropic", "claude-opus-4-5"),
]


def _run_fallback_chain(spec_yaml: str) -> dict[str, Any]:
    """
    Try each tier in order. For each tier:
      1. Call the model.
      2. If the JSON is invalid, attempt one repair call.
      3. If repair also fails, log the error and move to the next tier.
    Returns the first successful validated result, or _FALLBACK_RESULT if all fail.
    """
    import sys

    _mod = sys.modules[__name__]

    for tier_label, caller_name, model_name in _TIERS:
        caller = getattr(_mod, caller_name)
        try:
            raw = caller(model_name, spec_yaml)
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
                return _repair_json(raw, caller, model_name, spec_yaml)
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
    """
    Review a raw YAML string.

    Checks the cache first; on a miss, runs the fallback chain and caches
    the result.

    Returns a validated result dict with keys: verdict, risk_level, concerns.
    """
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
        print(
            json.dumps({"error": f"File not found: {spec_path}"}),
            file=sys.stderr,
        )
        return 2

    with open(spec_path, "r", encoding="utf-8") as fh:
        raw_yaml = fh.read()

    # Validate the YAML is parseable before sending to AI
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        print(json.dumps({"error": f"YAML parse error: {exc}"}), file=sys.stderr)
        return 2

    if not isinstance(parsed, dict):
        print(
            json.dumps({"error": f"YAML file did not parse to a mapping: {spec_path}"}),
            file=sys.stderr,
        )
        return 2

    # If no AI API keys are configured, skip the review and write a SKIPPED result.
    _has_any_key = any(
        os.environ.get(k)
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    )
    if not _has_any_key:
        skipped_output: dict[str, Any] = {
            "status": "SKIPPED",
            "verdict": "WARN",
            "risk_level": "unknown",
            "concerns": [
                "SRV-I001: No AI API keys configured "
                "(GEMINI_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY). "
                "Strategy review skipped. Configure at least one key to enable AI review."
            ],
            "spec_file": spec_path,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        skipped_json = json.dumps(skipped_output, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as out_fh:
                out_fh.write(skipped_json)
        else:
            print(skipped_json)
        return 0

    result = review_spec(raw_yaml)
    output = dict(result)
    output["spec_file"] = spec_path
    output["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    json_output = json.dumps(output, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as out_fh:
            out_fh.write(json_output)
    else:
        print(json_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
