"""Unit tests for the Tier 1 static scanner.

Tests cover all five contamination categories plus:
- Tier 0 skill structural pre-check
- SyntaxError handling
- Clean code passes
- Critical findings gate ``overall_passed``
- Non-critical findings leave ``overall_passed=True``
- ``should_proceed_to_tier2`` logic
"""

from __future__ import annotations

from src.schemas.verification import Severity
from src.verification.static_scanner import scan, scan_text_only, should_proceed_to_tier2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CLEAN_MCP_TOOL = """\
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("weather")

@mcp.tool()
async def fetch_weather(city: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.weather.com/{city}")
        return resp.text
"""

VALID_SKILL_MD = """\
---
name: data-loader
description: "Loads CSV data from disk."
---

# Data Loader

This skill loads CSV files and returns a dataframe.
"""


# ===================================================================
# Category 1: Hardcoded data (CRITICAL)
# ===================================================================

class TestHardcodedData:
    def test_hardcoded_list_return(self):
        code = '''\
def get_cities():
    return ["New York", "London", "Tokyo"]
'''
        report = scan(code, "cities-tool")
        assert not report.overall_passed
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_hardcoded_dict_return(self):
        code = '''\
def get_config():
    return {"host": "localhost", "port": "8080", "db": "test"}
'''
        report = scan(code, "config-tool")
        assert not report.overall_passed
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_small_list_not_flagged(self):
        """Lists with fewer than 3 items are fine."""
        code = '''\
def get_pair():
    return ["a", "b"]
'''
        report = scan(code, "pair-tool")
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) == 0

    def test_async_function_hardcoded(self):
        code = '''\
async def get_data():
    return ["one", "two", "three", "four"]
'''
        report = scan(code, "async-tool")
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1

    def test_non_literal_list_not_flagged(self):
        """Lists with non-constant elements are fine."""
        code = '''\
def get_items(x, y, z):
    return [x, y, z]
'''
        report = scan(code, "dynamic-tool")
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) == 0

    def test_hardcoded_list_via_variable_indirection(self):
        """B1.2: data = ['a','b','c']; return data must be flagged."""
        code = '''\
def get_cities():
    data = ["New York", "London", "Tokyo"]
    return data
'''
        report = scan(code, "cities-tool")
        assert not report.overall_passed
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_hardcoded_dict_via_variable_indirection(self):
        """B1.2: config = {'a':1,'b':2,'c':3}; return config must be flagged."""
        code = '''\
def get_config():
    config = {"host": "localhost", "port": "8080", "db": "test"}
    return config
'''
        report = scan(code, "config-tool")
        assert not report.overall_passed
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_small_variable_list_not_flagged(self):
        """B1.2: small indirect lists (< 3 items) should not be flagged."""
        code = '''\
def get_pair():
    data = ["a", "b"]
    return data
'''
        report = scan(code, "pair-tool")
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) == 0

    def test_async_hardcoded_list_via_variable_indirection(self):
        """B1.2 fix applies to async functions too."""
        code = '''\
async def get_users():
    users = ["alice", "bob", "charlie"]
    return users
'''
        report = scan(code, "users-tool")
        assert not report.overall_passed
        findings = [f for f in report.findings if f.category == "hardcoded_data"]
        assert len(findings) >= 1


# ===================================================================
# Category 2: Silent exception handling (HIGH)
# ===================================================================

class TestSilentException:
    def test_bare_except(self):
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except:
        pass
'''
        report = scan(code, "bare-except-tool")
        findings = [f for f in report.findings if f.category == "silent_exception"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.HIGH

    def test_except_exception_pass(self):
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except Exception:
        pass
'''
        report = scan(code, "except-pass-tool")
        findings = [f for f in report.findings if f.category == "silent_exception"]
        assert len(findings) >= 1

    def test_except_with_reraise_ok(self):
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except Exception:
        raise
