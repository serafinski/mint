import pytest

from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.metrics import GranularityMetrics, InvocationRecord
from src.schemas.task import (
    CapabilityReference,
    ChainResult,
    ClassificationResult,
    ClassifiedGap,
    DecompositionResult,
    GapItem,
    GeneratedArtifact,
    GeneratorInput,
    MatchItem,
    SubTask,
    SubTaskItem,
    TaskState,
    TaskStatus,
)
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

# --- CapabilityType ---


def test_capability_type_values():
    assert CapabilityType.MCP_TOOL.value == "mcp_tool"
    assert CapabilityType.SKILL.value == "skill"
    assert CapabilityType.AGENT.value == "agent"


# --- CapabilityDescriptor ---


def test_capability_descriptor_valid():
    cap = CapabilityDescriptor(
        name="fibonacci",
        description="Compute fibonacci numbers",
        capability_type=CapabilityType.MCP_TOOL,
    )
    assert cap.name == "fibonacci"
    assert cap.capability_type == CapabilityType.MCP_TOOL
    assert cap.version == 1


def test_capability_descriptor_rejects_extra_fields():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type=CapabilityType.MCP_TOOL,
            unknown_field="oops",
        )


def test_capability_descriptor_rejects_invalid_type():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type="not_a_real_type",
        )


def test_capability_descriptor_requires_name():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            description="test",
            capability_type=CapabilityType.SKILL,
        )


def test_capability_descriptor_roundtrip():
    cap = CapabilityDescriptor(
        name="weather",
        description="Fetch weather data",
        capability_type=CapabilityType.MCP_TOOL,
    )
    roundtripped = CapabilityDescriptor.model_validate_json(cap.model_dump_json())
    assert roundtripped.name == cap.name
    assert roundtripped.capability_type == cap.capability_type


def test_capability_descriptor_new_fields_defaults():
    """New fields should have defaults for backward compatibility."""
    cap = CapabilityDescriptor(
        name="test",
        description="test",
        capability_type=CapabilityType.MCP_TOOL,
    )
    assert cap.input_schema == {}
    assert cap.output_schema == {}
    assert cap.tags == []
    assert cap.domain == "general"
    assert cap.is_active is True
    assert cap.metadata == {}


def test_capability_descriptor_new_fields_populated():
    cap = CapabilityDescriptor(
        name="csv-parser",
        description="Parse CSV files",
        capability_type=CapabilityType.MCP_TOOL,
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        output_schema={"type": "array"},
        tags=["data", "parsing", "csv"],
        domain="data-engineering",
        is_active=False,
        metadata={"author": "system", "source": "generated"},
    )
    assert cap.input_schema["type"] == "object"
    assert cap.tags == ["data", "parsing", "csv"]
    assert cap.domain == "data-engineering"
    assert cap.is_active is False
    assert cap.metadata["author"] == "system"


def test_capability_descriptor_roundtrip_with_new_fields():
    cap = CapabilityDescriptor(
        name="enricher",
        description="Enrich data",
        capability_type=CapabilityType.SKILL,
        tags=["ml", "enrichment"],
        domain="ml",
        metadata={"version": "2"},
    )
    roundtripped = CapabilityDescriptor.model_validate_json(cap.model_dump_json())
    assert roundtripped.tags == cap.tags
    assert roundtripped.domain == cap.domain
    assert roundtripped.metadata == cap.metadata


# --- TaskStatus ---


def test_task_status_values():
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.DECOMPOSING.value == "decomposing"
    assert TaskStatus.DETECTING_GAPS.value == "detecting_gaps"
    assert TaskStatus.CLASSIFYING.value == "classifying"
    assert TaskStatus.GENERATING.value == "generating"
    assert TaskStatus.VERIFYING.value == "verifying"
    assert TaskStatus.EXECUTING.value == "executing"
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"


def test_task_status_invalid():
    with pytest.raises(ValueError):
        TaskStatus("not_a_status")


# --- SubTask ---


def test_subtask_valid():
    st = SubTask(
        description="Load CSV data",
        required_capabilities=["csv-parser"],
    )
    assert st.description == "Load CSV data"
    assert st.required_capabilities == ["csv-parser"]
    assert st.depends_on == []
    assert st.status == TaskStatus.PENDING


def test_subtask_with_dependencies():
    st = SubTask(
        description="Train model",
        required_capabilities=["ml-trainer"],
        depends_on=[0, 1],
        status=TaskStatus.EXECUTING,
    )
    assert st.depends_on == [0, 1]
    assert st.status == TaskStatus.EXECUTING


