import streamlit as st

from demo import chat, state, ui

demo = state.get_demo()

SUGGESTIONS = [
    "Summarize the emails from Desai Uniforms",
    "What's the status of PO ABC/PO/7781?",
    "Show me the purchase orders we received",
    "Which customers say they have made payments?",
    "Which shipments are still pending?",
    "Which enquiries have not been replied to?",
    "Summarize financial communications this month",
    "Give me an overview of this week",
]
FOLLOW_UPS = ["What about their payments?", "And their shipments?"]

history: list[dict] = st.session_state.setdefault("chat", [])

head, action = st.columns([4, 1], vertical_alignment="bottom")
with head:
    ui.page_header("Ask your emails", "Questions about customers, orders, payments and shipments — answered from "
                   "the demo mailbox with sources", badge="Offline rules engine · no AI model")
with action:
    if history and st.button("New conversation", icon=":material/add_comment:", width="stretch"):
        st.session_state["chat"] = []
        st.rerun()

st.markdown(
    '<p class="note">Answers are <b>assembled by rules</b>, not written by an AI model. Counts and totals are exact '
    'database results; quoted text, amounts and dates are copied from the emails. A payment an email '
    '<i>says</i> was made is shown as a claim — this demo has no access to bank or accounting records.</p>',
    unsafe_allow_html=True)


def ask(question: str) -> None:
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
    history.append({"role": "user", "content": question})
    try:
        answer = chat.ask(demo, question, previous)
        history.append({"role": "assistant", "content": answer.text, "kind": answer.kind,
                        "sources": [s.__dict__ for s in answer.sources]})
    except Exception:  # never show a stack trace to a customer
        history.append({"role": "assistant", "content": "Sorry — I couldn't build an answer for that question. "
                        "Try one of the suggested questions.", "kind": "error", "sources": []})


pending = st.session_state.pop("_pending_question", None)
if pending:
    with st.spinner("Building an offline summary from your emails…"):
        ask(pending)

if not history:
    st.write("")
    ui.section("Try asking", "Click a question, or type your own below")
    cols = st.columns(2)
    for i, q in enumerate(SUGGESTIONS):
        if cols[i % 2].button(q, key=f"sugg-{i}", width="stretch"):
            st.session_state["_pending_question"] = q
            st.rerun()

for idx, msg in enumerate(history):
    if msg["role"] == "user":
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(ui.esc(msg["content"]))
        continue
    with st.chat_message("assistant", avatar=":material/mark_email_read:"):
        st.markdown('<span class="offline-tag">Offline summary · rules-based, not AI-written</span>',
                    unsafe_allow_html=True)
        st.markdown(msg["content"])
        sources = msg.get("sources") or []
        if sources:
            with st.expander(f"Sources — {len(sources)} email{'s' if len(sources) != 1 else ''} · click to open",
                             expanded=len(sources) <= 12):
                cols = st.columns(2)
                for j, s in enumerate(sources):
                    label = f"[{s['n']}] {s['when']} · {s['who']} — {s['subject']}"
                    if cols[j % 2].button(label[:90], key=f"src-{idx}-{s['n']}", width="stretch", help=s["subject"]):
                        ui.email_dialog(s["email_id"])

if history and history[-1]["role"] == "assistant" and history[-1].get("kind") == "company_summary":
    row = st.container(horizontal=True)
    for i, q in enumerate(FOLLOW_UPS):
        if row.button(q, key=f"fu-{len(history)}-{i}", icon=":material/subdirectory_arrow_right:"):
            st.session_state["_pending_question"] = q
            st.rerun()

question = st.chat_input("Ask about a customer, PO, payment, shipment or enquiry…")
if question and question.strip():
    st.session_state["_pending_question"] = question.strip()[:500]
    st.rerun()
