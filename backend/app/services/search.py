"""Hybrid retrieval: pgvector cosine similarity + Postgres full-text search, merged with reciprocal
rank fusion. Filters (dates, category, company...) are applied in SQL to both legs."""
import re
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import Email, EmailClassification, EmailEmbedding
from app.services import email_query as q

CANDIDATES = 40
RRF_K = 60


def _doc():
    return func.to_tsvector("english", func.coalesce(Email.subject, "") + " " + func.coalesce(Email.body_clean, ""))


def _or_tsquery(query: str):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 1][:12]
    if not words:
        return None
    return func.to_tsquery("english", " | ".join(words))


def hybrid_search(db: Session, org_id: uuid.UUID, query: str, f: q.EmailFilters, embedder, *,
                  limit: int = 10) -> list[tuple[Email, EmailClassification | None, str]]:
    join_cls = and_(EmailClassification.email_id == Email.id, EmailClassification.is_current)
    ranks: dict[uuid.UUID, float] = {}
    sources: dict[uuid.UUID, set[str]] = {}
    threads: dict[uuid.UUID, uuid.UUID] = {}
    directions: dict[uuid.UUID, str] = {}

    qvec = embedder.embed_query(query)
    dist = func.min(EmailEmbedding.embedding.cosine_distance(qvec))
    vstmt = (select(Email.id, Email.thread_id, Email.direction, dist.label("d")).select_from(Email)
             .join(EmailEmbedding, and_(EmailEmbedding.email_id == Email.id,
                                        EmailEmbedding.organization_id == org_id,
                                        EmailEmbedding.model == embedder.model_name))
             .outerjoin(EmailClassification, join_cls).where(Email.organization_id == org_id))
    vstmt = q.apply_filters(db, org_id, vstmt, f).group_by(Email.id).order_by(dist).limit(CANDIDATES)
    for rank, (eid, tid, direction, _d) in enumerate(db.execute(vstmt).all()):
        threads[eid], directions[eid] = tid, direction
        ranks[eid] = ranks.get(eid, 0) + 1 / (RRF_K + rank)
        sources.setdefault(eid, set()).add("semantic")

    tsq = _or_tsquery(query)
    if tsq is not None:
        score = func.ts_rank(_doc(), tsq)
        fstmt = (select(Email.id, Email.thread_id, Email.direction, score.label("r")).select_from(Email)
                 .outerjoin(EmailClassification, join_cls).where(Email.organization_id == org_id, _doc().op("@@")(tsq)))
        fstmt = q.apply_filters(db, org_id, fstmt, f).order_by(score.desc()).limit(CANDIDATES)
        for rank, (eid, tid, direction, _r) in enumerate(db.execute(fstmt).all()):
            threads[eid], directions[eid] = tid, direction
            ranks[eid] = ranks.get(eid, 0) + 1 / (RRF_K + rank)
            sources.setdefault(eid, set()).add("keyword")

    # One hit per conversation, ranked by the thread's best match. Replies repeat the customer's topic and
    # would otherwise crowd out other threads; the received message represents its thread when it matched.
    best: dict[uuid.UUID, uuid.UUID] = {}
    for eid in sorted(ranks, key=ranks.get, reverse=True):
        tid = threads[eid]
        if tid not in best or (directions[best[tid]] == "outbound" and directions[eid] == "inbound"):
            best[tid] = eid
    thread_score = {tid: max(ranks[e] for e in ranks if threads[e] == tid) for tid in best}
    top = [best[tid] for tid in sorted(best, key=thread_score.get, reverse=True)[:limit]]
    if not top:
        return []
    rows = {e.id: (e, c) for e, c in db.execute(q.base_query(org_id).where(Email.id.in_(top))).all()}
    return [(*rows[i], "+".join(sorted(sources[i]))) for i in top if i in rows]