'''
        report = scan(code, "reraise-tool")
        findings = [f for f in report.findings if f.category == "silent_exception"]
        assert len(findings) == 0

    def test_except_with_logging_ok(self):
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except Exception as e:
        logger.error(f"Failed: {e}")
'''
        report = scan(code, "logging-tool")
        findings = [f for f in report.findings if f.category == "silent_exception"]
        assert len(findings) == 0

    def test_specific_exception_not_flagged(self):
        """Only bare except / except Exception are flagged."""
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except ZeroDivisionError:
        pass
'''
        report = scan(code, "specific-except-tool")
        findings = [f for f in report.findings if f.category == "silent_exception"]
        assert len(findings) == 0

    def test_silent_exception_is_high_not_critical(self):
        """HIGH severity should not block overall_passed."""
        code = '''\
def do_thing():
    try:
        x = 1 / 0
    except:
        pass
'''
        report = scan(code, "high-only-tool")
        # No other categories present, so only HIGH findings
        critical = [f for f in report.findings if f.severity == Severity.CRITICAL]
        assert len(critical) == 0
        assert report.overall_passed is True


# ===================================================================
# Category 3: Mock/demo artifacts (CRITICAL)
# ===================================================================

class TestMockArtifacts:
    def test_mock_variable(self):
        code = "mock_data = [1, 2, 3]\n"
        report = scan(code, "mock-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_fake_in_string(self):
        code = 'result = "This is fake data"\n'
        report = scan(code, "fake-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_placeholder_detected(self):
        code = "placeholder_value = 42\n"
        report = scan(code, "placeholder-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_dummy_detected(self):
        code = "dummy_response = {}\n"
        report = scan(code, "dummy-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_test_data_detected(self):
        code = "TEST_DATA = [1, 2, 3]\n"
        report = scan(code, "testdata-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_demo_in_uppercase(self):
        code = 'DEMO_MODE = True\n'
        report = scan(code, "demo-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_sample_not_flagged(self):
        """'sample' was removed from mock indicators — too many false positives
        in legitimate docstrings and Field examples (e.g. T07 fuzzy-join)."""
        code = 'Sample_Output = "hello"\n'
        report = scan(code, "sample-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) == 0


# ===================================================================
# Category 4: Synthetic data indicators (HIGH)
# ===================================================================

class TestSyntheticData:
    def test_todo_comment(self):
        code = "# TODO: implement real logic\nx = 1\n"
        report = scan(code, "todo-tool")
        findings = [f for f in report.findings if f.category == "synthetic_data"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.HIGH

    def test_fixme_comment(self):
        code = "# FIXME: this is broken\nx = 1\n"
        report = scan(code, "fixme-tool")
        findings = [f for f in report.findings if f.category == "synthetic_data"]
        assert len(findings) >= 1

    def test_placeholder_comment(self):
        code = "# placeholder logic\nx = 1\n"
        report = scan(code, "placeholder-comment-tool")
        findings = [f for f in report.findings if f.category == "synthetic_data"]
        assert len(findings) >= 1

    def test_for_testing_only(self):
        code = "# for testing only\nx = 1\n"
        report = scan(code, "testing-only-tool")
        findings = [f for f in report.findings if f.category == "synthetic_data"]
        assert len(findings) >= 1

    def test_synthetic_is_high_not_critical(self):
        """HIGH synthetic findings should not block overall_passed."""
        code = "# TODO: implement\nx = 1\n"
        report = scan(code, "synth-tool")
        assert report.overall_passed is True


# ===================================================================
# Category 5: Missing external calls (MEDIUM)
# ===================================================================

class TestMissingExternalCalls:
    def test_fetch_without_import(self):
        code = '''\
async def fetch_weather(city: str) -> str:
    return f"Weather for {city}"
'''
        report = scan(code, "fetch-no-import")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.MEDIUM

    def test_query_without_import(self):
        code = '''\
def query_database(table: str):
    return []
'''
        report = scan(code, "query-no-import")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) >= 1

    def test_fetch_with_httpx_ok(self):
        code = '''\
