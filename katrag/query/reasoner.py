"""Curriculum_Reasoner — deterministic computation for prerequisite chains,
credit summation, and graduation requirement evaluation (R15.1–R15.4).

All logic is pure deterministic code — LLM MUST NOT compute these values.
Called by Answer_Generator to supply numeric facts with provenance.

Requirements covered: 15.1, 15.2, 15.3, 15.4
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from katrag.common.types import (
    Credits,
    PrereqAnd,
    PrereqEmpty,
    PrereqLeaf,
    PrereqNode,
    PrereqOr,
    prereq_codes,
)
from katrag.errors import PrerequisiteCycleError, ReviewIssue


# ══════════════════════════════════════════════════════════════════════
# Data types for the reasoner
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CoursePrereq:
    """One course with its prerequisite expression — input to PrerequisiteGraph."""

    code: str
    prerequisite: PrereqNode

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("course code ต้องไม่ว่าง")


@dataclass(frozen=True, slots=True)
class CourseCredits:
    """One course with its credits and category — input to CreditsSummarizer."""

    code: str
    category: str
    credits: Credits
    alternative_group: str | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("course code ต้องไม่ว่าง")
        if not self.category:
            raise ValueError("category ต้องไม่ว่าง")


@dataclass(frozen=True, slots=True)
class GraduationRule:
    """One graduation rule from the store — input to GraduationEvaluator."""

    rule_id: int
    rule_kind: str
    attribute: str
    comparator: str
    value_numeric: float | None = None
    value_text: str | None = None
    citation_id: str = ""

    def __post_init__(self) -> None:
        if not self.attribute:
            raise ValueError("rule attribute ต้องไม่ว่าง")
        if self.comparator not in (">=", ">", "<=", "<", "=", "in"):
            raise ValueError(f"comparator ไม่ valid: {self.comparator}")


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Result of evaluating one graduation rule."""

    rule_id: int
    attribute: str
    comparator: str
    threshold: float | str | None
    actual_value: float | str | None
    passed: bool
    citation_id: str


# ══════════════════════════════════════════════════════════════════════
# PrerequisiteGraph — DAG computation with cycle detection (R15.1)
# ══════════════════════════════════════════════════════════════════════


