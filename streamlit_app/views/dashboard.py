from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from demo import data, state, ui

demo = state.get_demo()
today = ui.demo_today(demo)

ui.page_header("Dashboard", f"{ui.ORG} · what came into the mailbox, by business topic",
               badge="Offline rules analysis")

choice = st.segmented_control("Date range", list(data.PRESETS), default="Today", key="dash_range",
                              label_visibility="collapsed")
choice = choice or "Today"
start = end = None
if choice == "Custom":
    picked = st.date_input("Custom range", value=(today - timedelta(days=6), today),
                           min_value=today - timedelta(days=60), max_value=today, format="DD/MM/YYYY")
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Pick a start and an end date.")
        st.stop()
    start, end = picked
rng = data.date_range(demo, choice, start, end)
d = data.dashboard(demo, rng)
k = d.kpis


def share(n: int) -> str:
    return f"{n / k['total_received']:.0%} of received" if k["total_received"] else "—"


st.caption(f"{rng.label} · {rng.start_date:%d %b %Y} – {rng.end_date:%d %b %Y} (IST)")
ui.kpi_grid([
    ("Emails received", k["total_received"], f"+ {k['sent']} sent by you", "brand"),
    ("Enquiries", k["enquiries"], share(k["enquiries"]), "neutral"),
    ("Orders", k["orders"], share(k["orders"]), "neutral"),
    ("Financial", k["financial"], share(k["financial"]), "neutral"),
    ("Suppliers", k["suppliers"], share(k["suppliers"]), "neutral"),
    ("Logistics", k["logistics"], share(k["logistics"]), "neutral"),
    ("High priority", k["high_priority"], "needs attention first", "crit"),
    ("Action required", k["action_required"], "reply or task pending", "warn"),
])

left, right = st.columns([1.15, 1], gap="medium")
with left:
    with st.container(border=True):
        ui.section("Executive summary", "Built from the exact counts above by a template — not written by an AI model")
        st.markdown(f'<div class="summary">{ui.esc(d.summary)}</div>', unsafe_allow_html=True)
    with st.container(border=True):
        ui.section("Emails by category", f"Received emails, {rng.label.lower()}")
        cats = pd.DataFrame(d.categories).sort_values("count", ascending=False)
        cats["share"] = cats["count"] / max(int(cats["count"].sum()), 1)
        base = alt.Chart(cats).encode(
            y=alt.Y("label:N", sort="-x", title=None,
                    axis=alt.Axis(labelColor="#344054", labelFontSize=12, ticks=False, domain=False, labelPadding=8,
                                  labelLimit=220)),
            x=alt.X("count:Q", title=None,
                    axis=alt.Axis(grid=True, gridColor="#eef0f3", labels=False, ticks=False, domain=False)),
            tooltip=[alt.Tooltip("label:N", title="Category"), alt.Tooltip("count:Q", title="Emails"),
                     alt.Tooltip("share:Q", title="Share", format=".0%")],
        )
        bars = base.mark_bar(color="#2a78d6", cornerRadiusEnd=4, height=16)
        labels = base.mark_text(align="left", dx=6, color="#344054", fontSize=12, fontWeight=600).encode(text="count:Q")
        chart = (bars + labels).properties(height=alt.Step(30)).configure_view(stroke=None)
        st.altair_chart(chart, width="stretch")
        with st.expander("View as table"):
            st.dataframe(cats.rename(columns={"label": "Category", "count": "Emails"})[["Category", "Emails"]],
                         hide_index=True, width="stretch")

with right:
    with st.container(border=True):
        ui.section("Priority inbox", "High and medium priority emails, most urgent first")
        if not d.priority:
            st.caption("No high or medium priority emails in this range.")
        for i, row in enumerate(d.priority):
            c = row["classification"] or {}
            sent = datetime.fromisoformat(row["sent_at"])
            who = c.get("company_name") or row["from_name"] or row["from_email"]
            if who == "Internal":
                who = f"{row['from_name']} · internal"
            if row["direction"] == "outbound":
                who = "You"
            col_a, col_b = st.columns([4.4, 1.25], vertical_alignment="center")
            col_a.markdown(ui.item(who, ui.when(sent, today), row["subject"] or "", c.get("summary"),
                                   ui.priority_pill(c.get("priority"))), unsafe_allow_html=True)
            if col_b.button("Open", key=f"pi-{i}-{row['id']}", width="stretch"):
                ui.email_dialog(row["id"])

st.write("")
left2, right2 = st.columns([1, 1], gap="medium")
with left2:
    with st.container(border=True):
        ui.section("Open action items", "Created by the rules classifier; they close when you reply in the thread")
        if not d.actions:
            st.caption("No open action items for emails in this range.")
        for i, a in enumerate(d.actions):
            e = a["email"]
            c = e.get("classification") or {}
            sent = datetime.fromisoformat(e["sent_at"])
            due = f"Due {a['due_date']}" if a["due_date"] else ui.when(sent, today)
            who = c.get("company_name") or e["from_name"] or e["from_email"]
            if who == "Internal":
                who = f"{e['from_name']} · internal"
            col_a, col_b = st.columns([4.4, 1.25], vertical_alignment="center")
            col_a.markdown(ui.item(a["title"], due, who, None, ui.priority_pill(a["priority"])),
                           unsafe_allow_html=True)
            if col_b.button("Open", key=f"ai-{i}-{a['id']}", width="stretch"):
                ui.email_dialog(e["id"])
with right2:
    with st.container(border=True):
        ui.section("Recent important communications", "Newest first, with the key sentence copied from each email")
        if not d.recent:
            st.caption("Nothing important arrived in this range.")
        for i, r in enumerate(d.recent):
            col_a, col_b = st.columns([4.4, 1.25], vertical_alignment="center")
            col_a.markdown(ui.item(r["who"], ui.when(r["when"], today), f"{r['event']} · {r['subject']}", r["says"],
                                   ui.priority_pill(r["priority"])), unsafe_allow_html=True)
            if col_b.button("Open", key=f"rc-{i}-{r['id']}", width="stretch"):
                ui.email_dialog(r["id"])
