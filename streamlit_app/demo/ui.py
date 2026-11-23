"""Visual layer: theme CSS, HTML components and the shared email-detail view.

Every value interpolated into HTML goes through esc(): email content is untrusted input."""
from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

PRODUCT = "Factory Email Assistant"
VENDOR = "MercuryNova Tech"
ORG = "Suryodaya Fabrics"

CSS = """
<style>
:root {
  --ink: #101828; --ink-2: #344054; --muted: #667085; --line: #e4e7ec; --surface: #f6f7f9; --card: #ffffff;
  --brand: #1d5fbf; --brand-ink: #174ea6; --brand-soft: #eaf1fb;
  --crit: #b42318; --crit-soft: #fef3f2; --warn: #b54708; --warn-soft: #fffaeb;
  --good: #067647; --good-soft: #ecfdf3; --neutral-soft: #f2f4f7;
}
[data-testid="stAppViewContainer"] { background: var(--surface); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1320px; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--line); }
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] { border-radius: 8px; }
h1, h2, h3, h4 { color: var(--ink); letter-spacing: -0.01em; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > .card-marker) { background: var(--card); }

.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 1.1rem; }
.page-head h1 { font-size: 1.6rem; font-weight: 700; margin: 0; padding: 0; }
.page-head p { color: var(--muted); margin: .25rem 0 0; font-size: .95rem; }
.badge { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff; color: var(--ink-2);
  border-radius: 999px; padding: 3px 10px; font-size: .78rem; font-weight: 500; white-space: nowrap; }
.badge .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--brand); }

.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: .2rem 0 1.2rem; }
@media (max-width: 900px) { .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.kpi { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px 12px; position: relative; overflow: hidden; }
.kpi::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--line); }
.kpi[data-tone="brand"]::before { background: var(--brand); }
.kpi[data-tone="crit"]::before { background: #d03b3b; }
.kpi[data-tone="warn"]::before { background: #fab219; }
.kpi-label { color: var(--muted); font-size: .8rem; font-weight: 500; }
.kpi-value { color: var(--ink); font-size: 1.85rem; font-weight: 700; line-height: 1.15; margin-top: 4px; font-variant-numeric: tabular-nums; }
.kpi-hint { color: var(--muted); font-size: .75rem; margin-top: 2px; }

.section-title { font-size: 1rem; font-weight: 600; color: var(--ink); margin: 0 0 .15rem; }
.section-sub { font-size: .8rem; color: var(--muted); margin: 0 0 .6rem; }
.summary { font-size: .98rem; line-height: 1.6; color: var(--ink-2); }
.note { font-size: .78rem; color: var(--muted); }

.pill { display: inline-block; border-radius: 999px; padding: 1px 9px; font-size: .72rem; font-weight: 600; margin-right: 4px; white-space: nowrap; }
.pill.high { background: var(--crit-soft); color: var(--crit); }
.pill.medium { background: var(--warn-soft); color: var(--warn); }
.pill.low, .pill.neutral { background: var(--neutral-soft); color: var(--ink-2); }
.pill.brand { background: var(--brand-soft); color: var(--brand-ink); }
.pill.good { background: var(--good-soft); color: var(--good); }

.item { padding: 8px 0 2px; border-top: 1px solid var(--line); }
.item-top { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.item-who { font-weight: 600; color: var(--ink); font-size: .9rem; }
.item-when { color: var(--muted); font-size: .78rem; margin-left: auto; white-space: nowrap; }
.item-subject { color: var(--ink-2); font-size: .88rem; margin-top: 2px; }
.item-quote { color: var(--muted); font-size: .82rem; margin-top: 2px; }

.mail-meta { color: var(--muted); font-size: .84rem; line-height: 1.6; }
.mail-meta b { color: var(--ink-2); font-weight: 600; }
.mail-body { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px;
  white-space: pre-wrap; font-size: .9rem; line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
.fact-grid { display: grid; grid-template-columns: 150px 1fr; gap: 6px 14px; font-size: .86rem; }
.fact-grid .k { color: var(--muted); }
.fact-grid .v { color: var(--ink); }

.gate { text-align: center; margin: 9vh 0 1.2rem; }
.gate .mark { display: inline-flex; width: 48px; height: 48px; border-radius: 12px; background: var(--brand-soft); color: var(--brand);
  align-items: center; justify-content: center; font-weight: 700; font-size: 1.1rem; }
.gate h1 { font-size: 1.5rem; margin: .8rem 0 .2rem; }
.gate p { color: var(--muted); margin: 0; }

.side-brand { padding: 2px 4px 10px; }
.side-brand .name { font-weight: 700; color: var(--ink); font-size: 1rem; }
.side-brand .by { color: var(--muted); font-size: .76rem; }
.side-org { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--surface); font-size: .8rem; color: var(--ink-2); }
.side-org b { color: var(--ink); }

[data-testid="stChatMessage"] { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
[data-testid="stChatMessage"] code { background: var(--brand-soft); color: var(--brand-ink); border-radius: 5px; padding: 0 4px; font-size: .78rem; }
[data-testid="stExpander"] [data-testid="stBaseButton-secondary"] { justify-content: flex-start; text-align: left; }
[data-testid="stExpander"] [data-testid="stBaseButton-secondary"] p { text-align: left; }
.offline-tag { display: inline-block; font-size: .72rem; color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 1px 9px; margin-bottom: 6px; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
  .fact-grid { grid-template-columns: 1fr; }
  .kpi-value { font-size: 1.5rem; }
}
</style>
"""