class PrerequisiteGraph:
    """Build a prerequisite DAG from course relationships and compute chains.

    - compute_chain(code) → deterministic topological order of prerequisites
    - detect_cycle() → raises PrerequisiteCycleError if cycle exists

    The graph uses adjacency list representation. Topological sort uses
    Kahn's algorithm for deterministic output (sorted tie-breaking).
    """

    def __init__(self, courses: Sequence[CoursePrereq]) -> None:
        # adjacency: course_code → set of direct prerequisite codes
        self._adjacency: dict[str, set[str]] = {}
        self._all_codes: set[str] = set()

        for course in courses:
            self._all_codes.add(course.code)
            prereqs = set(prereq_codes(course.prerequisite))
            self._adjacency[course.code] = prereqs
            # Ensure prerequisite codes are also registered
            self._all_codes.update(prereqs)

        # Nodes not explicitly in courses list get empty prerequisites
        for code in list(self._all_codes):
            if code not in self._adjacency:
                self._adjacency[code] = set()

    @property
    def all_codes(self) -> frozenset[str]:
        """All course codes known to this graph."""
        return frozenset(self._all_codes)

    def direct_prerequisites(self, code: str) -> frozenset[str]:
        """Direct prerequisites of a course."""
        return frozenset(self._adjacency.get(code, set()))

    def compute_chain(self, code: str) -> list[str]:
        """Compute the prerequisite chain for a course in deterministic
        topological order (Kahn's algorithm with sorted tie-breaking).

        Returns ordered list of course codes that must be taken before `code`.
        Raises PrerequisiteCycleError if a cycle is detected involving this code.
        """
        # Collect all ancestors of `code` via BFS
        ancestors: set[str] = set()
        queue = list(self._adjacency.get(code, set()))
        visited: set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            ancestors.add(current)
            for prereq in self._adjacency.get(current, set()):
                if prereq not in visited:
                    queue.append(prereq)

        if not ancestors:
            return []

        # Check for cycle: if code is reachable from itself
        if code in ancestors:
            cycle_path = self._find_cycle_path(code)
            raise PrerequisiteCycleError(tuple(cycle_path))

        # Topological sort on the subgraph of ancestors using Kahn's algorithm
        # Build in-degree map for the subgraph
        subgraph: dict[str, set[str]] = {}
        in_degree: dict[str, int] = {}

        for node in ancestors:
            prereqs_in_subgraph = self._adjacency.get(node, set()) & ancestors
            subgraph[node] = prereqs_in_subgraph
            in_degree[node] = len(prereqs_in_subgraph)

        # Kahn's algorithm with sorted queue for determinism
        result: list[str] = []
        ready = sorted(n for n, deg in in_degree.items() if deg == 0)

        while ready:
            node = ready.pop(0)
            result.append(node)
            # Decrease in-degree of dependents
            for dependent in sorted(ancestors):
                if node in subgraph.get(dependent, set()):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        ready.append(dependent)
            ready.sort()

        # If not all nodes processed, there's a cycle in the subgraph
        if len(result) != len(ancestors):
            remaining = ancestors - set(result)
            cycle_path = self._find_cycle_in_set(remaining)
            raise PrerequisiteCycleError(tuple(cycle_path))

        return result

    def detect_cycle(self) -> list[ReviewIssue]:
        """Detect ALL cycles in the entire graph.

        Returns a list of ReviewIssue items for each cycle found.
        Raises PrerequisiteCycleError for the first cycle detected.
        """
        # Use DFS coloring: WHITE=0, GRAY=1, BLACK=2
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {code: WHITE for code in self._all_codes}
        parent: dict[str, str | None] = {code: None for code in self._all_codes}
        issues: list[ReviewIssue] = []

        def dfs(node: str) -> list[str] | None:
            color[node] = GRAY
            for prereq in sorted(self._adjacency.get(node, set())):
                if color[prereq] == GRAY:
                    # Found a cycle — trace back
                    cycle = [prereq, node]
                    current = node
                    while parent[current] is not None and parent[current] != prereq:
                        current = parent[current]  # type: ignore[assignment]
                        cycle.append(current)
                    cycle.reverse()
                    return cycle
                if color[prereq] == WHITE:
                    parent[prereq] = node
                    result = dfs(prereq)
                    if result is not None:
                        return result
            color[node] = BLACK
            return None

        for code in sorted(self._all_codes):
            if color[code] == WHITE:
                cycle = dfs(code)
                if cycle is not None:
                    issue = ReviewIssue(
                        kind="prerequisite_cycle",
                        detail={"course_codes": cycle},
                    )
                    issues.append(issue)
                    raise PrerequisiteCycleError(tuple(cycle))

        return issues

    def _find_cycle_path(self, start: str) -> list[str]:
        """Find a specific cycle path starting from `start` using DFS.

        Returns the full cycle including all nodes from start back to start.
        """
        path: list[str] = [start]
        on_path: set[str] = {start}

        def dfs(node: str) -> bool:
            for prereq in sorted(self._adjacency.get(node, set())):
                if prereq == start:
                    # Completed the cycle back to start
                    path.append(prereq)
                    return True
                if prereq not in on_path:
                    path.append(prereq)
                    on_path.add(prereq)
                    if dfs(prereq):
                        return True
                    path.pop()
                    on_path.discard(prereq)
            return False

        if dfs(start):
            # Remove the duplicate start at the end, keep the cycle nodes
            return path[:-1]
        return [start]

    def _find_cycle_in_set(self, nodes: set[str]) -> list[str]:
        """Find a cycle within a set of nodes."""
        if not nodes:
            return []
        start = sorted(nodes)[0]
        visited: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> bool:
            if node in visited:
                # Found cycle
                idx = path.index(node) if node in path else 0
                return True
            visited.add(node)
            path.append(node)
            for prereq in sorted(self._adjacency.get(node, set()) & nodes):
                if dfs(prereq):
                    return True
            path.pop()
            return False

        dfs(start)
        return path if path else [start]


