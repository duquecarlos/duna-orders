import streamlit as st
from duna_orders.config import settings

st.set_page_config(page_title="Duna Orders", page_icon="🛒", layout="wide")

st.title("🛒 Duna Orders — Pilot MVP")
st.caption("WhatsApp-native order control for small businesses")

col1, col2, col3 = st.columns(3)
col1.metric("Active client", settings.active_client_name)
col2.metric("LLM provider", settings.llm_provider)
col3.metric("Environment", settings.app_env)

st.divider()
st.subheader("Setup status")

checks = [
    ("Anthropic API key", bool(settings.anthropic_api_key)),
    ("Google Sheets credentials file", settings.google_sheets_credentials_path.exists()),
    ("Active client sheet ID", bool(settings.active_client_sheet_id)),
]
for label, ok in checks:
    st.write(("✅ " if ok else "❌ ") + label)

st.divider()
st.info("Next: M1 — storage layer (read/write products & orders).")