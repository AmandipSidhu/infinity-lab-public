"""Tests for scripts/pre_commit_gates.py.

Covers:
- check_cyclomatic_complexity: parses radon JSON output, reports violations
- check_bandit: parses bandit JSON output, flags HIGH severity issues
- check_semgrep: parses semgrep JSON output, flags ERROR-level findings
- check_ast: function length and parameter count via AST
- check_stub_detection: stub/placeholder detection via AST
- run_gates: integration of all checks
- CLI: exit codes, JSON output, --output flag
- TestReferenceStrategyGates: integration test against the reference strategy
"""

import ast
import datetime
import json
import shutil
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
    check_stub_detection,
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
        # def f(a, b, c, d, e, f, *args, **kwargs) has 8 params — should fail
        source = "def f(a, b, c, d, e, f, *args, **kwargs):\n    pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_ast(strategy)
        param_violations = [v for v in violations if v["check"] == "ast_param_count"]
        assert len(param_violations) == 1


# ---------------------------------------------------------------------------
# check_stub_detection
# ---------------------------------------------------------------------------


class TestCheckStubDetection:
    def test_bare_pass_in_concrete_class_fails(self, tmp_path: Path) -> None:
        """def process(self): pass in a non-except context → bare_pass violation."""
        source = "class MyClass:\n    def process(self):\n        pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert len(stub_v) == 1
        assert stub_v[0]["stub_type"] == "bare_pass"
        assert "process" in stub_v[0]["message"]

    def test_raise_not_implemented_in_concrete_class_fails(self, tmp_path: Path) -> None:
        """def on_data(self, data): raise NotImplementedError() → not_implemented violation."""
        source = "class MyClass:\n    def on_data(self, data):\n        raise NotImplementedError()\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert len(stub_v) == 1
        assert stub_v[0]["stub_type"] == "not_implemented"
        assert "on_data" in stub_v[0]["message"]

    def test_docstring_only_body_fails(self, tmp_path: Path) -> None:
        """def calculate(self): \"\"\"TODO\"\"\" → docstring_only violation."""
        source = 'class MyClass:\n    def calculate(self):\n        """TODO"""\n'
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert len(stub_v) == 1
        assert stub_v[0]["stub_type"] == "docstring_only"
        assert "calculate" in stub_v[0]["message"]

    def test_return_none_todo_comment_fails(self, tmp_path: Path) -> None:
        """def handle(self): return None  # TODO implement → return_none_todo violation."""
        source = "class MyClass:\n    def handle(self):\n        return None  # TODO implement\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert len(stub_v) == 1
        assert stub_v[0]["stub_type"] == "return_none_todo"
        assert "handle" in stub_v[0]["message"]

    def test_function_in_except_handler_is_suppressed(self, tmp_path: Path) -> None:
        """A function defined inside an except block with pass body is suppressed."""
        source = (
            "try:\n"
            "    risky()\n"
            "except ValueError:\n"
            "    def handle(): pass\n"
        )
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert stub_v == []

    def test_init_with_pass_is_suppressed(self, tmp_path: Path) -> None:
        """def __init__(self): pass is a valid empty constructor — suppressed."""
        source = "class MyClass:\n    def __init__(self):\n        pass\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert stub_v == []

    def test_abstract_method_in_abc_class_is_suppressed(self, tmp_path: Path) -> None:
        """Methods in an ABC subclass with raise NotImplementedError are suppressed."""
        source = (
            "from abc import ABC\n"
            "class MyABC(ABC):\n"
            "    def compute(self):\n"
            "        raise NotImplementedError\n"
        )
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert stub_v == []

    def test_fully_implemented_function_passes(self, tmp_path: Path) -> None:
        """A function with a real implementation produces no stub violation."""
        source = "def add(a: int, b: int) -> int:\n    return a + b\n"
        strategy = _make_strategy(tmp_path, source)
        violations = check_stub_detection(strategy)
        stub_v = [v for v in violations if v["check"] == "ast_stub_detection"]
        assert stub_v == []


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
    def test_missing_strategy_returns_1(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out.json"
        rc = main(["--strategy", "/nonexistent/strategy.py", "--output", str(out_file)])
        assert rc == 1
        data = json.loads(out_file.read_text())
        assert data["result"] == "FAIL"
        assert data["error_count"] == 1

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


# ---------------------------------------------------------------------------
# Reference strategy — integration validation
# ---------------------------------------------------------------------------


class TestReferenceStrategyGates:
    """Run real quality gates against the reference SMA crossover strategy.

    No subprocess mocking — exercises the full gate pipeline against
    production-quality code.  All gates must pass; the test writes the
    canonical output schema to a temporary file via pytest's tmp_path fixture.
    """

    REFERENCE_STRATEGY: Path = REPO_ROOT / "strategies" / "reference" / "sma_crossover_simple.py"

    # ------------------------------------------------------------------
    # Private helpers to compute per-gate metrics for the output schema
    # ------------------------------------------------------------------

    def _compute_max_ccn(self, strategy_file: Path) -> int:
        """Return the maximum CCN of any function/method block via radon."""
        if not shutil.which("radon"):
            pytest.skip("radon not installed")
        result = subprocess.run(
            ["radon", "cc", "-s", "-j", str(strategy_file)],
            capture_output=True,
            text=True,
        )
        raw = result.stdout.strip()
        if not raw:
            return 0
        try:
            data: dict[str, list[dict]] = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        max_ccn = 0
        for blocks in data.values():
            for block in blocks:
                if block.get("type") in ("F", "M"):
                    ccn: int = block.get("complexity", 0)
                    if ccn > max_ccn:
                        max_ccn = ccn
        return max_ccn

    def _compute_ast_metrics(self, strategy_file: Path) -> tuple[int, int]:
        """Return (max_function_length_lines, max_param_count) via AST."""
        source = strategy_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(strategy_file))
        except SyntaxError:
            return 0, 0
        max_length = 0
        max_params = 0
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Length = end_lineno - lineno + 1 (inclusive)
            length: int = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > max_length:
                max_length = length
            args = node.args
            param_count: int = (
                len(args.args)
                + len(args.posonlyargs)
                + len(args.kwonlyargs)
                + (1 if args.vararg else 0)
                + (1 if args.kwarg else 0)
            )
            if param_count > max_params:
                max_params = param_count
        return max_length, max_params

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def test_all_gates_pass_on_reference_strategy(self, tmp_path: Path) -> None:
        """All quality gates must pass on the reference SMA crossover strategy.

        Acceptance criteria (from issue #89):
        - stub_detection: PASS (no pass/TODO/placeholder bodies)
        - cyclomatic_complexity: PASS (max CCN < 10)
        - bandit_security: PASS (no HIGH severity findings)
        - function_length: PASS (max length < 150 lines)
        - param_count: PASS (max params < 8)

        Output is written to a temporary file via the tmp_path fixture.
        """
        assert self.REFERENCE_STRATEGY.is_file(), (
            f"Reference strategy not found: {self.REFERENCE_STRATEGY}"
        )

        summary = run_gates(self.REFERENCE_STRATEGY)

        assert summary["result"] == "PASS", (
            "Gates FAILED on reference strategy — violations:\n"
            + json.dumps(summary.get("violations", []), indent=2)
        )
        assert summary["error_count"] == 0, (
            "Gates produced ERROR-level findings on reference strategy:\n"
            + json.dumps(
                [v for v in summary.get("violations", []) if v.get("severity") == "ERROR"],
                indent=2,
            )
        )

        # Validate gate results from the summary (avoids tautological threshold re-checks)
        gates = summary["gates"]
        assert gates["stub_detection"]["result"] == "PASS", "stub_detection gate failed"
        assert gates["ccn_check"]["result"] == "PASS", "CCN gate failed"
        assert gates["bandit"]["result"] == "PASS", "bandit gate failed"
        assert gates["function_length"]["result"] == "PASS", "function_length gate failed"

        # Derive per-gate metrics for the output schema from summary violations.
        # max_ccn: max CCN among any violations found (0 when all functions are below threshold,
        # since run_gates() only records violations — functions with CCN < threshold are not
        # surfaced in the violations list).
        max_ccn = max(
            (v.get("ccn", 0) for v in gates["ccn_check"]["violations"]),
            default=0,
        )
        high_severity_count = len(
            [
                v for v in summary.get("violations", [])
                if v.get("check") == "bandit" and v.get("severity") == "ERROR"
            ]
        )
        stub_issues = [
            v.get("message", "")
            for v in summary.get("violations", [])
            if v.get("check") == "ast_stub_detection"
        ]
        param_count_violations = [
            v for v in summary.get("violations", [])
            if v.get("check") == "ast_param_count" and v.get("severity") == "ERROR"
        ]

        # Compute AST metrics independently for the output schema (not for gate validation)
        max_length, max_params = self._compute_ast_metrics(self.REFERENCE_STRATEGY)

        # Derive gate status from the actual summary results.
        # param_count has no dedicated gate in run_gates() summary (it shares ast_violations
        # with function_length), so we derive its status from violations directly.
        param_status = "FAIL" if param_count_violations else "PASS"

        # Build and persist the required output schema
        output_file = tmp_path / "pre_commit_gates_output.json"
        output: dict = {
            "strategy_file": str(self.REFERENCE_STRATEGY.relative_to(REPO_ROOT)),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "gates": {
                "stub_detection": {
                    "status": gates["stub_detection"]["result"],
                    "issues": stub_issues,
                },
                "cyclomatic_complexity": {
                    "status": gates["ccn_check"]["result"],
                    "max_ccn": max_ccn,
                },
                "bandit_security": {
                    "status": gates["bandit"]["result"],
                    "high_severity_count": high_severity_count,
                },
                "function_length": {
                    "status": gates["function_length"]["result"],
                    "max_length": max_length,
                },
                "param_count": {
                    "status": param_status,
                    "max_params": max_params,
                },
            },
            "overall_status": summary["result"],
        }
        output_file.write_text(json.dumps(output, indent=2), encoding="utf-8")

        # Validate what was written
        written = json.loads(output_file.read_text(encoding="utf-8"))
        assert written["overall_status"] == "PASS"
        for gate_name, gate_data in written["gates"].items():
            assert gate_data["status"] == "PASS", (
                f"Gate '{gate_name}' status is not PASS in written output"
            )
