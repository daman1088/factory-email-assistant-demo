# Factory Email Assistant: Streamlit demo

This is a customer demo of the Factory Email Assistant, built for Streamlit Community Cloud. It runs on a **fictional** textile-mill mailbox of 123 emails from Suryodaya Fabrics.

**Pages:** Dashboard, Email Explorer, Ask Your Emails (an offline, rules-based chatbot with clickable source citations) and About.

**Requirements:** none beyond the packages in `streamlit_app/requirements.txt`. There's no API key, no paid AI service, no Gmail connection and no external database.

*This repository is generated from the main project by `streamlit_app/deploy/make_package.py`. Make changes there and rebuild, rather than editing here.*

## Layout

```
streamlit_app/streamlit_app.py   entry point (Streamlit Community Cloud "Main file path")
streamlit_app/requirements.txt   dependencies (Python 3.12)
streamlit_app/demo/              data loading, chat, access gate, UI components
streamlit_app/views/             the four pages
backend/app/                     the subset of the main app's backend the demo reuses (offline rules only)
backend/mock_data/emails.json    the 123 fictional emails
.streamlit/config.toml           theme
.streamlit/secrets.toml.example  the one secret: DEMO_ACCESS_CODE
```

**How it starts up:**

1. An embedded PostgreSQL 16 + pgvector (the `pgserver` pip package) starts in a temporary folder.
2. The emails are loaded and classified by offline rules.
3. Everything is rebuilt on every restart, and again after midnight India time. Nothing is persisted.

## Run locally

You need Python 3.12. Run these commands from the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r streamlit_app/requirements.txt
cp -n .streamlit/secrets.toml.example .streamlit/secrets.toml   # then set DEMO_ACCESS_CODE
.venv/bin/streamlit run streamlit_app/streamlit_app.py           # http://localhost:8501
```

## Deploy on Streamlit Community Cloud

1. Push this repository to a **private** GitHub repository. You need admin rights on it.
2. Go to https://share.streamlit.io and click **Create app**, then **Yup, I have an app**. Connect GitHub and allow private-repository access if asked.
3. Fill in the deployment form:
   - **Repository:** this repo.
   - **Branch:** `main`.
   - **Main file path:** `streamlit_app/streamlit_app.py`.
   - **App URL:** optional.
4. Open **Advanced settings**:
   - **Python version:** **3.12**. It can't be changed later without deleting and redeploying the app.
   - **Secrets:** paste `DEMO_ACCESS_CODE = "<a long random phrase>"`.
   - Click **Save**.
5. Click **Deploy**. Expect a few minutes for the first build, then up to about a minute for the first page load.
6. Check before sharing:
   - the access page rejects a wrong code;
   - **About** shows "123 emails loaded · 100 received emails analysed";
   - **Dashboard → Today** shows 14 received;
   - "Summarize the emails from Desai Uniforms" works, and a source opens its email.
7. Share it, using one of these options:
   - **Private app:** an app from a private repo is visible only to people you invite. Use **Share** and enter your friend's email address; they sign in with Google or with an emailed link. The free tier allows one private app at a time.
   - **Public app:** use **Share → Make this app public**. Anyone with the link reaches the access page, so send the access code separately.

To change the access code later, go to **App settings → Secrets**.

## Security

- **Access gate:** one shared access code, compared in constant time. It allows 5 attempts per session and then locks for 60 seconds. With no code configured, the app stays locked.
- **Untrusted email text:** it's HTML-escaped where the page renders HTML, and chat answers can't render images or LaTeX from email text.
- **Fictional data only:** no real customer data, API keys or OAuth tokens. All addresses use the reserved `.example` domain.
- **No paid AI:** the AI-provider code isn't included, and the app forces the offline rules engine.

## Known limitations

- **Embedded Postgres on Linux not yet proven.** Before handing the link to anyone, make the first deployment a private test. The embedded Postgres (`pgserver` 0.1.4) ships official Linux x86_64 wheels. On a Mac it has been verified natively and under QEMU x86_64 emulation, but it hasn't yet run on a real Linux x86_64 host.
  - If it fails to start, visitors see a short error message, and the details appear under **Manage app → logs**.
- **Rules, not AI.** Answers are rules-based templates. The classifier matches the dataset's gold labels for 95 of 100 received emails.
- **Sign-in on refresh.** Refreshing the browser starts a new session, so visitors enter the access code again.
- **Wake-up delay.** Free apps sleep when unused. The first visit after a sleep takes about a minute while the app restarts and rebuilds the demo data.