def esc(value) -> str:
    return escape("" if value is None else str(value))


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def gate_header() -> str:
    return (f'<div class="gate"><div class="mark">&#9993;</div><h1>{PRODUCT}</h1>'
            f'<p>Customer demo · {VENDOR}</p></div>')


def page_header(title: str, subtitle: str, badge: str | None = None) -> None:
    right = f'<span class="badge"><span class="dot"></span>{esc(badge)}</span>' if badge else ""
    st.markdown(f'<div class="page-head"><div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>{right}</div>',
                unsafe_allow_html=True)


def kpi_grid(items: list[tuple[str, int, str, str]]) -> None:
    cards = "".join(
        f'<div class="kpi" data-tone="{esc(tone)}"><div class="kpi-label">{esc(label)}</div>'
        f'<div class="kpi-value">{value:,}</div><div class="kpi-hint">{esc(hint)}</div></div>'
        for label, value, hint, tone in items)
    st.markdown(f'<div class="kpi-grid">{cards}</div>', unsafe_allow_html=True)


def section(title: str, sub: str | None = None) -> None:
    st.markdown(f'<div class="section-title">{esc(title)}</div>'
                + (f'<div class="section-sub">{esc(sub)}</div>' if sub else ""), unsafe_allow_html=True)


def pill(text: str, tone: str = "neutral") -> str:
    return f'<span class="pill {esc(tone)}">{esc(text)}</span>'


def priority_pill(priority: str | None) -> str:
    return pill(f"{priority.capitalize()} priority", priority) if priority in ("high", "medium", "low") else ""


def when(dt: datetime, today) -> str:
    days = (today - dt.date()).days
    if days == 0:
        return f"Today, {dt:%H:%M}"
    if days == 1:
        return f"Yesterday, {dt:%H:%M}"
    return f"{dt.day} {dt:%b}, {dt:%H:%M}"


def item(who: str, when_text: str, subject: str, quote: str | None = None, pills: str = "") -> str:
    return (f'<div class="item"><div class="item-top">{pills}<span class="item-who">{esc(who)}</span>'
            f'<span class="item-when">{esc(when_text)}</span></div>'
            f'<div class="item-subject">{esc(subject)}</div>'
            + (f'<div class="item-quote">“{esc(quote.strip().rstrip("."))}”</div>' if quote else "") + "</div>")


# ---- email detail ---------------------------------------------------------------------------------------

def _facts_rows(detail: dict) -> list[tuple[str, str]]:
    f = detail["facts"]
    rows = []
    for label, values in f["references"].items():
        rows.append((f"{label} reference", ", ".join(values)))
    if f["quantities"]:
        rows.append(("Quantities", ", ".join(f["quantities"])))
    if f["rates"]:
        rows.append(("Rates", ", ".join(f["rates"])))
    if f["amounts"]:
        rows.append(("Amounts", ", ".join(f["amounts"])))
    if f["dates"]:
        rows.append(("Dates / deadlines", ", ".join(f["dates"])))
    if f["payment"]:
        kind = {"claimed": "Sender says a payment was made (not verified)", "promised": "Payment promised",
                "bank_credit": "Bank credit alert", "bank_debit": "Bank debit alert",
                "dispute": "Dispute / deduction", "reminder": "Payment reminder / dues",
                "invoice": "Invoice or statement"}.get(f["payment"]["kind"], f["payment"]["kind"])
        rows.append(("Payment", f"{kind} — “{f['payment']['quote']}”"))
    if f["shipment"]:
        rows.append(("Shipment", f"{f['shipment']['status']} — “{f['shipment']['quote']}”"))
    return rows


