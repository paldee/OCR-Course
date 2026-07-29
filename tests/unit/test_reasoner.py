"""Unit tests ของ Curriculum_Reasoner (task 18).

ทดสอบ:
1. PrerequisiteGraph — compute_chain, detect_cycle, cycle error
2. CreditsSummarizer — sum_by_category, sum_total, alternative groups
3. GraduationEvaluator — evaluate rules with pass/fail + citation_id

**Validates: Requirements 15.1, 15.2, 15.3, 15.4**
"""

from __future__ import annotations

import pytest

from katrag.common.types import Credits, PrereqAnd, PrereqEmpty, PrereqLeaf, PrereqOr
from katrag.errors import PrerequisiteCycleError
from katrag.query.reasoner import (
    CourseCredits,
    CoursePrereq,
    CreditsSummarizer,
    GraduationEvaluator,
    GraduationRule,
    PrerequisiteGraph,
    RuleEvaluation,
)


# ══════════════════════════════════════════════════════════════════════
# PrerequisiteGraph tests
# ══════════════════════════════════════════════════════════════════════


class TestPrerequisiteGraphChain:
    """Test compute_chain — deterministic topological order (R15.1)."""

    def test_single_course_no_prereqs(self) -> None:
        """Course without prerequisites returns empty chain."""
        courses = [CoursePrereq(code="CS101", prerequisite=PrereqEmpty())]
        graph = PrerequisiteGraph(courses)
        assert graph.compute_chain("CS101") == []

    def test_linear_chain(self) -> None:
        """A → B → C should produce chain [C, B] for A."""
        courses = [
            CoursePrereq(code="CS301", prerequisite=PrereqLeaf(code="CS201")),
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS101", prerequisite=PrereqEmpty()),
        ]
        graph = PrerequisiteGraph(courses)
        chain = graph.compute_chain("CS301")
        # CS101 comes before CS201
        assert chain == ["CS101", "CS201"]

    def test_diamond_dependency(self) -> None:
        """Diamond: D depends on B and C, both depend on A.
        Chain for D should contain A, B, C in valid topological order.
        """
        courses = [
            CoursePrereq(
                code="CS400",
                prerequisite=PrereqAnd(children=(
                    PrereqLeaf(code="CS201"),
                    PrereqLeaf(code="CS202"),
                )),
            ),
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS202", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS101", prerequisite=PrereqEmpty()),
        ]
        graph = PrerequisiteGraph(courses)
        chain = graph.compute_chain("CS400")
        # CS101 must appear before CS201 and CS202
        assert "CS101" in chain
        assert "CS201" in chain
        assert "CS202" in chain
        idx_101 = chain.index("CS101")
        idx_201 = chain.index("CS201")
        idx_202 = chain.index("CS202")
        assert idx_101 < idx_201
        assert idx_101 < idx_202

    def test_chain_is_deterministic(self) -> None:
        """Same input always produces same output (R15.4)."""
        courses = [
            CoursePrereq(
                code="CS400",
                prerequisite=PrereqAnd(children=(
                    PrereqLeaf(code="CS201"),
                    PrereqLeaf(code="CS202"),
                )),
            ),
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS202", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS101", prerequisite=PrereqEmpty()),
        ]
        graph = PrerequisiteGraph(courses)
        chain1 = graph.compute_chain("CS400")
        chain2 = graph.compute_chain("CS400")
        assert chain1 == chain2

    def test_or_prerequisites(self) -> None:
        """OR prerequisite: all alternatives appear in chain."""
        courses = [
            CoursePrereq(
                code="CS300",
                prerequisite=PrereqOr(children=(
                    PrereqLeaf(code="CS201"),
                    PrereqLeaf(code="CS202"),
                )),
            ),
            CoursePrereq(code="CS201", prerequisite=PrereqEmpty()),
            CoursePrereq(code="CS202", prerequisite=PrereqEmpty()),
        ]
        graph = PrerequisiteGraph(courses)
        chain = graph.compute_chain("CS300")
        # Both OR alternatives are in the chain (they are prerequisites either way)
        assert "CS201" in chain
        assert "CS202" in chain

    def test_unknown_code_returns_empty(self) -> None:
        """Querying a code not in graph returns empty chain."""
        courses = [CoursePrereq(code="CS101", prerequisite=PrereqEmpty())]
        graph = PrerequisiteGraph(courses)
        assert graph.compute_chain("UNKNOWN") == []


