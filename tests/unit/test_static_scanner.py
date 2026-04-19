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
from src.verification.static_scanner import scan, should_proceed_to_tier2

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
    def test_mock_standalone(self):
        """'mock' as a standalone word is flagged."""
        code = 'x = mock\n'
        report = scan(code, "mock-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_mock_in_compound_word_not_flagged(self):
        """'mock' inside a compound like 'mock_data' is NOT flagged (H4 fix)."""
        code = "mock_data = [1, 2, 3]\n"
        report = scan(code, "mock-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) == 0

    def test_fake_standalone_in_string(self):
        code = 'result = "This is fake data"\n'
        report = scan(code, "fake-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_placeholder_standalone(self):
        """'placeholder' as a standalone word is flagged."""
        code = 'x = "placeholder"\n'
        report = scan(code, "placeholder-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_placeholder_in_compound_not_flagged(self):
        """'placeholder' inside 'placeholder_value' is NOT flagged (H4 fix)."""
        code = "placeholder_value = 42\n"
        report = scan(code, "placeholder-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) == 0

    def test_dummy_standalone(self):
        code = 'x = "dummy"\n'
        report = scan(code, "dummy-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_test_data_detected(self):
        code = "TEST_DATA = [1, 2, 3]\n"
        report = scan(code, "testdata-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_demo_standalone(self):
        """'DEMO' as a standalone word is flagged."""
        code = 'mode = "DEMO"\n'
        report = scan(code, "demo-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) >= 1

    def test_demo_in_compound_not_flagged(self):
        """'DEMO' inside 'DEMO_MODE' is NOT flagged (H4 fix: no more
        false positives on 'demographics', 'remediation', etc.)."""
        code = 'DEMO_MODE = True\n'
        report = scan(code, "demo-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) == 0

    def test_demographics_not_flagged(self):
        """H4: 'demographics' must NOT be flagged."""
        code = 'demographics_data = load("demographics.csv")\n'
        report = scan(code, "demographics-tool")
        findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(findings) == 0

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
        """Default (network disabled): severity is MEDIUM and overall_passed stays True.

        Consolidates two earlier tests (one asserting overall_passed, one asserting
        severity) — both fired the same underlying ``missing_external_calls`` path
        with MEDIUM default severity.
        """
        code = '''\
def retrieve_items():
    return []
'''
        report = scan(code, "retrieve-tool")
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert report.overall_passed is True

    def test_missing_external_is_high_with_network(self):
        """When allow_network=True: severity is HIGH."""
        code = 'def fetch_data(): return []\n'
        report = scan(code, "fetch-tool", allow_network=True)
        findings = [f for f in report.findings if f.category == "missing_external_calls"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH


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
        code = 'x = "mock"\n'
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
result = "mock"

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
# scan with non-MCP types — regex-only scan for non-Python content
# (formerly scan_text_only, now handled by scan() with capability_type)
# ===================================================================

class TestScanNonMcpTypes:
    def test_clean_text_returns_empty_findings(self):
        text = "---\nname: my-skill\ndescription: Loads CSV data from disk.\n---\n\n## Overview\n\nThis skill reads files.\n"
        report = scan(text, "my-skill", "skill")
        assert report.findings == []
        assert report.overall_passed is True
        assert report.tier_reached == 1

    def test_mock_word_in_skill_prose_not_flagged(self):
        """Words like 'mock' / 'placeholder' inside SKILL.md prose must NOT be
        flagged as mock_artifact findings. Skills are natural-language
        instructions where these words appear legitimately (e.g., "do not
        leave placeholder values", "this is not a mock implementation").
        Mock detection is scoped to MCP_TOOL Python source only.
        """
        text = "---\nname: my-skill\ndescription: Loads data.\n---\n\nThis is a mock of the real API.\n"
        report = scan(text, "my-skill", "skill")
        mock_findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert mock_findings == []
        assert report.overall_passed is True

    def test_todo_comment_in_agent_prose_not_flagged(self):
        """`# TODO` in agent config text must NOT be flagged. Agent system
        prompts and JSON config are not Python source, so the synthetic-data
        regex (which targets `# TODO` / `# FIXME` code comments) does not
        apply.
        """
        text = "# TODO: implement real data fetching\nUse this for analysis.\n"
        report = scan(text, "todo-agent", "agent")
        synthetic = [f for f in report.findings if f.category == "synthetic_data"]
        assert synthetic == []

    def test_no_ast_syntax_error_for_non_python(self):
        """YAML/markdown content must not produce a syntax error finding."""
        yaml_text = "---\nname: test-skill\ndescription: A skill.\n---\n\n## Body\n\nSome markdown content.\n"
        report = scan(yaml_text, "test-skill", "skill")
        syntax_findings = [f for f in report.findings if f.category == "syntax"]
        assert len(syntax_findings) == 0

    def test_tier_reached_is_1(self):
        report = scan("some plain text", "text-cap", "agent")
        assert report.tier_reached == 1

    def test_capability_name_in_report(self):
        """Non-MCP scan preserves capability_name, tier_reached=1, and empty findings.

        Complements ``TestReportStructure::test_report_has_capability_name`` which
        covers the MCP_TOOL path — this one guards the SKILL/AGENT branch where
        the scanner short-circuits before running AST+regex.
        """
        report = scan("content", "cap-name", "agent")
        assert report.capability_name == "cap-name"
        assert report.tier_reached == 1
        assert report.findings == []
        assert report.overall_passed is True

    def test_json_agent_config_with_mock_word_not_flagged(self):
        """JSON agent config containing the word 'mock' in the system_prompt
        must NOT be flagged. Agent prompts can legitimately reference mocking
        as a domain concept (e.g., "you are a testing agent that creates
        mock responses for review"). Mock detection is MCP_TOOL only.
        """
        import json
        config = json.dumps({"system_prompt": "You are a mock testing agent."})
        report = scan(config, "agent-cap", "agent")
        assert report.overall_passed is True
        assert [f for f in report.findings if f.category == "mock_artifact"] == []

    def test_empty_text_returns_clean_report(self):
        report = scan("", "empty-cap", "agent")
        assert report.findings == []
        assert report.overall_passed is True

    def test_skill_with_placeholder_in_instructions_passes(self):
        """Regression for T04 false positive: skills routinely use the word
        'placeholder' in their instructions (e.g., "do not leave placeholder
        values in the output"). The Tier 1 mock-artifact regex was firing on
        these legitimate prose uses and rejecting otherwise-valid skills.
        """
        skill = (
            "---\n"
            "name: inventory-reorder\n"
            "description: Build an inventory reorder pipeline.\n"
            "---\n\n"
            "## Instructions\n\n"
            "Compute reorder quantities. Do not leave placeholder values in\n"
            "the output. Replace any placeholder fields with real computed\n"
            "values before returning.\n"
        )
        report = scan(skill, "inventory-reorder", "skill")
        assert report.overall_passed is True
        assert [f for f in report.findings if f.category == "mock_artifact"] == []

    def test_skill_with_mock_word_in_instructions_passes(self):
        """Regression for T04: 'mock' as a domain term in skill prose must
        not block verification. Skills often reference mocking as a concept
        (e.g., "this is not a mock — call the real API")."""
        skill = (
            "---\n"
            "name: data-fetcher\n"
            "description: Fetch real data from the source.\n"
            "---\n\n"
            "## Important\n\n"
            "This skill must call the real API. Do not return mock data.\n"
        )
        report = scan(skill, "data-fetcher", "skill")
        assert report.overall_passed is True
        assert [f for f in report.findings if f.category == "mock_artifact"] == []

    def test_mcp_tool_mock_detection_still_works(self):
        """Sanity check: the MCP_TOOL path still flags mock literals.
        Scoping the regex to MCP_TOOL must not regress mock detection on
        actual Python source.
        """
        code = 'def get_data():\n    return "placeholder"\n'
        report = scan(code, "fake-tool", "mcp_tool")
        mock_findings = [f for f in report.findings if f.category == "mock_artifact"]
        assert len(mock_findings) >= 1
        assert mock_findings[0].severity.value == "critical"


# ===================================================================
# A1: Mock library imports (CRITICAL)
# ===================================================================

class TestMockImports:
    def test_import_unittest_mock(self):
        code = "import unittest.mock\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL
        assert "unittest.mock" in findings[0].message

    def test_from_unittest_mock(self):
        code = "from unittest.mock import MagicMock\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_import_mock_root(self):
        code = "import mock\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) >= 1

    def test_import_faker(self):
        code = "import faker\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_from_faker_import(self):
        code = "from faker import Faker\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) >= 1

    def test_legitimate_import_not_flagged(self):
        code = "import requests\nimport json\n"
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "mock_import"]
        assert len(findings) == 0

    def test_mock_import_blocks_overall_pass(self):
        code = "import unittest.mock\n"
        report = scan(code, "tool")
        assert report.overall_passed is False


# ===================================================================
# A1: random.seed(<literal>) determinism artifact (HIGH)
# ===================================================================

class TestRandomSeed:
    def test_random_seed_literal(self):
        code = '''\
import random
def get_value():
    random.seed(42)
    return random.randint(0, 100)
'''
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "determinism_artifact"]
        assert len(findings) >= 1
        assert findings[0].severity == Severity.HIGH

    def test_np_random_seed_literal(self):
        code = '''\
import numpy as np
def get_value():
    np.random.seed(7)
    return np.random.rand()
'''
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "determinism_artifact"]
        assert len(findings) >= 1

    def test_numpy_random_seed_literal(self):
        code = '''\
import numpy
def get_value():
    numpy.random.seed(7)
    return numpy.random.rand()
'''
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "determinism_artifact"]
        assert len(findings) >= 1

    def test_random_seed_dynamic_not_flagged(self):
        code = '''\
import random
import time
def get_value():
    random.seed(time.time())
    return random.randint(0, 100)
'''
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "determinism_artifact"]
        assert len(findings) == 0

    def test_no_seed_call_not_flagged(self):
        code = '''\
import random
def get_value():
    return random.randint(0, 100)
'''
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "determinism_artifact"]
        assert len(findings) == 0


# ===================================================================
# A1: Lorem ipsum placeholder text (HIGH)
# ===================================================================

class TestLoremIpsum:
    def test_lorem_ipsum_in_string_literal(self):
        code = 'def get_text():\n    return "Lorem ipsum dolor sit amet"\n'
        report = scan(code, "tool")
        findings = [f for f in report.findings if f.category == "synthetic_data"]
        # At least one finding should mention lorem ipsum
        assert any("Lorem ipsum" in f.message or "lorem ipsum" in f.message.lower() for f in findings)

    def test_lorem_ipsum_case_insensitive(self):
        code = 'def get_text():\n    return "LOREM IPSUM"\n'
        report = scan(code, "tool")
        findings = [
            f for f in report.findings
            if f.category == "synthetic_data" and "lorem" in f.message.lower()
        ]
        assert len(findings) >= 1

    def test_legitimate_text_not_flagged_for_lorem(self):
        code = 'def get_text():\n    return "Hello world"\n'
        report = scan(code, "tool")
        findings = [
            f for f in report.findings
            if f.category == "synthetic_data" and "lorem" in f.message.lower()
        ]
        assert len(findings) == 0