# ══════════════════════════════════════════════════════════════════════
# CreditsSummarizer — aggregate credits per category (R15.2)
# ══════════════════════════════════════════════════════════════════════


class CreditsSummarizer:
    """Aggregate credits from course records.

    - sum_by_category(courses) → dict[category, total_credits]
    - sum_total(courses) → total credits for curriculum
    - Alternative groups are counted once (by group ID), not per member.
    """

    def sum_by_category(
        self, courses: Sequence[CourseCredits]
    ) -> dict[str, int]:
        """Sum credits.total per category.

        Courses in the same alternative_group contribute only once
        (the first course encountered in that group is counted).
        """
        seen_groups: set[str] = set()
        totals: dict[str, int] = defaultdict(int)

        for course in courses:
            # If course is in an alternative group, count only once
            if course.alternative_group is not None:
                if course.alternative_group in seen_groups:
                    continue
                seen_groups.add(course.alternative_group)

            totals[course.category] += course.credits.total

        return dict(totals)

    def sum_total(self, courses: Sequence[CourseCredits]) -> int:
        """Sum all credits.total across all categories.

        Same alternative-group deduplication applies.
        """
        by_category = self.sum_by_category(courses)
        return sum(by_category.values())


# ══════════════════════════════════════════════════════════════════════
# GraduationEvaluator — evaluate graduation requirements (R15.3)
# ══════════════════════════════════════════════════════════════════════


class GraduationEvaluator:
    """Evaluate graduation requirements against actual course data.

    Rules come from Provenance_Store (rule table).
    Returns citation IDs for every rule used — per R15.3.
    """

    def evaluate(
        self,
        actual_values: Mapping[str, float | str],
        rules: Sequence[GraduationRule],
    ) -> list[RuleEvaluation]:
        """Evaluate each rule against actual_values.

        Parameters
        ----------
        actual_values : dict mapping rule attribute → actual numeric or text value
            e.g. {"total_credits": 135, "gpa": 3.2, "category_core": 90}
        rules : sequence of GraduationRule from store

        Returns
        -------
        list[RuleEvaluation] with pass/fail status + citation_id for each rule
        """
        results: list[RuleEvaluation] = []

        for rule in rules:
            actual = actual_values.get(rule.attribute)
            passed = self._check_rule(rule, actual)

            results.append(RuleEvaluation(
                rule_id=rule.rule_id,
                attribute=rule.attribute,
                comparator=rule.comparator,
                threshold=rule.value_numeric if rule.value_numeric is not None else rule.value_text,
                actual_value=actual,
                passed=passed,
                citation_id=rule.citation_id,
            ))

        return results

    def _check_rule(
        self,
        rule: GraduationRule,
        actual: float | str | None,
    ) -> bool:
        """Check a single rule against an actual value."""
        if actual is None:
            return False

        if rule.comparator == "in":
            # Text-based 'in' check
            if rule.value_text is None:
                return False
            allowed = {v.strip() for v in rule.value_text.split(",")}
            return str(actual) in allowed

        # Numeric comparisons
        if rule.value_numeric is None:
            return False

        try:
            actual_num = float(actual)
        except (ValueError, TypeError):
            return False

        threshold = rule.value_numeric

        if rule.comparator == ">=":
            return actual_num >= threshold
        elif rule.comparator == ">":
            return actual_num > threshold
        elif rule.comparator == "<=":
            return actual_num <= threshold
        elif rule.comparator == "<":
            return actual_num < threshold
        elif rule.comparator == "=":
            return actual_num == threshold
        else:
            return False