def test_subtask_rejects_extra():
    with pytest.raises(ValueError):
        SubTask(
            description="test",
            required_capabilities=["x"],
            bonus="bad",
        )


def test_subtask_roundtrip():
    st = SubTask(description="test", required_capabilities=["a", "b"])
    roundtripped = SubTask.model_validate_json(st.model_dump_json())
    assert roundtripped.description == st.description
    assert roundtripped.required_capabilities == st.required_capabilities


# --- ClassifiedGap ---


def test_classified_gap_valid():
    cg = ClassifiedGap(
        description="Need a CSV parser",
        query="parse csv files",
        classified_type=CapabilityType.MCP_TOOL,
        confidence=0.85,
    )
    assert cg.classified_type == CapabilityType.MCP_TOOL
    assert cg.confidence == 0.85
    assert cg.rationale == ""


def test_classified_gap_with_rationale():
    cg = ClassifiedGap(
        description="Need data analysis agent",
        query="analyze sales data",
        classified_type=CapabilityType.AGENT,
        confidence=0.72,
        rationale="Requires multi-step reasoning over data",
    )
    assert cg.rationale == "Requires multi-step reasoning over data"


def test_classified_gap_confidence_bounds():
    with pytest.raises(ValueError):
        ClassifiedGap(
            description="test",
            query="test",
            classified_type=CapabilityType.SKILL,
            confidence=1.5,
        )
    with pytest.raises(ValueError):
        ClassifiedGap(
            description="test",
            query="test",
            classified_type=CapabilityType.SKILL,
            confidence=-0.1,
        )


def test_classified_gap_rejects_extra():
    with pytest.raises(ValueError):
        ClassifiedGap(
            description="test",
            query="test",
            classified_type=CapabilityType.SKILL,
            confidence=0.5,
            extra_field="bad",
        )


def test_classified_gap_roundtrip():
    cg = ClassifiedGap(
        description="test",
        query="test",
        classified_type=CapabilityType.MCP_TOOL,
        confidence=0.9,
    )
    roundtripped = ClassifiedGap.model_validate_json(cg.model_dump_json())
    assert roundtripped.classified_type == cg.classified_type
    assert roundtripped.confidence == cg.confidence


# --- GeneratedArtifact ---


def test_generated_artifact_mcp_tool():
    art = GeneratedArtifact(
        capability_name="csv-parser",
        capability_type=CapabilityType.MCP_TOOL,
        source_code="def parse_csv(): ...",
    )
    assert art.source_code == "def parse_csv(): ..."
    assert art.skill_content is None
    assert art.agent_config is None
    assert art.generated_at is not None


def test_generated_artifact_skill():
    art = GeneratedArtifact(
        capability_name="data-cleaner",
        capability_type=CapabilityType.SKILL,
        skill_content="---\nname: data-cleaner\n---\n# Data Cleaner",
    )
    assert art.skill_content is not None
    assert art.source_code is None


def test_generated_artifact_agent():
    art = GeneratedArtifact(
        capability_name="analyst",
        capability_type=CapabilityType.AGENT,
        agent_config={"system_prompt": "You are a data analyst.", "model": "gpt-5-mini"},
    )
    assert art.agent_config["model"] == "gpt-5-mini"


def test_generated_artifact_rejects_extra():
    with pytest.raises(ValueError):
        GeneratedArtifact(
            capability_name="test",
            capability_type=CapabilityType.MCP_TOOL,
            bonus="bad",
        )


def test_generated_artifact_roundtrip():
    art = GeneratedArtifact(
        capability_name="test",
        capability_type=CapabilityType.SKILL,
        skill_content="# Test",
    )
    roundtripped = GeneratedArtifact.model_validate_json(art.model_dump_json())
    assert roundtripped.capability_name == art.capability_name
    assert roundtripped.skill_content == art.skill_content


def test_generated_artifact_description_field():
    """description field is stored and None by default (Bug 1 fix)."""
    art_no_desc = GeneratedArtifact(
        capability_name="add-numbers",
        capability_type=CapabilityType.MCP_TOOL,
        source_code="pass",
    )
    assert art_no_desc.description is None

    art_with_desc = GeneratedArtifact(
        capability_name="add-numbers",
        capability_type=CapabilityType.MCP_TOOL,
        source_code="pass",
        description="Adds two integers",
    )
    assert art_with_desc.description == "Adds two integers"


