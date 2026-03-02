"""Tests for scripts/pre_commit_gates.py.

Covers:
- check_cyclomatic_complexity: parses radon JSON output, reports violations
- check_bandit: parses bandit JSON output, flags HIGH severity issues
- check_semgrep: parses semgrep JSON output, flags ERROR-level findings
- check_ast: function length and parameter count via AST
- run_gates: integration of all checks
- CLI: exit codes, JSON output, --output flag
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pre_commit_gates  # noqa: E402
from pre_commit_gates import (  # noqa: E402
    _PARAM_MAX_COUNT,
    _CCN_THRESHOLD,
    _FUNCTION_MAX_LINES,
    check_ast,
    check_bandit,
    check_cyclomatic_complexity,
    check_semgrep,
    main,
    run_gates,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_strategy(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "strategy.py"
    f.write_text(source, encoding="utf-8")
    return f


def _make_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# check_cyclomatic_complexity
# ---------------------------------------------------------------------------


class TestCheckCyclomaticComplexity:
    def test_no_violations_when_ccn_below_threshold(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def foo(): pass\n")
        radon_output = json.dumps(
            {str(strategy): [{"name": "foo", "lineno": 1, "complexity": 3}]}
        )
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=radon_output)):
            violations = check_cyclomatic_complexity(strategy)
        assert violations == []

    def test_violation_when_ccn_equals_threshold(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def foo(): pass\n")
        radon_output = json.dumps(
            {str(strategy): [{"name": "foo", "lineno": 1, "complexity": _CCN_THRESHOLD}]}
        )
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=radon_output)):
            violations = check_cyclomatic_complexity(strategy)
        assert len(violations) == 1
        assert violations[0]["check"] == "radon_ccn"
        assert violations[0]["ccn"] == _CCN_THRESHOLD

    def test_violation_when_ccn_above_threshold(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def bar(): pass\n")
        radon_output = json.dumps(
            {str(strategy): [{"name": "bar", "lineno": 5, "complexity": 15}]}
        )
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=radon_output)):
            violations = check_cyclomatic_complexity(strategy)
        assert len(violations) == 1
        assert violations[0]["ccn"] == 15

    def test_error_on_radon_failure(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="", stderr="crashed", returncode=2),
        ):
            violations = check_cyclomatic_complexity(strategy)
        assert len(violations) == 1
        assert "radon cc failed" in violations[0]["message"]

    def test_error_on_invalid_json(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="not json"),
        ):
            violations = check_cyclomatic_complexity(strategy)
        assert len(violations) == 1
        assert "unparseable" in violations[0]["message"]

    def test_empty_stdout_no_violations(self, tmp_path: Path) -> None:
        """radon returns empty stdout when file has no functions — no violation."""
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="", returncode=0),
        ):
            violations = check_cyclomatic_complexity(strategy)
        assert violations == []


# ---------------------------------------------------------------------------
# check_bandit
# ---------------------------------------------------------------------------


class TestCheckBandit:
    def test_no_violations_for_clean_output(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "x = 1\n")
        bandit_output = json.dumps({"results": [], "metrics": {}})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=bandit_output)):
            violations = check_bandit(strategy)
        assert violations == []

    def test_high_severity_issue_becomes_violation(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        issue = {
            "test_id": "B301",
            "issue_text": "Pickle usage detected",
            "issue_severity": "HIGH",
            "issue_confidence": "HIGH",
            "filename": str(strategy),
            "line_number": 3,
        }
        bandit_output = json.dumps({"results": [issue]})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=bandit_output)):
            violations = check_bandit(strategy)
        assert len(violations) == 1
        assert violations[0]["check"] == "bandit"
        assert "B301" in violations[0]["message"]

    def test_medium_severity_not_flagged(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        issue = {
            "test_id": "B105",
            "issue_text": "Hardcoded password",
            "issue_severity": "MEDIUM",
            "issue_confidence": "MEDIUM",
            "filename": str(strategy),
            "line_number": 1,
        }
        bandit_output = json.dumps({"results": [issue]})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=bandit_output)):
            violations = check_bandit(strategy)
        assert violations == []

    def test_error_on_unexpected_exit_code(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="", stderr="err", returncode=2),
        ):
            violations = check_bandit(strategy)
        assert len(violations) == 1
        assert "bandit failed" in violations[0]["message"]

    def test_empty_stdout_no_violation(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="", returncode=0),
        ):
            violations = check_bandit(strategy)
        assert violations == []


# ---------------------------------------------------------------------------
# check_semgrep
# ---------------------------------------------------------------------------


class TestCheckSemgrep:
    def test_no_violations_for_empty_results(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "x = 1\n")
        semgrep_output = json.dumps({"results": [], "errors": []})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=semgrep_output)):
            violations = check_semgrep(strategy)
        assert violations == []

    def test_error_severity_finding_becomes_violation(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        finding = {
            "check_id": "python.security.audit.eval-detected",
            "path": str(strategy),
            "start": {"line": 10},
            "extra": {
                "severity": "ERROR",
                "message": "Detected eval() usage",
            },
        }
        semgrep_output = json.dumps({"results": [finding]})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=semgrep_output)):
            violations = check_semgrep(strategy)
        assert len(violations) == 1
        assert violations[0]["check"] == "semgrep"
        assert "eval" in violations[0]["message"].lower()

    def test_warning_severity_not_flagged(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        finding = {
            "check_id": "some-rule",
            "path": str(strategy),
            "start": {"line": 1},
            "extra": {"severity": "WARNING", "message": "just a warning"},
        }
        semgrep_output = json.dumps({"results": [finding]})
        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=semgrep_output)):
            violations = check_semgrep(strategy)
        assert violations == []

    def test_error_on_unexpected_exit_code(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "")
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(stdout="", stderr="crash", returncode=3),
        ):
            violations = check_semgrep(strategy)
        assert len(violations) == 1
        assert "semgrep failed" in violations[0]["message"]


# ---------------------------------------------------------------------------
# check_ast
# ---------------------------------------------------------------------------


class TestCheckAst:
    def test_clean_function_passes(self, tmp_path: Path) -> None:
        source = "def add(a, b):\n    return a + b\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        assert violations == []

    def test_function_at_length_limit_fails(self, tmp_path: Path) -> None:
        body = "\n".join(["    x = 1"] * (_FUNCTION_MAX_LINES - 1))
        source = f"def long_func(a):\n{body}\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        length_violations = [v for v in violations if v["check"] == "ast_function_length"]
        assert len(length_violations) == 1
        assert length_violations[0]["length"] >= _FUNCTION_MAX_LINES

    def test_function_just_under_length_limit_passes(self, tmp_path: Path) -> None:
        body = "\n".join(["    x = 1"] * (_FUNCTION_MAX_LINES - 2))
        source = f"def short_func(a):\n{body}\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        length_violations = [v for v in violations if v["check"] == "ast_function_length"]
        assert length_violations == []

    def test_function_at_param_limit_fails(self, tmp_path: Path) -> None:
        params = ", ".join(f"p{i}" for i in range(_PARAM_MAX_COUNT))
        source = f"def many_params({params}):\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        param_violations = [v for v in violations if v["check"] == "ast_param_count"]
        assert len(param_violations) == 1
        assert param_violations[0]["param_count"] == _PARAM_MAX_COUNT

    def test_function_just_under_param_limit_passes(self, tmp_path: Path) -> None:
        params = ", ".join(f"p{i}" for i in range(_PARAM_MAX_COUNT - 1))
        source = f"def few_params({params}):\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        param_violations = [v for v in violations if v["check"] == "ast_param_count"]
        assert param_violations == []

    def test_syntax_error_returns_error_violation(self, tmp_path: Path) -> None:
        source = "def broken(:\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        assert len(violations) == 1
        assert violations[0]["check"] == "ast"
        assert "SyntaxError" in violations[0]["message"]

    def test_vararg_counts_as_one_param(self, tmp_path: Path) -> None:
        # def f(a, b, c, *args) has 4 params (3 positional + 1 vararg) — should pass
        source = "def f(a, b, c, *args):\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        param_violations = [v for v in violations if v["check"] == "ast_param_count"]
        assert param_violations == []

    def test_vararg_plus_kwargs_can_exceed_limit(self, tmp_path: Path) -> None:
        # def f(a, b, c, *args, **kwargs) has 5 params — should fail
        source = "def f(a, b, c, *args, **kwargs):\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        param_violations = [v for v in violations if v["check"] == "ast_param_count"]
        assert len(param_violations) == 1


# ---------------------------------------------------------------------------
# run_gates (integration)
# ---------------------------------------------------------------------------


class TestRunGates:
    def test_all_pass_returns_pass(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def add(a, b):\n    return a + b\n")
        clean_radon = json.dumps({str(strategy): [{"name": "add", "lineno": 1, "complexity": 1}]})
        clean_bandit = json.dumps({"results": []})
        clean_semgrep = json.dumps({"results": []})

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "radon":
                return _make_subprocess_result(stdout=clean_radon)
            if cmd[0] == "bandit":
                return _make_subprocess_result(stdout=clean_bandit)
            if cmd[0] == "semgrep":
                return _make_subprocess_result(stdout=clean_semgrep)
            return _make_subprocess_result()

        with patch("subprocess.run", side_effect=fake_run):
            summary = run_gates(strategy)

        assert summary["result"] == "PASS"
        assert summary["error_count"] == 0

    def test_any_violation_returns_fail(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def add(a, b):\n    return a + b\n")
        bad_radon = json.dumps(
            {str(strategy): [{"name": "add", "lineno": 1, "complexity": 12}]}
        )
        clean = json.dumps({"results": []})

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "radon":
                return _make_subprocess_result(stdout=bad_radon)
            return _make_subprocess_result(stdout=clean)

        with patch("subprocess.run", side_effect=fake_run):
            summary = run_gates(strategy)

        assert summary["result"] == "FAIL"
        assert summary["error_count"] >= 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_missing_strategy_returns_2(self) -> None:
        rc = main(["--strategy", "/nonexistent/strategy.py"])
        assert rc == 2

    def test_no_args_exits_with_error(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_output_flag_writes_json(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def add(a, b):\n    return a + b\n")
        out_file = tmp_path / "output.json"
        clean_radon = json.dumps({str(strategy): [{"name": "add", "lineno": 1, "complexity": 1}]})
        clean_bandit = json.dumps({"results": []})
        clean_semgrep = json.dumps({"results": []})

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "radon":
                return _make_subprocess_result(stdout=clean_radon)
            if cmd[0] == "bandit":
                return _make_subprocess_result(stdout=clean_bandit)
            return _make_subprocess_result(stdout=clean_semgrep)

        with patch("subprocess.run", side_effect=fake_run):
            rc = main(["--strategy", str(strategy), "--output", str(out_file)])

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "result" in data
        assert rc == 0

    def test_returns_1_on_violations(self, tmp_path: Path) -> None:
        strategy = _make_strategy(tmp_path, "def add(a, b):\n    return a + b\n")
        bad_radon = json.dumps(
            {str(strategy): [{"name": "add", "lineno": 1, "complexity": 15}]}
        )
        clean = json.dumps({"results": []})

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "radon":
                return _make_subprocess_result(stdout=bad_radon)
            return _make_subprocess_result(stdout=clean)

        with patch("subprocess.run", side_effect=fake_run):
            rc = main(["--strategy", str(strategy)])

        assert rc == 1
