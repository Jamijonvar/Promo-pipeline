"""
gmail_auth_setup.py

One-time (and re-runnable) setup script that authorizes this pipeline to send
email from jonnywolfmanagement@gmail.com via the Gmail API.

IMPORTANT: This must be run locally on a machine with a real browser, by you
(Jonny) -- not from a remote/sandboxed environment. The consent flow spins up
a local web server and opens your browser to a Google "Allow this app to
access your Gmail" screen; the redirect back to that local server has to
happen on the same machine. That's a hard requirement of how Google's
installed-app OAuth flow works, not a choice made here.

What it does:
  1. Looks for the OAuth client file you downloaded from Google Cloud Console
     (Promo-Pipeline project > Google Auth Platform > Clients).
  2. Opens your browser to Google's consent screen, scoped to ONLY
     'gmail.send' -- this app can send mail as you, nothing else. It cannot
     read your inbox, delete anything, or access other Google services.
  3. Saves the resulting token to a local file so future runs (drafting,
     sending) don't need you to log in again, until the token is revoked or
     expires.

Setup:
  pip install google-auth-oauthlib google-api-python-client

  Move the client-secret JSON you downloaded from Google Cloud Console into:
    ~/Documents/promo pipeline (non git)/gmail_oauth_client.json
  (Same non-git folder the Sheets service account credentials already live
  in -- this file should never be committed to the repo.)

Run:
  python3 "Py scripts/gmail_auth_setup.py"

This script only requests consent and saves a token. It does not send
anything. Sending is a separate, later step.
"""

import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# --- Configuration ----------------------------------------------------------

CLIENT_SECRET_PATH = os.path.expanduser(
    "~/Documents/promo pipeline (non git)/gmail_oauth_client.json"
)
TOKEN_PATH = os.path.expanduser(
    "~/Documents/promo pipeline (non git)/gmail_token.json"
)

# Deliberately minimal -- this app can send mail as you and nothing else.
# It cannot read, search, or delete anything in your inbox.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_credentials():
    """
    Returns valid Credentials, running the consent flow if needed and
    caching the result to TOKEN_PATH for next time.
    """
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.valid:
        print(f"[ok] Already authorized. Token cached at {TOKEN_PATH}")
        return creds

    if creds and creds.expired and creds.refresh_token:
        print("[info] Token expired -- refreshing without needing browser login again.")
        creds.refresh(Request())
        _save_token(creds)
        return creds

    if not os.path.exists(CLIENT_SECRET_PATH):
        raise FileNotFoundError(
            f"No OAuth client file found at '{CLIENT_SECRET_PATH}'. "
            f"Download it from Google Cloud Console (Promo-Pipeline project > "
            f"Google Auth Platform > Clients > promo-pipeline-gmail-sender > "
            f"Download JSON) and move it there first."
        )

    print("[info] No valid token found -- starting browser consent flow.")
    print("[info] This will open a browser tab asking you to sign in as")
    print("       jonnywolfmanagement@gmail.com and approve 'Send email on")
    print("       your behalf' access. Nothing else is requested.")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(f"[ok] Authorized. Token saved to {TOKEN_PATH}")
    return creds


def _save_token(creds):
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())


if __name__ == "__main__":
    try:
        get_credentials()
    except Exception as e:
        print(f"[error] {e}")
        sys.exit(1)