import httpx

async def fetch_data(url: str):
    async with httpx.AsyncClient() as c:
        return await c.get(url)
'''
        report = scan(code, "fetch-with-import")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) == 0

    def test_fetch_with_requests_ok(self):
        code = '''\
import requests

def fetch_page(url: str):
    return requests.get(url).text
'''
        report = scan(code, "fetch-requests")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) == 0

    def test_non_external_function_not_flagged(self):
        """Functions that don't have fetch/query/etc in name are fine."""
        code = '''\
def compute_sum(a: int, b: int) -> int:
    return a + b
'''
        report = scan(code, "compute-tool")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) == 0

    def test_missing_external_is_medium(self):
        """MEDIUM findings should not block overall_passed."""
        code = '''\
def retrieve_items():
    return []
'''
        report = scan(code, "retrieve-tool")
        assert report.overall_passed is True


# ===================================================================
# SyntaxError handling
# ===================================================================

class TestSyntaxError:
    def test_syntax_error_reported_as_critical(self):
        code = "def broken(\n"
        report = scan(code, "broken-tool")
        assert not report.overall_passed
        assert report.tier_reached == 1
        findings = [f for f in report.findings if f.category == "syntax"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert "SyntaxError" in findings[0].message

    def test_syntax_error_skips_other_checks(self):
        """On SyntaxError, only the syntax finding is returned."""
        code = "def broken( # TODO mock\n"
        report = scan(code, "broken-tool")
        assert len(report.findings) == 1
        assert report.findings[0].category == "syntax"


# ===================================================================
# Clean code
# ===================================================================

class TestCleanCode:
    def test_clean_code_passes(self):
        report = scan(CLEAN_MCP_TOOL, "weather-tool")
        assert report.overall_passed is True
        assert report.tier_reached == 1
        assert len(report.findings) == 0

    def test_clean_code_empty_findings(self):
        code = '''\
async def compute_factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * await compute_factorial(n - 1)
'''
        report = scan(code, "factorial-tool")
        assert report.findings == []
        assert report.overall_passed is True


# ===================================================================
# Tier 0: Skill structural pre-check
# ===================================================================

class TestSkillPrecheck:
    def test_valid_skill_passes_tier0(self):
        report = scan(VALID_SKILL_MD, "data-loader", capability_type="skill")
        tier0_findings = [f for f in report.findings if f.tier == 0]
        assert len(tier0_findings) == 0
        # Tier 1 should have run (tier_reached >= 1)
        assert report.tier_reached >= 1

    def test_invalid_skill_no_frontmatter(self):
        bad_skill = "# Just a heading\nNo frontmatter here.\n"
        report = scan(bad_skill, "bad-skill", capability_type="skill")
        assert not report.overall_passed
        assert report.tier_reached == 0
        tier0_findings = [f for f in report.findings if f.tier == 0]
        assert len(tier0_findings) == 1
        assert tier0_findings[0].severity == Severity.CRITICAL
        assert tier0_findings[0].category == "skill_structure"

    def test_skill_missing_name(self):
        bad_skill = """\
---
description: "A skill without a name."
---

# No Name Skill
"""
        report = scan(bad_skill, "no-name-skill", capability_type="skill")
        assert not report.overall_passed
        assert report.tier_reached == 0

    def test_mcp_tool_skips_tier0(self):
        """Non-skill types should not run the Tier 0 pre-check."""
        bad_skill_content = "# Not a skill\nNo frontmatter.\n"
        report = scan(bad_skill_content, "not-a-skill", capability_type="mcp_tool")
        tier0_findings = [f for f in report.findings if f.tier == 0]
        assert len(tier0_findings) == 0
        assert report.tier_reached == 1


# ===================================================================
# should_proceed_to_tier2
# ===================================================================

class TestShouldProceed:
    def test_no_findings_proceeds(self):
        report = scan(CLEAN_MCP_TOOL, "clean-tool")
        assert should_proceed_to_tier2(report) is True

    def test_critical_blocks(self):
        code = 'mock_data = 42\n'
        report = scan(code, "mock-tool")
        assert should_proceed_to_tier2(report) is False

    def test_high_only_proceeds(self):
        code = "# TODO: implement\nx = 1\n"
        report = scan(code, "todo-tool")
        assert should_proceed_to_tier2(report) is True

    def test_medium_only_proceeds(self):
        code = '''\
