import os
import webbrowser
from fyers_apiv3 import fyersModel
from dotenv import load_dotenv
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

load_dotenv()

# Step 1: Get Credentials from .env
client_id = os.getenv("FYERS_CLIENT_ID")
secret_key = os.getenv("FYERS_SECRET_KEY")
redirect_uri = os.getenv("FYERS_REDIRECT_URL")

def get_access_token():
    if not client_id or "YOUR" in client_id:
        print("❌ Error: Please update FYERS_CLIENT_ID in .env file.")
        return None

    # Create session object
    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )

    # Generate Auth URL
    auth_url = session.generate_authcode()
    print("\n" + "="*50)
    print("🔑 FYERS ACCESS TOKEN EXPIRED OR MISSING")
    print("="*50)
    print("1. Opening your browser for login...")
    print(f"2. URL: {auth_url}")
    
    webbrowser.open(auth_url)

    print("\n3. Waiting for authentication callback...")
    
    parsed_uri = urllib.parse.urlparse(redirect_uri)
    host = parsed_uri.hostname or "127.0.0.1"
    port = parsed_uri.port or 5000

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
                self.wfile.write(b"<html><body><h2>Authentication successful!</h2><p>You can safely close this window.</p></body></html>")
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h2>Authentication failed!</h2><p>Missing auth_code.</p></body></html>")

        def log_message(self, format, *args):
            pass  # Suppress logging logger

    try:
        httpd = HTTPServer((host, port), AuthHandler)
        while auth_code is None:
            httpd.handle_request()
        httpd.server_close()
    except Exception as e:
        print(f"❌ Error starting local server on {host}:{port}. Is the port in use? Error: {e}")
        auth_code = input("\n👉 Fallback - Paste the Auth Code here (or press Enter to cancel): ").strip()

    if not auth_code:
        print("❌ Login cancelled.")
        return None
        
    print("\n✅ Auth Code Received automatically!")

    try:
        session.set_token(auth_code)
        response = session.generate_token()
        
        if "access_token" in response:
            access_token = response["access_token"]
            # Save token to file
            token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_token.txt")
            with open(token_file, "w") as f:
                f.write(access_token)
            print("\n✅ Access Token generated and saved successfully!")
            print("This token is valid for 24 hours.")
            return access_token
        else:
            print(f"❌ Error generating token: {response.get('message', response)}")
            return None
    except Exception as e:
        print(f"❌ Exception during token generation: {e}")
        return None

if __name__ == "__main__":
    get_access_token()

