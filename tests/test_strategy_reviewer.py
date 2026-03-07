"""Tests for scripts/strategy_reviewer.py.

Covers:
- Happy path: valid spec with a PASS verdict from Tier 1
- Fallback chain: Tier 1 fails → Tier 2 succeeds
- Full fallback chain: all tiers fail → SRV-W050 WARN result
- JSON repair: tier returns broken JSON → repair succeeds
- JSON repair failure: broken JSON and repair also fails → next tier used
- Caching: cache hit skips AI calls; cache miss triggers AI calls
- Cache expiry: expired cache entry triggers fresh AI call
- CLI: missing args, missing file, valid file, invalid YAML
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

# Ensure the scripts directory is importable
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import strategy_reviewer  # noqa: E402
from strategy_reviewer import (  # noqa: E402
    _FALLBACK_RESULT,
    _extract_json_block,
    _load_cache,
    _parse_and_validate,
    _run_fallback_chain,
    _save_cache,
    _spec_hash,
    _validate_result,
    main,
    review_spec,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_RESULT: dict[str, Any] = {
    "verdict": "PASS",
    "risk_level": "low",
    "concerns": [],
}

WARN_RESULT: dict[str, Any] = {
    "verdict": "WARN",
    "risk_level": "high",
    "concerns": ["Stop loss is too wide."],
}

MINIMAL_SPEC_YAML = """\
metadata:
  name: "Test Strategy"
  version: "1.0.0"
  description: "A test strategy."
strategy:
  type: momentum