def email_detail(detail: dict | None, today) -> None:
    if detail is None:
        st.info("This email is no longer available (the demo data is rebuilt each day). Ask the question again.")
        return
    cls = detail["classification"]
    st.markdown(f"#### {esc(detail['subject'])}", unsafe_allow_html=True)
    direction = pill("Received", "brand") if detail["direction"] == "inbound" else pill("Sent by you", "neutral")
    tags = direction
    if cls:
        tags += pill(cls["category"], "neutral") + priority_pill(cls["priority"])
        if cls["action_required"]:
            tags += pill("Action required", "medium")
    to = ", ".join(detail["to"])
    cc = ", ".join(detail["cc"])
    st.markdown(
        f'<div>{tags}</div><div class="mail-meta" style="margin-top:6px">'
        f'<b>From</b> {esc(detail["from_name"])} &lt;{esc(detail["from_email"])}&gt;<br>'
        f'<b>To</b> {esc(to)}' + (f' · <b>Cc</b> {esc(cc)}' if cc else "")
        + f'<br><b>Date</b> {esc(when(detail["sent_at"], today))} IST</div>', unsafe_allow_html=True)

    thread = detail["thread"]
    tabs = st.tabs(["Email", "Rules analysis", f"Conversation ({len(thread)})"])
    with tabs[0]:
        st.markdown(f'<div class="mail-body">{esc(detail["body"])}</div>', unsafe_allow_html=True)
        if detail["attachments"]:
            st.caption("Attachments (names only — files are not part of the demo): " + ", ".join(detail["attachments"]))
    with tabs[1]:
        if cls:
            st.markdown(f'<div class="section-sub">Summary</div><div class="summary">{esc(cls["summary"])}</div>',
                        unsafe_allow_html=True)
            if cls["suggested_action"]:
                st.markdown(f'<div class="section-sub" style="margin-top:10px">Suggested action</div>'
                            f'<div class="summary">{esc(cls["suggested_action"])}</div>', unsafe_allow_html=True)
        rows = [("Company", (cls or {}).get("company") or detail["who"]), ("Event", detail["facts"]["event"])]
        rows += _facts_rows(detail)
        if detail["actions"]:
            rows.append(("Action items", "; ".join(
                f"{a['title']} ({a['priority']}, {a['status']})" for a in detail["actions"])))
        grid = "".join(f'<div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>' for k, v in rows)
        st.markdown(f'<div class="fact-grid" style="margin-top:12px">{grid}</div>', unsafe_allow_html=True)
        st.markdown('<p class="note" style="margin-top:12px">Produced by the offline rules classifier and '
                    'extractor (no AI model). Figures are copied from the email text and are not verified against '
                    'accounts or bank records.</p>', unsafe_allow_html=True)
    with tabs[2]:
        for t in thread:
            who = "You" if t["direction"] == "outbound" else t["from"]
            label = f"{'▸ ' if t['current'] else ''}{when(t['sent_at'], today)} · {who}"
            with st.expander(label, expanded=t["current"] or len(thread) <= 2):
                st.markdown(f'<div class="mail-body">{esc(t["body"])}</div>', unsafe_allow_html=True)


@st.dialog("Source email", width="large")
def email_dialog(email_id: str) -> None:
    from demo import data, state

    demo = state.get_demo()
    email_detail(data.email_detail(demo, email_id), demo_today(demo))


def demo_today(demo):
    from demo.state import IST

    return datetime.now(IST).date()


def sidebar(demo, on_sign_out) -> None:
    with st.sidebar:
        st.markdown(
            f'<div class="side-org"><b>{ORG}</b> · demo workspace<br>{demo.email_count} fictional emails · '
            f'times in IST<br><span class="note">Offline rules engine · Gmail and AI not connected</span></div>',
            unsafe_allow_html=True)
        st.write("")
        st.button("Sign out", on_click=on_sign_out, icon=":material/logout:", width="stretch")
        st.markdown(f'<div class="note" style="margin-top:14px">Factory Email Assistant demo · {VENDOR}</div>',
                    unsafe_allow_html=True)
