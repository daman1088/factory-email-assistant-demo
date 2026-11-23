"""'Ask your emails': a bounded tool-use loop over the Messages API, streamed to the browser.

Per user turn:
  1. Replay recent session history as plain text (earlier thinking blocks are not replayed, so nothing
     in the cached prefix is ever edited).
  2. Stream the model; when it calls tools, run them (tenant-scoped, read-only) and loop, at most
     MAX_TOOL_ROUNDS times.
  3. Validate citations: [[email:<id>]] markers survive only if a tool returned that id in this turn
     (or it was cited earlier in this session). Everything else is stripped.
  4. Persist the answer, citations and a tool trace (ids only, no bodies) for audit.
"""
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import log_ctx
from app.models import ChatMessage, ChatSession, Email, Organization
from app.prompts import chat_v1
from app.services import email_query as q
from app.services.chat_tools import TOOL_DEFINITIONS, ToolContext, run_tool

logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 6
HISTORY_MESSAGES = 12
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CITATION_RE = re.compile(r"\[\[email:([0-9a-fA-F-]{36})\]\]")


def sanitize_citations(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Drop citation markers for ids no tool returned. Returns (text, cited ids in order)."""
    cited: list[str] = []

    def repl(m: re.Match) -> str:
        eid = m.group(1).lower()
        if eid in allowed:
            if eid not in cited:
                cited.append(eid)
            return f"[[email:{eid}]]"
        return ""

    cleaned = CITATION_RE.sub(repl, text)
    return re.sub(r"[ \t]+([.,;:])", r"\1", cleaned), cited


def citation_details(db: Session, org: Organization, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    rows = {str(e.id): (e, c) for e, c in db.execute(
        q.base_query(org.id).where(Email.id.in_([uuid.UUID(i) for i in ids]))).all()}
    out = []
    for i in ids:
        if i in rows:
            e, c = rows[i]
            out.append({"email_id": i, "from_name": e.from_name or e.from_email, "subject": e.subject,
                        "sent_at": q.to_local(e.sent_at, org.timezone).isoformat(),
                        "category": c.primary_category if c else None})
    return out


def _history(db: Session, session: ChatSession, before: datetime) -> tuple[list[dict], set[str]]:
    rows = list(db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id,
                                  ChatMessage.organization_id == session.organization_id,
                                  ChatMessage.created_at < before)
        .order_by(ChatMessage.created_at.desc()).limit(HISTORY_MESSAGES)).scalars())[::-1]
    while rows and rows[0].role != "user":
        rows.pop(0)
    prior_ids = {c["email_id"] for r in rows for c in (r.citations or [])}
    return [{"role": r.role, "content": r.content} for r in rows], prior_ids


def _context_block(org: Organization) -> str:
    now = q.to_local(datetime.now(UTC), org.timezone)
    return (f"<context>Current date and time: {now:%A, %d %B %Y, %H:%M} ({org.timezone}). "
            f"Organisation: {org.name}.</context>")


def _save(db: Session, session: ChatSession, role: str, content: str, **kw) -> ChatMessage:
    msg = ChatMessage(organization_id=session.organization_id, session_id=session.id, role=role,
                      content=content, **kw)
    db.add(msg)
    session.updated_at = datetime.now(UTC)
    db.commit()
    return msg


def _final_event(db, org, session, text, cited, trace, model, usage) -> dict:
    citations = citation_details(db, org, cited)
    msg = _save(db, session, "assistant", text, citations=citations, tool_trace=trace, model=model,
                input_tokens=usage.get("input"), output_tokens=usage.get("output"))
    return {"type": "final", "message": {"id": str(msg.id), "role": "assistant", "content": text,
                                         "citations": citations, "model": model,
                                         "created_at": msg.created_at.isoformat()}}


def run_turn(db: Session, org: Organization, session: ChatSession, question: str, embedder,
             client=None) -> Iterator[dict]:
    """Yields events: {"type": "text", "delta"}, {"type": "tool", "name"}, {"type": "final", "message"},
    {"type": "error", "message"}."""
    user_msg = _save(db, session, "user", question)
    if session.title == "New conversation":
        session.title = question[:80]
        db.commit()
    settings = get_settings()
    ctx = ToolContext(db=db, org=org, embedder=embedder)

    if client is None and settings.llm_provider != "anthropic":
        yield from _offline_turn(db, org, session, question, ctx)
        return
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=120.0)

    history, prior_ids = _history(db, session, user_msg.created_at)
    messages = history + [{"role": "user", "content": [
        {"type": "text", "text": _context_block(org)},
        {"type": "text", "text": question},
    ]}]
    trace: list[dict] = []
    usage = {"input": 0, "output": 0}
    model = settings.chat_model
    final_text = ""

    try:
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            with client.beta.messages.stream(
                model=settings.chat_model,
                max_tokens=8000,
                system=[{"type": "text", "text": chat_v1.SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=TOOL_DEFINITIONS,
                messages=messages,
                output_config={"effort": settings.chat_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "delta": event.text}
                response = stream.get_final_message()
            model = response.model
            usage["input"] += response.usage.input_tokens
            usage["output"] += response.usage.output_tokens

            if response.stop_reason == "refusal":
                final_text = "I can't help with that request."
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            final_text = "".join(b.text for b in response.content if b.type == "text")
            if not tool_uses or round_no == MAX_TOOL_ROUNDS:
                break
            if response.stop_reason == "max_tokens":
                final_text = "The answer was cut off. Please ask a narrower question."
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                yield {"type": "tool", "name": block.name}
                started = time.monotonic()
                before = set(ctx.seen_email_ids)
                content, is_error = run_tool(ctx, block.name, block.input if isinstance(block.input, dict) else {})
                trace.append({"tool": block.name, "input": block.input, "error": is_error,
                              "result_ids": sorted(ctx.seen_email_ids - before),
                              "ms": round((time.monotonic() - started) * 1000)})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                **({"is_error": True} if is_error else {})})
            if round_no == MAX_TOOL_ROUNDS - 1:
                results.append({"type": "text", "text": "Tool budget reached. Answer now with the information "
                                                        "already retrieved, and say what you could not check."})
            messages.append({"role": "user", "content": results})
            yield {"type": "reset"}  # interim text was a preamble; the answer comes in the next round
    except Exception as exc:
        logger.exception("chat turn failed", extra=log_ctx(session_id=str(session.id)))
        db.rollback()
        msg = "The AI service is unavailable right now. Please try again in a minute."
        if type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError"):
            msg = "The AI service rejected the API key. Check ANTHROPIC_API_KEY in Settings."
        yield {"type": "error", "message": msg}
        return

    text, cited = sanitize_citations(final_text.strip() or "I couldn't produce an answer.",
                                     ctx.seen_email_ids | prior_ids)
    logger.info("chat turn", extra=log_ctx(session_id=str(session.id), tools=len(trace), model=model,
                                           input_tokens=usage["input"], output_tokens=usage["output"]))
    yield _final_event(db, org, session, text, cited, trace, model, usage)


def _offline_turn(db, org, session, question, ctx: ToolContext) -> Iterator[dict]:
    """LLM_PROVIDER=fake: no language model. Build a rules-based report from SQL and verbatim email text
    (app/services/offline), clearly labelled as such."""
    from app.services import offline

    yield {"type": "tool", "name": "offline_report"}
    started = time.monotonic()
    previous = db.execute(
        select(ChatMessage.content).where(ChatMessage.session_id == session.id,
                                          ChatMessage.organization_id == session.organization_id,
                                          ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc()).offset(1).limit(1)).scalar_one_or_none()
    try:
        text, intent = offline.answer(ctx, question, previous)
    except Exception:
        logger.exception("offline answer failed", extra=log_ctx(session_id=str(session.id)))
        db.rollback()
        yield {"type": "error", "message": "Couldn't build an offline answer for that question."}
        return
    text, cited = sanitize_citations(text, ctx.seen_email_ids)
    yield {"type": "text", "delta": text}
    trace = [{"tool": f"offline:{intent.kind}", "error": False,
              "input": {"company": intent.company, "date_preset": intent.date_preset,
                        "categories": intent.categories, "signals": intent.signals},
              "result_ids": sorted(ctx.seen_email_ids), "ms": round((time.monotonic() - started) * 1000)}]
    yield _final_event(db, org, session, text, cited, trace, offline.MODEL, {})


def to_json_line(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
