"""LangGraph workflow for retrieval, reply drafting, and policy review."""

from typing import TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # Keep local development usable before optional graph deps install.
    END = START = StateGraph = None

from supportai.gemini_drafter import draft_reply
from supportai.domains import get_domain
from supportai.knowledge import SearchResult, search_articles
from supportai.policy import PolicyReview, review_plan
from supportai.quality import QualityReport, evaluate_resolution
from supportai.resolution import (
    ResolutionPlan,
    _extract_actions,
    _extract_questions,
    _template_reply,
)
from supportai.tickets import Ticket


class ResolutionState(TypedDict, total=False):
    ticket: Ticket
    limit: int
    results: list[SearchResult]
    actions: list[str]
    questions: list[str]
    template: str
    customer_reply: str
    reply_source: str
    reply_warning: str
    plan: ResolutionPlan
    policy_review: PolicyReview
    quality: QualityReport
    triage_summary: str
    agent_trace: list[dict]


def triage_agent(state: ResolutionState) -> dict:
    ticket = state["ticket"]
    return {
        "triage_summary": f"{ticket.domain}: {ticket.category} / {ticket.urgency}",
        "agent_trace": [{
            "agent": "Ticket Triage Agent",
            "status": "complete",
            "detail": f"Classified as {ticket.category}, urgency {ticket.urgency}.",
        }],
    }


def knowledge_retrieval_agent(state: ResolutionState) -> dict:
    ticket = state["ticket"]
    results = search_articles(
        ticket.message,
        domain=ticket.domain,
        category=ticket.category,
        limit=state.get("limit", 3),
    )
    results = [result for result in results if result.score >= 0.5]
    actions = _extract_actions(results)
    if not actions and not results:
        actions = ["Escalate to a support specialist because no matching guidance was found."]
    trace = state.get("agent_trace", []) + [{
        "agent": "Knowledge Retrieval Agent",
        "status": "complete",
        "detail": f"Retrieved {len(results)} domain-scoped support article(s).",
    }]
    return {
        "results": results,
        "actions": actions,
        "questions": _extract_questions(results),
        "template": _template_reply(ticket, results),
        "agent_trace": trace,
    }


def resolution_agent(state: ResolutionState) -> dict:
    reply, source, warning = draft_reply(
        state["ticket"], state["results"], state["template"]
    )
    trace = state.get("agent_trace", []) + [{
        "agent": "Resolution Agent",
        "status": "complete" if source == "gemini" else "fallback",
        "detail": "Prepared a Gemini draft." if source == "gemini" else "Prepared a local template draft.",
    }]
    return {
        "customer_reply": reply,
        "reply_source": source,
        "reply_warning": warning,
        "agent_trace": trace,
    }


def policy_reviewer_agent(state: ResolutionState) -> dict:
    ticket = state["ticket"]
    results = state["results"]
    plan = ResolutionPlan(
        ticket_id=ticket.id,
        domain=ticket.domain,
        domain_name=get_domain(ticket.domain)["name"],
        customer_name=ticket.customer_name,
        issue=ticket.message,
        actions=state["actions"],
        questions=state["questions"],
        customer_reply=state["customer_reply"],
        sources=[
            f"{result.title} - {result.source} (match {result.score:.0%})"
            for result in results
        ],
        reply_source=state["reply_source"],
        reply_warning=state["reply_warning"],
    )
    policy_review = review_plan(plan)
    return {
        "plan": plan,
        "policy_review": policy_review,
        "agent_trace": state.get("agent_trace", []) + [{
            "agent": "Policy Reviewer Agent",
            "status": "complete" if policy_review.passed else "review_required",
            "detail": "No configured policy flags." if policy_review.passed else f"{len(policy_review.findings)} finding(s) require agent attention.",
        }],
    }


def quality_evaluation_agent(state: ResolutionState) -> dict:
    quality = evaluate_resolution(state["plan"], state["policy_review"])
    return {
        "quality": quality,
        "agent_trace": state.get("agent_trace", []) + [{
            "agent": "Quality Evaluation Agent",
            "status": "complete",
            "detail": f"Heuristic draft quality score: {quality.score}/100.",
        }],
    }


def _build_resolution_graph():
    if StateGraph is None:
        return None
    graph = StateGraph(ResolutionState)
    graph.add_node("ticket_triage_agent", triage_agent)
    graph.add_node("knowledge_retrieval_agent", knowledge_retrieval_agent)
    graph.add_node("resolution_agent", resolution_agent)
    graph.add_node("policy_reviewer_agent", policy_reviewer_agent)
    graph.add_node("quality_evaluation_agent", quality_evaluation_agent)
    graph.add_edge(START, "ticket_triage_agent")
    graph.add_edge("ticket_triage_agent", "knowledge_retrieval_agent")
    graph.add_edge("knowledge_retrieval_agent", "resolution_agent")
    graph.add_edge("resolution_agent", "policy_reviewer_agent")
    graph.add_edge("policy_reviewer_agent", "quality_evaluation_agent")
    graph.add_edge("quality_evaluation_agent", END)
    return graph.compile()


_RESOLUTION_GRAPH = _build_resolution_graph()


def run_resolution_workflow(ticket: Ticket, *, limit: int = 3) -> ResolutionState:
    """Run with LangGraph, or run the same nodes in sequence before it is installed."""
    initial_state: ResolutionState = {"ticket": ticket, "limit": limit}
    if _RESOLUTION_GRAPH is None:
        state = initial_state
        for node in (
            triage_agent,
            knowledge_retrieval_agent,
            resolution_agent,
            policy_reviewer_agent,
            quality_evaluation_agent,
        ):
            state.update(node(state))
        return state
    return _RESOLUTION_GRAPH.invoke(initial_state)
