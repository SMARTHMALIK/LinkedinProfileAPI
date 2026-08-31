import os
import re
import urllib3
import requests
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class LinkedInSession:
    def __init__(self):
        self._session: requests.Session | None = None

    def login(self) -> requests.Session:
        email = os.getenv("LINKEDIN_EMAIL")
        password = os.getenv("LINKEDIN_PASSWORD")
        li_at = os.getenv("LINKEDIN_LI_AT")
        jsessionid = os.getenv("LINKEDIN_JSESSIONID", "")

        sess = requests.Session()
        sess.headers.update(HEADERS)
        sess.verify = False
        sess.max_redirects = 5
        self._session = sess

        # Priority 1: programmatic login with email + password
        if email and password:
            print(f"[DEBUG] Logging in with LINKEDIN_EMAIL ({email[:4]}...)")
            if self._login_with_credentials(sess, email, password):
                return sess
            print("[DEBUG] Credential login failed — falling back to cookie env vars")

        # Priority 2: manual li_at cookie from env
        if li_at:
            sess.cookies.set("li_at", li_at, domain=".linkedin.com", path="/")
            if jsessionid:
                sess.cookies.set("JSESSIONID", jsessionid, domain=".linkedin.com", path="/")
                print(f"[DEBUG] Using manual li_at + JSESSIONID from env")
            else:
                try:
                    resp = sess.get(
                        "https://www.linkedin.com/feed/",
                        allow_redirects=True,
                        timeout=10,
                    )
                    jsid = sess.cookies.get("JSESSIONID", "")
                    print(f"[DEBUG] Warm-up: status={resp.status_code}, JSESSIONID={jsid!r}")
                except requests.exceptions.TooManyRedirects:
                    print("[DEBUG] Warm-up redirect loop")
                except Exception as exc:
                    print(f"[DEBUG] Warm-up error: {exc}")
            return sess

        print("[DEBUG] No credentials configured — running unauthenticated (public HTML only).")
        return sess

    def _login_with_credentials(self, sess: requests.Session, email: str, password: str) -> bool:
        """
        POST email+password to LinkedIn's login form.
        Uses curl_cffi with Chrome impersonation so LinkedIn serves the real login
        page (not a bot-wall) even from datacenter IPs. Returns True on success.
        """
        try:
            from curl_cffi.requests import Session as _CurlSess
            curl_sess = _CurlSess(impersonate="chrome120")
            use_curl = True
        except ImportError:
            curl_sess = None
            use_curl = False

        _get = curl_sess.get if use_curl else sess.get
        _post = curl_sess.post if use_curl else sess.post

        try:
            # Step 1: GET login page for CSRF token
            resp = _get(
                "https://www.linkedin.com/login",
                headers={
                    "Referer": "https://www.linkedin.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
            print(f"[DEBUG] Login page GET: {resp.status_code} (curl_cffi={use_curl})")

            # Try multiple patterns — LinkedIn has changed this field across versions
            csrf_match = (
                re.search(r'name=["\']loginCsrfParam["\'][^>]*value=["\']([^"\']+)["\']', resp.text)
                or re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']loginCsrfParam["\']', resp.text)
                or re.search(r'"loginCsrfParam"\s*[,:\s]*"([^"]+)"', resp.text)
                or re.search(r'loginCsrfParam[^"\']*["\']([a-zA-Z0-9_\-]{8,})["\']', resp.text)
            )
            if not csrf_match:
                print(f"[DEBUG] loginCsrfParam not found. Page snippet: {resp.text[:800]!r}")
                return False
            csrf_token = csrf_match.group(1)
            print(f"[DEBUG] CSRF token found: {csrf_token[:12]}...")

            # Step 2: POST credentials
            resp = _post(
                "https://www.linkedin.com/checkpoint/lg/login-submit",
                data={
                    "session_key": email,
                    "session_password": password,
                    "loginCsrfParam": csrf_token,
                    "trk": "guest_homepage-basic_sign_in-button",
                    "_d": "d",
                },
                headers={
                    "Referer": "https://www.linkedin.com/login",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.linkedin.com",
                },
                timeout=15,
            )
            print(f"[DEBUG] Login POST: status={resp.status_code}, url={str(resp.url)[:80]}")

            # Extract li_at from whichever session has it
            if use_curl:
                li_at = curl_sess.cookies.get("li_at", "")
                jsid = curl_sess.cookies.get("JSESSIONID", "")
            else:
                li_at = sess.cookies.get("li_at", "")
                jsid = sess.cookies.get("JSESSIONID", "")

            print(f"[DEBUG] Cookies: li_at={'yes' if li_at else 'NO'}, JSESSIONID={'yes' if jsid else 'NO'}")

            if li_at:
                # Transfer cookies to the long-lived requests session used by Voyager API
                sess.cookies.set("li_at", li_at, domain=".linkedin.com", path="/")
                if jsid:
                    sess.cookies.set("JSESSIONID", jsid, domain=".linkedin.com", path="/")
                print("[DEBUG] Login successful!")
                return True

            url_str = str(resp.url)
            if any(k in url_str for k in ("checkpoint", "challenge", "verification", "security")):
                print(f"[DEBUG] Security challenge triggered: {url_str}")
                print("[DEBUG] Go to LinkedIn in a browser, approve the login from this IP, then redeploy.")
                return False

            print(f"[DEBUG] Login failed — no li_at. Snippet: {resp.text[:400]!r}")
            return False

        except Exception as exc:
            print(f"[DEBUG] Login error: {type(exc).__name__}: {exc}")
            return False

    def relogin(self) -> bool:
        """Re-authenticate when the session expires. Returns True if successful."""
        email = os.getenv("LINKEDIN_EMAIL")
        password = os.getenv("LINKEDIN_PASSWORD")
        if not (email and password):
            return False
        print("[DEBUG] Session expired — attempting re-login with credentials")
        sess = self._session
        if sess is None:
            sess = requests.Session()
            sess.headers.update(HEADERS)
            sess.verify = False
            sess.max_redirects = 5
            self._session = sess
        sess.cookies.clear()
        return self._login_with_credentials(sess, email, password)

    def is_authenticated(self) -> bool:
        return bool(self._session and self._session.cookies.get("li_at"))

    def get_session(self) -> requests.Session:
        if self._session is None:
            self.login()
        return self._session

    def get_voyager_headers(self) -> dict:
        sess = self.get_session()
        jsessionid = sess.cookies.get("JSESSIONID", "")
        return {
            "csrf-token": jsessionid,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "x-li-track": (
                '{"clientVersion":"1.13.1665","osName":"web","timezoneOffset":5.5,'
                '"timezone":"Asia/Kolkata","deviceFormFactor":"DESKTOP",'
                '"mpName":"voyager-web","displayDensity":1,'
                '"displayWidth":1920,"displayHeight":1080}'
            ),
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Origin": "https://www.linkedin.com",
            "x-li-page-instance": "urn:li:page:d_flagship3_profile_view_base;AAAAAAAAAA==",
        }


session = LinkedInSession()
