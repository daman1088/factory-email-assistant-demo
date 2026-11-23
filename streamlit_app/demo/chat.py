"""'Ask your emails' for the demo: the main app's offline answer engine (app.services.offline), unchanged.

The engine returns markdown with [[email:<uuid>]] citation markers. Here they are validated (only emails the
engine actually read this turn survive - the same check the main app runs), numbered [1], [2]... in order of
appearance, and returned with the source list the page turns into clickable buttons.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Email
from app.services import email_query as q
from app.services import offline
from app.services.chat_agent import sanitize_citations
from app.services.chat_tools import ToolContext
from app.services.offline.reports import Msg
from demo.bootstrap import DemoData

MARKER = re.compile(r"\s*\[\[email:([0-9a-fA-F-]{36})\]\]")
MODEL = offline.MODEL  # "offline-answers-v1"


@dataclass
class Source:
    n: int
    email_id: str
    when: str
    who: str
    subject: str


@dataclass
class Answer:
    text: str  # markdown with citations rendered as `[n]`
    sources: list[Source] = field(default_factory=list)
    kind: str = ""
    company: str | None = None
    raw: str = ""  # engine output with the validated [[email:...]] markers, for tests / audit


def _neutralise_markdown(text: str) -> str:
    """Quoted email text is untrusted: stop it from rendering images or LaTeX in the chat bubble."""
    return text.replace("![", "!\\[").replace("$", "\\$")


def ask(demo: DemoData, question: str, previous_question: str | None = None) -> Answer:
    with demo.session() as db:
        ctx = ToolContext(db=db, org=demo.org(db), embedder=demo.embedder)
        raw, intent = offline.answer(ctx, question, previous_question)
        raw, cited = sanitize_citations(raw, ctx.seen_email_ids)
        if demo.search_mode == "keyword":
            raw = raw.replace("keyword + meaning search", "keyword search")
        numbers: dict[str, int] = {}
        for eid in MARKER.findall(raw):
            numbers.setdefault(eid.lower(), len(numbers) + 1)
        sources = []
        if numbers:
            rows = db.execute(q.base_query(demo.org_id).where(
                Email.id.in_(list(numbers)))).all()
            by_id = {str(e.id): (e, c) for e, c in rows}
            for eid, n in numbers.items():
                if eid in by_id:
                    m = Msg(*by_id[eid], demo.tz)
                    sources.append(Source(n=n, email_id=eid, when=f"{m.local.day} {m.local:%b}", who=m.who,
                                          subject=m.subject))
    shown = MARKER.sub(lambda mt: f" `[{numbers[mt.group(1).lower()]}]`", raw)
    return Answer(text=_neutralise_markdown(shown), sources=sorted(sources, key=lambda s: s.n), kind=intent.kind,
                  company=intent.company, raw=raw)