# --- Severity ---


def test_severity_values():
    assert Severity.CRITICAL.value == "critical"
    assert Severity.HIGH.value == "high"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.LOW.value == "low"
    assert Severity.INFO.value == "info"


def test_severity_invalid():
    with pytest.raises(ValueError):
        Severity("fatal")


# --- VerificationFinding ---


def test_verification_finding_valid():
    f = VerificationFinding(
        tier=1,
        category="security",
        severity=Severity.CRITICAL,
        message="Uses eval() on user input",
    )
    assert f.tier == 1
    assert f.severity == Severity.CRITICAL
    assert f.location is None


def test_verification_finding_with_location():
    f = VerificationFinding(
        tier=2,
        category="correctness",
        severity=Severity.MEDIUM,
        message="Edge case not handled",
        location="line 42",
    )
    assert f.location == "line 42"


def test_verification_finding_tier_bounds():
    # tier=0 is valid (Tier 0 = structural pre-check for skills, runs before Tier 1)
    f = VerificationFinding(
        tier=0,
        category="structural",
        severity=Severity.CRITICAL,
        message="quick_validate.py check failed",
    )
    assert f.tier == 0
    # tier=4 is above the maximum
    with pytest.raises(ValueError):
        VerificationFinding(
            tier=4,
            category="test",
            severity=Severity.LOW,
            message="test",
        )


def test_verification_finding_rejects_extra():
    with pytest.raises(ValueError):
        VerificationFinding(
            tier=1,
            category="test",
            severity=Severity.LOW,
            message="test",
            bonus="bad",
        )


def test_verification_finding_roundtrip():
    f = VerificationFinding(
        tier=3,
        category="adversarial",
        severity=Severity.HIGH,
        message="Prompt injection vulnerability",
    )
    roundtripped = VerificationFinding.model_validate_json(f.model_dump_json())
    assert roundtripped.tier == f.tier
    assert roundtripped.severity == f.severity


# --- VerificationReport ---


def test_verification_report_valid():
    r = VerificationReport(
        capability_name="csv-parser",
        findings=[],
        overall_passed=True,
        tier_reached=3,
    )
    assert r.overall_passed is True
    assert r.tier_reached == 3
    assert r.verified_at is not None


def test_verification_report_with_findings():
    finding = VerificationFinding(
        tier=1,
        category="security",
        severity=Severity.CRITICAL,
        message="Dangerous import",
    )
    r = VerificationReport(
        capability_name="bad-tool",
        findings=[finding],
        overall_passed=False,
        tier_reached=1,
    )
    assert len(r.findings) == 1
    assert r.overall_passed is False


def test_verification_report_tier_reached_bounds():
    with pytest.raises(ValueError):
        VerificationReport(
            capability_name="test",
            findings=[],
            overall_passed=True,
            tier_reached=-1,
        )
    with pytest.raises(ValueError):
        VerificationReport(
            capability_name="test",
            findings=[],
            overall_passed=True,
            tier_reached=4,
        )


def test_verification_report_rejects_extra():
    with pytest.raises(ValueError):
        VerificationReport(
            capability_name="test",
            findings=[],
            overall_passed=True,
            tier_reached=2,
            bonus="bad",
        )


def test_verification_report_roundtrip():
    finding = VerificationFinding(
        tier=2,
        category="correctness",
        severity=Severity.MEDIUM,
        message="test failure",
    )
    r = VerificationReport(
        capability_name="test-tool",
        findings=[finding],
        overall_passed=False,
        tier_reached=2,
    )
    roundtripped = VerificationReport.model_validate_json(r.model_dump_json())
    assert roundtripped.capability_name == r.capability_name
    assert len(roundtripped.findings) == 1
    assert roundtripped.findings[0].severity == Severity.MEDIUM


# --- InvocationRecord ---


def test_invocation_record_valid():
    rec = InvocationRecord(
        capability_id="cap-123",
        task_id="task-456",
        success=True,
        token_cost=150,
        latency_ms=45.2,
    )
    assert rec.success is True
    assert rec.error_message is None
    assert rec.invoked_at is not None


def test_invocation_record_with_error():
    rec = InvocationRecord(
        capability_id="cap-123",
        task_id="task-456",
        success=False,
        token_cost=50,
        latency_ms=10.0,
        error_message="Timeout exceeded",
    )
    assert rec.error_message == "Timeout exceeded"