class TestPrerequisiteGraphCycle:
    """Test cycle detection — raises PrerequisiteCycleError (R15.1)."""

    def test_self_cycle(self) -> None:
        """Course requiring itself creates a cycle."""
        courses = [CoursePrereq(code="CS101", prerequisite=PrereqLeaf(code="CS101"))]
        graph = PrerequisiteGraph(courses)
        with pytest.raises(PrerequisiteCycleError) as exc_info:
            graph.compute_chain("CS101")
        assert "CS101" in exc_info.value.course_codes

    def test_two_node_cycle(self) -> None:
        """A requires B, B requires A."""
        courses = [
            CoursePrereq(code="CS101", prerequisite=PrereqLeaf(code="CS201")),
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
        ]
        graph = PrerequisiteGraph(courses)
        with pytest.raises(PrerequisiteCycleError) as exc_info:
            graph.compute_chain("CS101")
        assert "CS101" in exc_info.value.course_codes
        assert "CS201" in exc_info.value.course_codes

    def test_three_node_cycle(self) -> None:
        """A → B → C → A."""
        courses = [
            CoursePrereq(code="CS101", prerequisite=PrereqLeaf(code="CS301")),
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS301", prerequisite=PrereqLeaf(code="CS201")),
        ]
        graph = PrerequisiteGraph(courses)
        with pytest.raises(PrerequisiteCycleError):
            graph.compute_chain("CS101")

    def test_detect_cycle_method(self) -> None:
        """detect_cycle() raises PrerequisiteCycleError and reports cycle path."""
        courses = [
            CoursePrereq(code="A", prerequisite=PrereqLeaf(code="B")),
            CoursePrereq(code="B", prerequisite=PrereqLeaf(code="A")),
        ]
        graph = PrerequisiteGraph(courses)
        with pytest.raises(PrerequisiteCycleError) as exc_info:
            graph.detect_cycle()
        assert len(exc_info.value.course_codes) >= 2

    def test_no_cycle_detect_returns_empty(self) -> None:
        """detect_cycle() returns empty list when no cycle."""
        courses = [
            CoursePrereq(code="CS201", prerequisite=PrereqLeaf(code="CS101")),
            CoursePrereq(code="CS101", prerequisite=PrereqEmpty()),
        ]
        graph = PrerequisiteGraph(courses)
        issues = graph.detect_cycle()
        assert issues == []


# ══════════════════════════════════════════════════════════════════════
# CreditsSummarizer tests
# ══════════════════════════════════════════════════════════════════════


class TestCreditsSummarizer:
    """Test credits summation (R15.2)."""

    def _make_course(
        self,
        code: str,
        category: str,
        total: int,
        alt_group: str | None = None,
    ) -> CourseCredits:
        return CourseCredits(
            code=code,
            category=category,
            credits=Credits(total=total, lecture=total, lab=0, self_study=0),
            alternative_group=alt_group,
        )

    def test_single_category(self) -> None:
        """Sum courses in one category."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS101", "วิชาแกน", 3),
            self._make_course("CS102", "วิชาแกน", 3),
        ]
        result = summarizer.sum_by_category(courses)
        assert result == {"วิชาแกน": 6}

    def test_multiple_categories(self) -> None:
        """Sum courses across categories."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS101", "วิชาแกน", 3),
            self._make_course("GE001", "วิชาศึกษาทั่วไป", 2),
            self._make_course("CS201", "วิชาแกน", 3),
        ]
        result = summarizer.sum_by_category(courses)
        assert result == {"วิชาแกน": 6, "วิชาศึกษาทั่วไป": 2}

    def test_sum_total(self) -> None:
        """Total across all categories."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS101", "วิชาแกน", 3),
            self._make_course("GE001", "วิชาศึกษาทั่วไป", 2),
            self._make_course("CS201", "วิชาเฉพาะ", 4),
        ]
        total = summarizer.sum_total(courses)
        assert total == 9

    def test_alternative_group_counted_once(self) -> None:
        """Courses in the same alternative_group count only once."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS301", "วิชาเลือก", 3, alt_group="elective_A"),
            self._make_course("CS302", "วิชาเลือก", 3, alt_group="elective_A"),
            self._make_course("CS303", "วิชาเลือก", 3, alt_group="elective_A"),
        ]
        result = summarizer.sum_by_category(courses)
        # Only counted once (first encountered)
        assert result == {"วิชาเลือก": 3}

    def test_alternative_group_different_groups(self) -> None:
        """Different alternative groups each count once."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS301", "วิชาเลือก", 3, alt_group="group_A"),
            self._make_course("CS302", "วิชาเลือก", 3, alt_group="group_A"),
            self._make_course("CS401", "วิชาเลือก", 3, alt_group="group_B"),
            self._make_course("CS402", "วิชาเลือก", 3, alt_group="group_B"),
        ]
        result = summarizer.sum_by_category(courses)
        assert result == {"วิชาเลือก": 6}  # 3 + 3 (one from each group)

    def test_no_alternative_group_all_counted(self) -> None:
        """Courses without alternative_group are all counted."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS101", "วิชาแกน", 3),
            self._make_course("CS102", "วิชาแกน", 3),
            self._make_course("CS103", "วิชาแกน", 3),
        ]
        result = summarizer.sum_by_category(courses)
        assert result == {"วิชาแกน": 9}

    def test_empty_courses_list(self) -> None:
        """Empty input produces empty result."""
        summarizer = CreditsSummarizer()
        assert summarizer.sum_by_category([]) == {}
        assert summarizer.sum_total([]) == 0

    def test_mixed_grouped_and_ungrouped(self) -> None:
        """Mix of grouped and ungrouped courses."""
        summarizer = CreditsSummarizer()
        courses = [
            self._make_course("CS101", "วิชาแกน", 3),
            self._make_course("CS301", "วิชาแกน", 3, alt_group="elective_A"),
            self._make_course("CS302", "วิชาแกน", 3, alt_group="elective_A"),
        ]
        result = summarizer.sum_by_category(courses)
        assert result == {"วิชาแกน": 6}  # 3 (ungrouped) + 3 (one from group)


