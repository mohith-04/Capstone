"""
config.py — Central configuration loader
=========================================
All environment variables are read here and re-exported as typed Python
constants. Every other module imports from config.py rather than calling
os.getenv() directly. This makes the config surface easy to audit, easy to
mock in tests, and trivial to swap for a secrets manager later.

Usage
-----
    from config import PORT, FRONTEND_ORIGIN

Setup
-----
    cp .env.example .env
    # fill in values, then restart the server
"""

import os
from dotenv import load_dotenv

# load_dotenv() reads .env in the working directory (silently skips if absent,
# which is fine in CI/CD where real env vars are injected directly).
load_dotenv()

# Server
PORT: int = int(os.getenv("PORT", "8000"))
FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

# Twilio (Stage 5 — hospital SMS pre-alerting).
# Empty strings are safe defaults; the alerting module checks whether these
# are set before attempting a real SMS send, and falls back to logging.
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
HOSPITAL_PHONE_NUMBER: str = os.getenv("HOSPITAL_PHONE_NUMBER", "")

# Hospital pre-alert ETA threshold in seconds.
# When the ambulance ETA drops below this value, the alert fires.
# Default: 300 s (5 minutes) — enough lead time for ER preparation.
ALERT_ETA_THRESHOLD_SECONDS: int = int(
    os.getenv("ALERT_ETA_THRESHOLD_SECONDS", "300")
)
