# PlacidWay WhatsApp Onboarding - Streamlit demo

A single-file (`app.py`) client demo that looks like WhatsApp and behaves like the planned n8n chatbot.
**Everything is simulated** - no WhatsApp, Twilio, PlacidWay API or AI service is called.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Optional tests (no Streamlit needed): `python -m unittest -v test_app`

## Share a live link with the client
1. Put `app.py` and `requirements.txt` in a GitHub repo.
2. Go to https://share.streamlit.io, connect the repo, choose `app.py` as the main file, deploy.
3. Send the generated URL. (Do not put any secrets in the repo - the demo needs none.)

## How to demo it (2 minutes)
1. Type **START** -> bot sends Step 1 with the link.
2. Type **DONE** immediately -> bot says the step is *not complete*. The bot checked the (mock) status API; it does not trust DONE.
3. Click **✅ Complete current step in the portal** (sidebar), then type **DONE** -> "Step 1 complete ✓", next step + link.
4. Try the sidebar **Edge-case message** list (gibberish, emoji, empty, long text, legal/pricing questions...).
5. Tick **Status API is DOWN** and send STATUS -> safe apology, nothing advances; SUPPORT still works.
6. Open the right-hand tabs: **Backend verification**, **n8n workflow trace**, **Session & handoff**.

## What is mock / assumed
- Status API contract is the one *proposed* in the project overview (not approved by PlacidWay).
- Portal links use the documented path structure on `http://www.placidway.com`; they are for demonstration only.
- FAQ text comes from the client's Scope of Work and awaits final approval.
- START, DONE and SIGNED are proposed commands pending client confirmation; PAUSE/STOP behaviour is an assumption.
- The sticky input at the bottom is Streamlit's `st.chat_input` (it has its own send arrow).
- Not run on a live Streamlit server in the build environment; the UI was exercised with a stubbed `streamlit` module.