# ══════════════════════════════════════════════════════════════════════
# GraduationEvaluator tests
# ══════════════════════════════════════════════════════════════════════


class TestGraduationEvaluator:
    """Test graduation requirement evaluation (R15.3)."""

    def test_ge_pass(self) -> None:
        """Rule passes when actual >= threshold."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="graduation",
                attribute="total_credits",
                comparator=">=",
                value_numeric=129.0,
                citation_id="rule-001",
            ),
        ]
        actual = {"total_credits": 135.0}
        results = evaluator.evaluate(actual, rules)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].citation_id == "rule-001"

    def test_ge_fail(self) -> None:
        """Rule fails when actual < threshold."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="graduation",
                attribute="total_credits",
                comparator=">=",
                value_numeric=129.0,
                citation_id="rule-001",
            ),
        ]
        actual = {"total_credits": 100.0}
        results = evaluator.evaluate(actual, rules)
        assert len(results) == 1
        assert results[0].passed is False

    def test_multiple_rules(self) -> None:
        """Evaluate multiple rules — each gets citation_id."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="graduation",
                attribute="total_credits",
                comparator=">=",
                value_numeric=129.0,
                citation_id="rule-001",
            ),
            GraduationRule(
                rule_id=2,
                rule_kind="graduation",
                attribute="gpa",
                comparator=">=",
                value_numeric=2.0,
                citation_id="rule-002",
            ),
        ]
        actual = {"total_credits": 135.0, "gpa": 1.8}
        results = evaluator.evaluate(actual, rules)
        assert results[0].passed is True
        assert results[0].citation_id == "rule-001"
        assert results[1].passed is False
        assert results[1].citation_id == "rule-002"

    def test_missing_attribute_fails(self) -> None:
        """Rule fails when attribute not in actual_values."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="graduation",
                attribute="elective_credits",
                comparator=">=",
                value_numeric=18.0,
                citation_id="rule-003",
            ),
        ]
        actual: dict[str, float | str] = {"total_credits": 135.0}
        results = evaluator.evaluate(actual, rules)
        assert results[0].passed is False

    def test_comparator_equal(self) -> None:
        """Comparator '=' checks equality."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="graduation",
                attribute="years",
                comparator="=",
                value_numeric=4.0,
                citation_id="rule-004",
            ),
        ]
        assert evaluator.evaluate({"years": 4.0}, rules)[0].passed is True
        assert evaluator.evaluate({"years": 3.0}, rules)[0].passed is False

    def test_comparator_less_than(self) -> None:
        """Comparator '<' check."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="dismissal",
                attribute="absent_days",
                comparator="<",
                value_numeric=20.0,
                citation_id="rule-005",
            ),
        ]
        assert evaluator.evaluate({"absent_days": 15.0}, rules)[0].passed is True
        assert evaluator.evaluate({"absent_days": 25.0}, rules)[0].passed is False

    def test_comparator_in(self) -> None:
        """Comparator 'in' checks text membership."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=1,
                rule_kind="grading",
                attribute="final_grade",
                comparator="in",
                value_text="A,B+,B,C+,C,D+,D",
                citation_id="rule-006",
            ),
        ]
        assert evaluator.evaluate({"final_grade": "B+"}, rules)[0].passed is True
        assert evaluator.evaluate({"final_grade": "F"}, rules)[0].passed is False

    def test_all_rules_return_citation_ids(self) -> None:
        """Every evaluated rule has a citation_id in the result (R15.3)."""
        evaluator = GraduationEvaluator()
        rules = [
            GraduationRule(
                rule_id=i,
                rule_kind="graduation",
                attribute=f"attr_{i}",
                comparator=">=",
                value_numeric=float(i * 10),
                citation_id=f"cite-{i:03d}",
            )
            for i in range(1, 6)
        ]
        actual = {f"attr_{i}": float(i * 100) for i in range(1, 6)}
        results = evaluator.evaluate(actual, rules)
        for i, r in enumerate(results, start=1):
            assert r.citation_id == f"cite-{i:03d}"
