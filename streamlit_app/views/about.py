import streamlit as st

from demo import data, state, ui

demo = state.get_demo()
correct, scored = data.classifier_accuracy(demo)

ui.page_header("About this demo", f"What the {ui.PRODUCT} does, and what this demo does and doesn't include",
               badge="Demo guide")

a, b = st.columns(2, gap="medium")
with a:
    with st.container(border=True):
        ui.section("What it does")
        st.markdown(
            "The Factory Email Assistant reads a manufacturer's business mailbox **read-only** and turns it into:\n\n"
            "- a **dashboard** of enquiries, orders, payments, supplier and logistics emails, with priorities and "
            "action items;\n"
            "- an **email explorer** that pulls out PO numbers, quantities, invoice references, amounts, "
            "payment statements and delivery dates;\n"
            "- **Ask your emails**: answers to business questions, citing the exact source emails.\n\n"
            "It never sends, deletes or changes an email.")
    with st.container(border=True):
        ui.section("How the dashboard works")
        st.markdown(
            "Each received email is classified into one of seven business categories with a priority and an "
            "action-required flag. Every number is an exact count for the selected date range "
            "(Today, Yesterday, 7 days, 30 days, All time or Custom), using India time (IST). The executive "
            "summary is a template filled with those same counts.\n\n"
            f"In this demo the classification is done by **offline rules**. On the sample mailbox they agree with "
            f"the hand-labelled category for **{correct} of {scored}** received emails; an AI model would "
            "classify real-world email more reliably.")
with b:
    with st.container(border=True):
        ui.section("What you can ask")
        st.markdown(
            "- *Summarize the emails from Desai Uniforms* — then *What about their payments?*\n"
            "- *What's the status of PO ABC/PO/7781?* · *Show me the purchase orders we received*\n"
            "- *Which customers say they have made payments?* · *Summarize financial communications this month*\n"
            "- *Which shipments are still pending?*\n"
            "- *Which enquiries have not been replied to?* · *List open action items*\n"
            "- *How many enquiries did we receive this week?* · *Give me an overview of today*\n\n"
            "Questions outside these patterns get a labelled list of the best-matching emails instead of a guess.")
    with st.container(border=True):
        ui.section("How answers are built")
        st.markdown(
            "Answers come from an **offline rules-based engine, not a live AI model**:\n\n"
            "- counts and totals are exact database results;\n"
            "- quotes, quantities, amounts and dates are copied word for word from the emails;\n"
            "- every line cites its source email, and you can click any source to read it;\n"
            "- a payment a customer says they made is shown as a **claim to check against your bank statement**, "
            "never as a confirmed payment;\n"
            "- if nothing matches, it says so rather than filling the gap.")

with st.container(border=True):
    ui.section("About this demo")
    st.markdown(
        f"- **All {demo.email_count} emails are fictional.** The company (Suryodaya Fabrics, a textile mill in "
        "Surat), its customers, suppliers, people, amounts and reference numbers are invented for demonstration.\n"
        "- **Dates move with the calendar.** The sample emails are placed relative to today, so *Today* and "
        "*Yesterday* always have mail.\n"
        "- **Gmail integration is planned but not enabled.** The production version will connect to Gmail with "
        "read-only permission; this demo uses only the bundled sample mailbox.\n"
        "- **AI-powered reasoning is not yet activated.** The full product can use an AI model (Anthropic Claude) "
        "for classification, summaries and free-form questions. This demo makes no AI calls and needs no API key.\n"
        "- Nothing you type is stored: the conversation lives only in your browser session.")
    search = ("local semantic embeddings (on this server)" if demo.search_mode == "semantic"
              else "keyword similarity")
    st.caption(f"Demo status · {demo.email_count} emails loaded · {demo.classified} received emails analysed · "
               f"search: {search} · data prepared in {demo.build_seconds:.0f} s · "
               f"{ui.PRODUCT} by {ui.VENDOR}")
