"""
auth.py — Fyers OAuth2 Token Manager
Supports two modes:
  1. Auto-capture via local HTTP server (recommended)
  2. Manual URL paste fallback

Run directly: python auth.py
"""
import os
import json
import webbrowser
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "access_token.txt")
TOKEN_META_FILE = os.path.join(BASE_DIR, "token_meta.json")


def get_credentials():
    return {
        "client_id": os.getenv("FYERS_CLIENT_ID", ""),
        "secret_key": os.getenv("FYERS_SECRET_KEY", ""),
        "redirect_uri": os.getenv("FYERS_REDIRECT_URL", "http://127.0.0.1:5000/callback"),
    }


def _make_session():
    creds = get_credentials()
    if not creds["client_id"] or not creds["secret_key"]:
        raise ValueError("Missing FYERS_CLIENT_ID or FYERS_SECRET_KEY in .env file")
    return fyersModel.SessionModel(
        client_id=creds["client_id"],
        secret_key=creds["secret_key"],
        redirect_uri=creds["redirect_uri"],
        response_type="code",
        grant_type="authorization_code",
    )


def generate_auth_url() -> str:
    return _make_session().generate_authcode()


def get_access_token() -> str | None:
    """
    Full login flow:
    1. Open browser to Fyers login
    2. Auto-capture auth_code via local HTTP server
    3. Fallback to manual paste if server fails
    4. Exchange code for token, save to file
    """
    creds = get_credentials()
    session = _make_session()
    auth_url = session.generate_authcode()

    print("\n" + "=" * 50)
    print("🔑 Fyers Authentication")
    print("=" * 50)
    print(f"Opening browser: {auth_url}")
    webbrowser.open(auth_url)

    # Parse host/port from redirect_uri
    parsed = urllib.parse.urlparse(creds["redirect_uri"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5000

    auth_code = None

    class AuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            if "auth_code" in params:
                auth_code = params["auth_code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;text-align:center;padding:2rem'>"
                    b"<h2 style='color:green'>&#10003; Authentication Successful!</h2>"
                    b"<p>You can now close this window and return to the scanner.</p>"
                    b"</body></html>"
                )
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # Suppress server logs

    try:
        print(f"\n⏳ Waiting for login callback on {host}:{port}...")
        httpd = HTTPServer((host, port), AuthHandler)
        while auth_code is None:
            httpd.handle_request()
        httpd.server_close()
        print("✅ Auth code captured automatically!")
    except Exception as e:
        print(f"⚠️ Local server failed ({e}). Fallback to manual input.")
        redirect_url = input("\n📋 Paste the full redirect URL here: ").strip()
        params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
        if "auth_code" not in params:
            print("❌ auth_code not found in URL.")
            return None
        auth_code = params["auth_code"][0]

    if not auth_code:
        return None

    return _exchange_code(session, auth_code)


def exchange_code_for_token(auth_code: str) -> str:
    session = _make_session()
    return _exchange_code(session, auth_code)


def _exchange_code(session, auth_code: str) -> str:
    session.set_token(auth_code)
    response = session.generate_token()
    if response.get("s") != "ok":
        raise RuntimeError(f"Token generation failed: {response}")
    token = response["access_token"]
    _save_token(token)
    return token


def extract_auth_code_from_url(redirect_url: str) -> str:
    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    if "auth_code" not in params:
        raise ValueError("auth_code not found in URL")
    return params["auth_code"][0]


def _save_token(token: str):
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    meta = {
        "generated_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(hours=23)).isoformat(),
    }
    with open(TOKEN_META_FILE, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"✅ Token saved to {TOKEN_FILE}")


def load_token() -> str | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    if os.path.exists(TOKEN_META_FILE):
        with open(TOKEN_META_FILE) as f:
            meta = json.load(f)
        expires = datetime.fromisoformat(meta.get("expires_at", "2000-01-01"))
        if datetime.now() > expires:
            return None
    with open(TOKEN_FILE) as f:
        return f.read().strip() or None


def is_token_valid() -> bool:
    if not os.path.exists(TOKEN_META_FILE):
        return False
    with open(TOKEN_META_FILE) as f:
        meta = json.load(f)
    expires = datetime.fromisoformat(meta.get("expires_at", "2000-01-01"))
    return datetime.now() < expires


def get_token_info() -> dict:
    if not os.path.exists(TOKEN_META_FILE):
        return {"valid": False, "generated_at": None, "expires_at": None}
    with open(TOKEN_META_FILE) as f:
        meta = json.load(f)
    expires = datetime.fromisoformat(meta.get("expires_at", "2000-01-01"))
    return {
        "valid": datetime.now() < expires,
        "generated_at": meta.get("generated_at"),
        "expires_at": meta.get("expires_at"),
    }


if __name__ == "__main__":
    token = get_access_token()
    if token:
        print(f"\n🎉 Success! Token: {token[:20]}...")
        print("You can now run the scanner app.")
    else:
        print("\n❌ Failed to get token.")