def test_invocation_record_token_cost_non_negative():
    with pytest.raises(ValueError):
        InvocationRecord(
            capability_id="cap-123",
            task_id="task-456",
            success=True,
            token_cost=-1,
            latency_ms=10.0,
        )


def test_invocation_record_latency_non_negative():
    with pytest.raises(ValueError):
        InvocationRecord(
            capability_id="cap-123",
            task_id="task-456",
            success=True,
            token_cost=100,
            latency_ms=-5.0,
        )


def test_invocation_record_rejects_extra():
    with pytest.raises(ValueError):
        InvocationRecord(
            capability_id="cap-123",
            task_id="task-456",
            success=True,
            token_cost=100,
            latency_ms=10.0,
            bonus="bad",
        )


def test_invocation_record_roundtrip():
    rec = InvocationRecord(
        capability_id="cap-123",
        task_id="task-456",
        success=True,
        token_cost=200,
        latency_ms=30.5,
    )
    roundtripped = InvocationRecord.model_validate_json(rec.model_dump_json())
    assert roundtripped.capability_id == rec.capability_id
    assert roundtripped.token_cost == rec.token_cost


# --- GranularityMetrics ---


def test_granularity_metrics_valid():
    gm = GranularityMetrics(
        capability_id="cap-123",
        reuse_rate=0.75,
        success_rate=0.92,
        token_cost=150.0,
        maintenance_burden=0.3,
        window_size=100,
    )
    assert gm.reuse_rate == 0.75
    assert gm.computed_at is not None


def test_granularity_metrics_rate_bounds():
    with pytest.raises(ValueError):
        GranularityMetrics(
            capability_id="cap-123",
            reuse_rate=1.5,
            success_rate=0.5,
            token_cost=100.0,
            maintenance_burden=0.1,
            window_size=50,
        )
    with pytest.raises(ValueError):
        GranularityMetrics(
            capability_id="cap-123",
            reuse_rate=0.5,
            success_rate=-0.1,
            token_cost=100.0,
            maintenance_burden=0.1,
            window_size=50,
        )


def test_granularity_metrics_token_cost_non_negative():
    with pytest.raises(ValueError):
        GranularityMetrics(
            capability_id="cap-123",
            reuse_rate=0.5,
            success_rate=0.5,
            token_cost=-10.0,
            maintenance_burden=0.1,
            window_size=50,
        )


def test_granularity_metrics_maintenance_burden_non_negative():
    with pytest.raises(ValueError):
        GranularityMetrics(
            capability_id="cap-123",
            reuse_rate=0.5,
            success_rate=0.5,
            token_cost=100.0,
            maintenance_burden=-0.5,
            window_size=50,
        )


def test_granularity_metrics_rejects_extra():
    with pytest.raises(ValueError):
        GranularityMetrics(
            capability_id="cap-123",
            reuse_rate=0.5,
            success_rate=0.5,
            token_cost=100.0,
            maintenance_burden=0.1,
            window_size=50,
            bonus="bad",
        )


def test_granularity_metrics_roundtrip():
    gm = GranularityMetrics(
        capability_id="cap-123",
        reuse_rate=0.8,
        success_rate=0.95,
        token_cost=200.0,
        maintenance_burden=0.15,
        window_size=200,
    )
    roundtripped = GranularityMetrics.model_validate_json(gm.model_dump_json())
    assert roundtripped.capability_id == gm.capability_id
    assert roundtripped.reuse_rate == gm.reuse_rate
    assert roundtripped.window_size == gm.window_size


# --- TaskState (expanded) ---


def test_task_state_minimal():
    state = TaskState(task_description="compute fibonacci")
    assert state.task_description == "compute fibonacci"
    assert state.required_capabilities == []
    assert state.matches == []
    assert state.gaps == []


def test_task_state_backward_compat():
    """All new fields have defaults, so old-style construction still works."""
    state = TaskState(task_description="test task")
    assert state.subtasks == []
    assert state.classified_gaps == []
    assert state.generated_artifacts == []
    assert state.verification_results == []
    assert state.status == TaskStatus.PENDING
    assert state.errors == []
    assert state.trace_id is None


def test_task_state_rejects_extra_fields():
    with pytest.raises(ValueError):
        TaskState(task_description="test", bogus="field")