"""


# ---------------------------------------------------------------------------
# _spec_hash
# ---------------------------------------------------------------------------


class TestSpecHash:
    def test_returns_hex_string(self) -> None:
        h = _spec_hash("foo: bar\n")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest

    def test_same_input_same_hash(self) -> None:
        assert _spec_hash("foo: bar\n") == _spec_hash("foo: bar\n")

    def test_different_input_different_hash(self) -> None:
        assert _spec_hash("foo: bar\n") != _spec_hash("foo: baz\n")


# ---------------------------------------------------------------------------
# _extract_json_block
# ---------------------------------------------------------------------------


class TestExtractJsonBlock:
    def test_plain_json_returned_unchanged(self) -> None:
        text = '{"verdict": "PASS", "risk_level": "low", "concerns": []}'
        assert _extract_json_block(text) == text

    def test_fenced_json_block_extracted(self) -> None:
        text = '```json\n{"verdict": "PASS", "risk_level": "low", "concerns": []}\n```'
        result = _extract_json_block(text)
        assert '"verdict"' in result

    def test_json_embedded_in_prose(self) -> None:
        text = 'Here is my analysis:\n{"verdict": "WARN", "risk_level": "medium", "concerns": ["x"]}\nDone.'
        result = _extract_json_block(text)
        parsed = json.loads(result)
        assert parsed["verdict"] == "WARN"


# ---------------------------------------------------------------------------
# _validate_result
# ---------------------------------------------------------------------------


class TestValidateResult:
    def test_valid_pass_result(self) -> None:
        result = _validate_result({"verdict": "PASS", "risk_level": "low", "concerns": []})
        assert result["verdict"] == "PASS"
        assert result["risk_level"] == "low"
        assert result["concerns"] == []

    def test_valid_warn_result(self) -> None:
        result = _validate_result({"verdict": "WARN", "risk_level": "high", "concerns": ["risk"]})
        assert result["verdict"] == "WARN"
        assert result["concerns"] == ["risk"]

    def test_invalid_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid verdict"):
            _validate_result({"verdict": "FAIL", "risk_level": "low", "concerns": []})

    def test_invalid_risk_level_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid risk_level"):
            _validate_result({"verdict": "PASS", "risk_level": "extreme", "concerns": []})

    def test_concerns_not_list_raises(self) -> None:
        with pytest.raises(ValueError, match="'concerns' must be a list"):
            _validate_result({"verdict": "PASS", "risk_level": "low", "concerns": "none"})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="Expected a JSON object"):
            _validate_result(["PASS", "low", []])


# ---------------------------------------------------------------------------
# _parse_and_validate
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_valid_json_string(self) -> None:
        raw = '{"verdict": "PASS", "risk_level": "low", "concerns": []}'
        result = _parse_and_validate(raw)
        assert result["verdict"] == "PASS"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_and_validate("not json at all")

    def test_valid_json_invalid_schema_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_and_validate('{"verdict": "MAYBE", "risk_level": "low", "concerns": []}')


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestCache:
    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            h = _spec_hash(MINIMAL_SPEC_YAML)
            _save_cache(h, VALID_RESULT)
            loaded = _load_cache(h)
        assert loaded is not None
        assert loaded["verdict"] == "PASS"
        assert "_cached_at" not in loaded

    def test_cache_miss_returns_none(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            result = _load_cache("nonexistent_hash")
        assert result is None

    def test_expired_cache_returns_none(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            h = _spec_hash(MINIMAL_SPEC_YAML)
            _save_cache(h, VALID_RESULT)
            # Manually expire the cache entry
            cache_file = tmp_path / f"{h}.json"
            entry = json.loads(cache_file.read_text())
            entry["_cached_at"] = time.time() - (8 * 24 * 3600)  # 8 days ago
            cache_file.write_text(json.dumps(entry))

            loaded = _load_cache(h)
        assert loaded is None

    def test_corrupted_cache_returns_none(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            h = _spec_hash(MINIMAL_SPEC_YAML)
            cache_file = tmp_path / f"{h}.json"
            cache_file.write_text("not valid json")
            loaded = _load_cache(h)
        assert loaded is None


# ---------------------------------------------------------------------------
# _run_fallback_chain tests
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def _make_valid_raw(self, result: dict[str, Any]) -> str:
        return json.dumps(result)

    def test_tier1_success_returns_result(self) -> None:
        raw = self._make_valid_raw(VALID_RESULT)
        with patch.object(strategy_reviewer, "_call_gemini", return_value=raw) as mock_gemini:
            with patch.object(strategy_reviewer, "_call_gemini_thinking") as mock_gemini_thinking:
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)
        assert result["verdict"] == "PASS"
        mock_gemini.assert_called_once_with("gemini-2.5-flash", MINIMAL_SPEC_YAML)
        mock_gemini_thinking.assert_not_called()

    def test_tier1_fails_tier2_succeeds(self) -> None:
        raw = self._make_valid_raw(WARN_RESULT)
        models_called: list[str] = []

        def gemini_side_effect(model: str, spec: str) -> str:
            models_called.append(model)
            if model == "gemini-2.5-flash" and len(models_called) == 1:
                # First call is Tier 1 — fail it so Tier 2 is tried
                raise RuntimeError("Tier 1 API error")
            return raw

        with patch.object(strategy_reviewer, "_call_gemini", side_effect=gemini_side_effect):
            with patch.object(strategy_reviewer, "_call_gemini_thinking") as mock_thinking:
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "WARN"
        assert models_called == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
        mock_thinking.assert_not_called()

    def test_all_tiers_fail_returns_srv_w050(self) -> None:
        with patch.object(strategy_reviewer, "_call_gemini", side_effect=RuntimeError("fail")):
            with patch.object(strategy_reviewer, "_call_gemini_thinking", side_effect=RuntimeError("fail")):
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "WARN"
        assert result["risk_level"] == "high"
        assert any("SRV-W050" in c for c in result["concerns"])

    def test_tier1_invalid_json_repair_succeeds(self) -> None:
        bad_raw = "Here is the result: {'verdict': 'PASS'}"
        good_raw = self._make_valid_raw(VALID_RESULT)

        call_count = {"n": 0}

        def gemini_side_effect(model: str, text: str) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bad_raw  # First call: initial review, bad JSON
            return good_raw  # Second call: repair

        with patch.object(strategy_reviewer, "_call_gemini", side_effect=gemini_side_effect):
            with patch.object(strategy_reviewer, "_call_gemini_thinking") as mock_thinking:
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "PASS"
        assert call_count["n"] == 2
        mock_thinking.assert_not_called()

    def test_tier1_invalid_json_repair_fails_falls_to_tier2(self) -> None:
        bad_raw = "not json at all"
        good_raw = self._make_valid_raw(VALID_RESULT)

        models_called: list[str] = []

        def gemini_side_effect(model: str, text: str) -> str:
            models_called.append(model)
            if model == "gemini-2.5-flash":
                # Both Tier 1 initial and Tier 1 repair return bad JSON
                return bad_raw
            # Tier 2 (gemini-2.5-flash-lite) succeeds
            return good_raw

        with patch.object(strategy_reviewer, "_call_gemini", side_effect=gemini_side_effect):
            with patch.object(strategy_reviewer, "_call_gemini_thinking") as mock_thinking:
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "PASS"
        assert "gemini-2.5-flash-lite" in models_called
        mock_thinking.assert_not_called()

    def test_tier4_gemini_pro_called_as_last_resort(self) -> None:
        raw = self._make_valid_raw(WARN_RESULT)

        gemini_calls: list[tuple[str, str]] = []

        def gemini_side_effect(model: str, text: str) -> str:
            gemini_calls.append((model, text))
            if model == "gemini-2.5-pro":
                return raw
            raise RuntimeError("fail")

        with patch.object(strategy_reviewer, "_call_gemini", side_effect=gemini_side_effect):
            with patch.object(strategy_reviewer, "_call_gemini_thinking", side_effect=RuntimeError("fail")):
                result = _run_fallback_chain(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "WARN"
        assert any(model == "gemini-2.5-pro" for model, _ in gemini_calls)


# ---------------------------------------------------------------------------
# review_spec (integration: cache + fallback chain)
# ---------------------------------------------------------------------------


class TestReviewSpec:
    def test_cache_hit_skips_ai(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            h = _spec_hash(MINIMAL_SPEC_YAML)
            _save_cache(h, VALID_RESULT)

            with patch.object(strategy_reviewer, "_run_fallback_chain") as mock_chain:
                result = review_spec(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "PASS"
        mock_chain.assert_not_called()

    def test_cache_miss_calls_fallback_chain(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=WARN_RESULT) as mock_chain:
                result = review_spec(MINIMAL_SPEC_YAML)

        assert result["verdict"] == "WARN"
        mock_chain.assert_called_once_with(MINIMAL_SPEC_YAML)

    def test_result_is_cached_after_ai_call(self, tmp_path: Path) -> None:
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                review_spec(MINIMAL_SPEC_YAML)

            # Second call should use cache, not AI
            with patch.object(strategy_reviewer, "_run_fallback_chain") as mock_chain2:
                result2 = review_spec(MINIMAL_SPEC_YAML)

        assert result2["verdict"] == "PASS"
        mock_chain2.assert_not_called()


# ---------------------------------------------------------------------------
# CLI (main function)
# ---------------------------------------------------------------------------


class TestCLI:
    def test_no_args_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main([])
        assert rc == 2

    def test_too_many_args_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["a.yaml", "b.yaml"])
        assert rc == 2

    def test_file_not_found_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["/nonexistent/path/spec.yaml"])
        assert rc == 2

    def test_invalid_yaml_returns_2(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("key: [unclosed bracket\n")
        rc = main([str(bad_yaml)])
        assert rc == 2

    def test_non_mapping_yaml_returns_2(self, tmp_path: Path) -> None:
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")
        rc = main([str(list_yaml)])
        assert rc == 2

    def test_valid_spec_returns_0_and_outputs_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(MINIMAL_SPEC_YAML)

        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                rc = main([str(spec_file)])

        assert rc == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["verdict"] == "PASS"
        assert output["risk_level"] == "low"
        assert "spec_file" in output
        assert "reviewed_at" in output

    def test_warn_verdict_still_returns_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(MINIMAL_SPEC_YAML)
        # FIXED: set fake key so the no-key-stub branch is not taken and the mock runs
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-for-mock")

        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=WARN_RESULT):
                rc = main([str(spec_file)])

        assert rc == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["verdict"] == "WARN"

    def test_srv_w050_fallback_returns_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(MINIMAL_SPEC_YAML)
        # FIXED: set fake key so the no-key-stub branch is not taken and the mock runs
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-for-mock")

        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=_FALLBACK_RESULT):
                rc = main([str(spec_file)])

        assert rc == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["verdict"] == "WARN"
        assert any("SRV-W050" in c for c in output["concerns"])