def retrieve_items():
    return []
'''
        report = scan(code, "retrieve-tool")
        assert should_proceed_to_tier2(report) is True


# ===================================================================
# Report structure
# ===================================================================

class TestReportStructure:
    def test_report_has_capability_name(self):
        report = scan("x = 1\n", "my-tool")
        assert report.capability_name == "my-tool"

    def test_report_tier_reached_is_1_for_mcp_tool(self):
        report = scan("x = 1\n", "my-tool")
        assert report.tier_reached == 1

    def test_report_verified_at_populated(self):
        report = scan("x = 1\n", "my-tool")
        assert report.verified_at is not None

    def test_multiple_categories_combined(self):
        """Code with multiple issues produces findings for each."""
        code = '''\
# TODO: fix this
mock_result = "hello"

def fetch_data():
    try:
        pass
    except:
        pass
    return ["a", "b", "c"]
'''
        report = scan(code, "multi-issue-tool")
        categories = {f.category for f in report.findings}
        assert "synthetic_data" in categories
        assert "mock_artifact" in categories
        assert "silent_exception" in categories
        assert "hardcoded_data" in categories
        assert "missing_external_calls" in categories


# ===================================================================
# scan_text_only — regex-only scan for non-Python content
# ===================================================================

class TestScanTextOnly:
    def test_clean_text_returns_empty_findings(self):
        text = "---\nname: my-skill\ndescription: Loads CSV data from disk.\n---\n\n## Overview\n\nThis skill reads files.\n"
        report = scan_text_only(text, "my-skill")
        assert report.findings == []
        assert report.overall_passed is True
        assert report.tier_reached == 1

    def test_mock_indicator_detected_as_critical(self):
        text = "---\nname: my-skill\ndescription: Loads data.\n---\n\nmock_result = [1, 2, 3]\n"
        report = scan_text_only(text, "my-skill")
        mock_findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(mock_findings) >= 1
        assert all(f.severity.value == "critical" for f in mock_findings)
        assert report.overall_passed is False

    def test_synthetic_comment_detected_as_high(self):
        text = "# TODO: implement real data fetching\nUse this skill for analysis.\n"
        report = scan_text_only(text, "todo-skill")
        synthetic = [f for f in report.findings if f.category == "synthetic_data"]
        assert len(synthetic) >= 1
        assert all(f.severity.value == "high" for f in synthetic)

    def test_no_ast_syntax_error_for_non_python(self):
        """YAML/markdown content must not produce a syntax error finding."""
        yaml_text = "---\nname: test-skill\ndescription: A skill.\n---\n\n## Body\n\nSome markdown content.\n"
        report = scan_text_only(yaml_text, "test-skill")
        syntax_findings = [f for f in report.findings if f.category == "syntax"]
        assert len(syntax_findings) == 0

    def test_tier_reached_is_1(self):
        report = scan_text_only("some plain text", "text-cap")
        assert report.tier_reached == 1

    def test_capability_name_in_report(self):
        report = scan_text_only("content", "cap-name")
        assert report.capability_name == "cap-name"

    def test_json_config_with_mock_detected(self):
        """JSON agent config containing 'mock' is caught by regex scan."""
        import json
        config = json.dumps({"system_prompt": "You are a mock testing agent."})
        report = scan_text_only(config, "agent-cap")
        assert report.overall_passed is False
        assert any(f.category == "mock_artifact" for f in report.findings)

    def test_empty_text_returns_clean_report(self):
        report = scan_text_only("", "empty-cap")
        assert report.findings == []
        assert report.overall_passed is True