def test_task_state_with_matches_and_gaps():
    cap = CapabilityDescriptor(
        name="fib",
        description="fibonacci",
        capability_type=CapabilityType.MCP_TOOL,
    )
    state = TaskState(
        task_description="compute stuff",
        required_capabilities=["fibonacci", "weather"],
        matches=[MatchItem(capability=cap, score=0.85)],
        gaps=[GapItem(description="weather data", query="fetch weather")],
    )
    assert len(state.matches) == 1
    assert state.matches[0].score == 0.85
    assert len(state.gaps) == 1


def test_task_state_full_pipeline():
    """Test TaskState with all new fields populated."""
    cap = CapabilityDescriptor(
        name="csv-parser",
        description="Parse CSV files",
        capability_type=CapabilityType.MCP_TOOL,
    )
    finding = VerificationFinding(
        tier=1,
        category="security",
        severity=Severity.LOW,
        message="All clear",
    )
    report = VerificationReport(
        capability_name="csv-parser",
        findings=[finding],
        overall_passed=True,
        tier_reached=3,
    )
    state = TaskState(
        task_description="Build analytics pipeline",
        required_capabilities=["csv-parser", "ml-trainer"],
        matches=[MatchItem(capability=cap, score=0.9)],
        gaps=[GapItem(description="ML training", query="train model")],
        subtasks=[
            SubTask(description="Load data", required_capabilities=["csv-parser"]),
            SubTask(
                description="Train model",
                required_capabilities=["ml-trainer"],
                depends_on=[0],
            ),
        ],
        classified_gaps=[
            ClassifiedGap(
                description="ML training",
                query="train model",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.8,
            ),
        ],
        generated_artifacts=[
            GeneratedArtifact(
                capability_name="ml-trainer",
                capability_type=CapabilityType.MCP_TOOL,
                source_code="def train(): ...",
            ),
        ],
        verification_results=[report],
        status=TaskStatus.COMPLETED,
        errors=[],
        trace_id="trace-abc-123",
    )
    assert len(state.subtasks) == 2
    assert state.subtasks[1].depends_on == [0]
    assert len(state.classified_gaps) == 1
    assert len(state.generated_artifacts) == 1
    assert len(state.verification_results) == 1
    assert state.status == TaskStatus.COMPLETED
    assert state.trace_id == "trace-abc-123"


def test_task_state_roundtrip():
    state = TaskState(
        task_description="test",
        status=TaskStatus.GENERATING,
        errors=["something went wrong"],
        trace_id="trace-xyz",
    )
    roundtripped = TaskState.model_validate_json(state.model_dump_json())
    assert roundtripped.status == TaskStatus.GENERATING
    assert roundtripped.errors == ["something went wrong"]
    assert roundtripped.trace_id == "trace-xyz"


def test_task_state_attempt_count_default():
    state = TaskState(task_description="test")
    assert state.attempt_count == 0


def test_task_state_attempt_count_set():
    state = TaskState(task_description="test", attempt_count=2)
    assert state.attempt_count == 2


def test_task_state_attempt_count_rejects_negative():
    with pytest.raises(ValueError):
        TaskState(task_description="test", attempt_count=-1)


def test_task_state_attempt_count_roundtrip():
    state = TaskState(task_description="test", attempt_count=3)
    roundtripped = TaskState.model_validate_json(state.model_dump_json())
    assert roundtripped.attempt_count == 3


# --- GapItem / MatchItem ---


def test_gap_item_rejects_extra():
    with pytest.raises(ValueError):
        GapItem(description="test", query="test", extra="bad")


def test_match_item_requires_capability():
    with pytest.raises(ValueError):
        MatchItem(score=0.5)


# --- SubTaskItem ---


def test_subtask_item_rejects_extra():
    with pytest.raises(ValueError):
        SubTaskItem(
            description="Do something",
            required_capabilities=["cap-a"],
            unknown_field="oops",
        )


# --- DecompositionResult ---


def test_decomposition_result_rejects_extra():
    with pytest.raises(ValueError):
        DecompositionResult(
            subtasks=[],
            reasoning="test",
            unknown_field="oops",
        )


# --- ClassificationResult ---


def test_classification_result_rejects_extra():
    with pytest.raises(ValueError):
        ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="test-cap",
            unknown_field="oops",
        )


# --- GeneratorInput ---


def test_generator_input_valid():
    gap = ClassifiedGap(
        description="Parse CSV",
        query="parse CSV",
        classified_type=CapabilityType.MCP_TOOL,
        confidence=0.9,
        rationale="Atomic operation",
    )
    gi = GeneratorInput(gap=gap, task_context="Analyse sales data")
    assert gi.gap.classified_type == CapabilityType.MCP_TOOL
    assert gi.task_context == "Analyse sales data"


