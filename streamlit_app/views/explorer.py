from datetime import timedelta

import streamlit as st

from demo import data, state, ui

demo = state.get_demo()
today = ui.demo_today(demo)
df = state.emails()

ui.page_header("Email Explorer", f"Browse and search all {len(df)} emails in the {ui.ORG} demo mailbox",
               badge=f"{len(df)} fictional emails")

with st.container(border=True):
    c1, c2 = st.columns([2.2, 1])
    query = c1.text_input("Search", placeholder="Search sender, company, subject or email text — e.g. Desai, PO, UTR",
                          key="ex_query")
    period = c2.selectbox("Date", list(data.PRESETS), index=list(data.PRESETS).index("All time"), key="ex_period")
    c3, c4, c5 = st.columns([1.6, 1, 1])
    cat_options = list(data.CATEGORY_NAMES)
    categories = c3.multiselect("Category", cat_options, format_func=data.CATEGORY_NAMES.get, key="ex_cats",
                                placeholder="All categories")
    priorities = c4.multiselect("Priority", ["high", "medium", "low"], format_func=str.capitalize, key="ex_prio",
                                placeholder="Any priority")
    direction = c5.selectbox("Direction", ["All", "Received", "Sent"], key="ex_dir")
    start = end = None
    if period == "Custom":
        picked = st.date_input("Custom range", value=(today - timedelta(days=6), today),
                               min_value=today - timedelta(days=60), max_value=today, format="DD/MM/YYYY")
        if isinstance(picked, tuple) and len(picked) == 2:
            start, end = picked
        else:
            st.stop()

rng = data.date_range(demo, period, start, end)
result = data.filter_emails(df, query=query, categories=categories, priorities=priorities,
                            direction={"Received": "inbound", "Sent": "outbound"}.get(direction), rng=rng)

st.caption(f"**{len(result)}** of {len(df)} emails match · {rng.label}"
           + (" · sent emails are not categorised, so category and priority filters hide them"
              if (categories or priorities) and direction != "Received" else ""))

table = result.assign(
    When=result["sent_at"].map(lambda t: ui.when(t, today)),
    Dir=result["direction"].map({"inbound": "↓ In", "outbound": "↑ Out"}),
    Contact=result.apply(lambda r: r["company"] if r["direction"] == "inbound" else f"To: {r['company']}", axis=1),
    Subject=result["subject"],
    Category=result["category_label"],
    Priority=result["priority"].fillna("").str.capitalize(),
)[["When", "Dir", "Contact", "Subject", "Category", "Priority"]]

left, right = st.columns([1.05, 1], gap="medium")
with left:
    event = st.dataframe(
        table, hide_index=True, width="stretch", height=620, on_select="rerun", selection_mode="single-row",
        key="ex_table",
        column_config={
            "When": st.column_config.TextColumn(width=118),
            "Dir": st.column_config.TextColumn(width=62, help="↓ received · ↑ sent by you"),
            "Contact": st.column_config.TextColumn(width=150),
            "Subject": st.column_config.TextColumn(width=280),
            "Category": st.column_config.TextColumn(width=150),
            "Priority": st.column_config.TextColumn(width=80),
        })
selected = event.selection.rows if event and event.selection else []
with right:
    with st.container(border=True):
        if selected and selected[0] < len(result):
            ui.email_detail(data.email_detail(demo, result.iloc[selected[0]]["id"]), today)
        elif len(result):
            st.markdown('<div class="section-title">Select an email</div>'
                        '<div class="section-sub">Click a row to read the full email, the rules analysis '
                        '(orders, invoices, payments, dates) and the rest of the conversation.</div>',
                        unsafe_allow_html=True)
        else:
            st.caption("No emails match these filters.")
