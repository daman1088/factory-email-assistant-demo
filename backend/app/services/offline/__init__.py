"""Offline answer engine (LLM_PROVIDER=fake): template reports built from SQL and verbatim email text.

It is not an AI model and does not pretend to be one: every answer is labelled as rules-based, every number
is an exact database result or text copied from an email, and unsupported questions fall back to a labelled
search instead of a guess. The paid Claude path in chat_agent.run_turn is unaffected.
"""
from app.services.chat_tools import ToolContext
from app.services.offline import intent as intent_mod
from app.services.offline.reports import build, company_candidates

MODEL = "offline-answers-v1"


def answer(ctx: ToolContext, question: str, previous_question: str | None = None) -> tuple[str, intent_mod.Intent]:
    candidates = company_candidates(ctx.db, ctx.org.id)
    previous = intent_mod.parse(previous_question, candidates) if previous_question else None
    parsed = intent_mod.parse(question, candidates, previous)
    return build(ctx, parsed), parsed
