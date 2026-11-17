"""Query decomposition and the per-request retrieval plan.

Decomposition is only used when the question genuinely needs several pieces of
evidence (comparisons, two departments, timelines). Simple questions keep a
single retrieval query, so the pipeline does not pay a multi-query cost for
"what is the leave policy?".

Security: subquestions are search strings only. Each subquestion's candidates
go through the same authorization pass as the original query; decomposition
cannot create a new retrieval path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.rag.query_rewriter import expand_queries, rewrite_question
from app.services.rag.query_understanding import QueryAnalysis, analyze_query

_DEPARTMENT_LABELS = {"HR": "HR", "IT": "IT"}


@dataclass(frozen=True, slots=True)
class QueryPlan:
    analysis: QueryAnalysis
    rewritten: str
    expansions: tuple[str, ...]
    subquestions: tuple[str, ...]

    @property
    def retrieval_queries(self) -> tuple[str, ...]:
        """Distinct queries to retrieve for, original rewrite first."""
        ordered = [self.rewritten, *self.expansions, *self.subquestions[1:]]
        seen: set[str] = set()
        result: list[str] = []
        for query in ordered:
            normalized = query.strip()
            if not normalized or normalized.lower() in seen:
                continue
            seen.add(normalized.lower())
            result.append(normalized)
        return tuple(result)


def _department_label(department: str) -> str:
    return _DEPARTMENT_LABELS.get(department, department.title())


def _aspects(question: str) -> tuple[str, str]:
    lowered = question.lower()
    if "leave" in lowered:
        return ("annual leave policy", "leave approval procedure")
    if "reimburs" in lowered or "expense" in lowered:
        return ("reimbursement policy", "expense approval procedure")
    return ("policy", "approval procedure")


def decompose_question(
    question: str,
    analysis: QueryAnalysis,
    *,
    max_subqueries: int = 4,
) -> tuple[str, ...]:
    """Split a genuinely complex question into searchable subquestions."""
    if max_subqueries < 2:
        return ()

    subquestions: list[str] = []
    if analysis.intent == "comparison" and len(analysis.departments) >= 2:
        for department in analysis.departments[:2]:
            for aspect in _aspects(question):
                subquestions.append(f"{_department_label(department)} {aspect}")
    elif analysis.is_complex:
        fragments = re.split(r";|\band\b|,", question)
        for fragment in fragments:
            cleaned = fragment.strip(" ?.!\t\n")
            if len(cleaned.split()) >= 3:
                subquestions.append(cleaned)

    seen: set[str] = set()
    unique: list[str] = []
    for item in subquestions:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    if len(unique) < 2:
        return ()
    return tuple(unique[:max_subqueries])


def build_query_plan(
    question: str,
    *,
    conversation_topic: str | None = None,
    max_expansions: int = 2,
    max_subqueries: int = 4,
    enable_understanding: bool = True,
    enable_expansion: bool = True,
    enable_decomposition: bool = True,
    max_chars: int = 400,
) -> QueryPlan:
    """Build the complete, bounded retrieval plan for one question."""
    analysis = analyze_query(question)

    if not enable_understanding:
        rewritten = question.strip()[:max_chars]
        return QueryPlan(
            analysis=analysis,
            rewritten=rewritten,
            expansions=(),
            subquestions=(rewritten,),
        )

    rewritten = rewrite_question(
        question,
        analysis,
        conversation_topic=conversation_topic,
        max_chars=max_chars,
    )
    expansions = (
        expand_queries(
            rewritten, analysis, max_expansions=max_expansions
        )
        if enable_expansion
        else ()
    )
    subquestions = (
        decompose_question(
            question, analysis, max_subqueries=max_subqueries
        )
        if enable_decomposition
        else ()
    )
    if not subquestions:
        subquestions = (rewritten,)

    return QueryPlan(
        analysis=analysis,
        rewritten=rewritten,
        expansions=expansions,
        subquestions=subquestions,
    )