def test_generator_input_default_context():
    gap = ClassifiedGap(
        description="Sort numbers",
        query="sort numbers",
        classified_type=CapabilityType.MCP_TOOL,
        confidence=0.9,
        rationale="Deterministic",
    )
    gi = GeneratorInput(gap=gap)
    assert gi.task_context == ""


def test_generator_input_rejects_extra():
    gap = ClassifiedGap(
        description="Sort numbers",
        query="sort numbers",
        classified_type=CapabilityType.MCP_TOOL,
        confidence=0.9,
        rationale="Deterministic",
    )
    with pytest.raises(ValueError):
        GeneratorInput(gap=gap, unknown_field="oops")


def test_generator_input_roundtrip():
    gap = ClassifiedGap(
        description="Data pipeline",
        query="data pipeline",
        classified_type=CapabilityType.SKILL,
        confidence=0.85,
        rationale="Multi-step",
    )
    gi = GeneratorInput(gap=gap, task_context="Build analytics")
    roundtripped = GeneratorInput.model_validate_json(gi.model_dump_json())
    assert roundtripped.gap.classified_type == CapabilityType.SKILL
    assert roundtripped.task_context == "Build analytics"


# --- CapabilityReference (Stage 1 compositional) ---


def test_capability_reference_registry_source():
    ref = CapabilityReference(
        source="registry",
        capability_name="csv-parser",
        capability_type=CapabilityType.MCP_TOOL,
        score=0.87,
    )
    assert ref.source == "registry"
    assert ref.capability_name == "csv-parser"
    assert ref.score == 0.87


def test_capability_reference_generated_source_default_score():
    ref = CapabilityReference(
        source="generated",
        capability_name="freshly-built-thing",
        capability_type=CapabilityType.SKILL,
    )
    assert ref.source == "generated"
    assert ref.score is None


def test_capability_reference_rejects_invalid_source():
    with pytest.raises(ValueError):
        CapabilityReference(
            source="cached",  # not in Literal["registry", "generated"]
            capability_name="x",
            capability_type=CapabilityType.MCP_TOOL,
        )


def test_capability_reference_rejects_extra():
    with pytest.raises(ValueError):
        CapabilityReference(
            source="registry",
            capability_name="x",
            capability_type=CapabilityType.MCP_TOOL,
            bonus="bad",
        )


def test_capability_reference_roundtrip():
    ref = CapabilityReference(
        source="registry",
        capability_name="weather-fetcher",
        capability_type=CapabilityType.MCP_TOOL,
        score=0.42,
    )
    roundtripped = CapabilityReference.model_validate_json(ref.model_dump_json())
    assert roundtripped.source == "registry"
    assert roundtripped.capability_name == "weather-fetcher"
    assert roundtripped.score == 0.42


# --- ChainResult (Stage 1 compositional) ---


def test_chain_result_minimal_success():
    cr = ChainResult(chain_length=1, chain_success=True)
    assert cr.chain_length == 1
    assert cr.chain_success is True
    assert cr.step_results == []
    assert cr.failed_step_index is None
    assert cr.failed_step_name is None


def test_chain_result_failure_with_step_info():
    cr = ChainResult(
        chain_length=3,
        chain_success=False,
        step_results=[{"name": "step-a", "ok": True}, {"name": "step-b", "ok": False}],
        failed_step_index=1,
        failed_step_name="step-b",
    )
    assert cr.chain_length == 3
    assert cr.chain_success is False
    assert cr.failed_step_index == 1
    assert cr.failed_step_name == "step-b"
    assert len(cr.step_results) == 2


def test_chain_result_chain_length_must_be_at_least_one():
    with pytest.raises(ValueError):
        ChainResult(chain_length=0, chain_success=False)


def test_chain_result_rejects_extra():
    with pytest.raises(ValueError):
        ChainResult(
            chain_length=1,
            chain_success=True,
            unknown_field="oops",
        )


def test_chain_result_roundtrip():
    cr = ChainResult(
        chain_length=2,
        chain_success=True,
        step_results=[{"name": "a", "ok": True}, {"name": "b", "ok": True}],
    )
    roundtripped = ChainResult.model_validate_json(cr.model_dump_json())
    assert roundtripped.chain_length == 2
    assert roundtripped.chain_success is True
    assert len(roundtripped.step_results) == 2
