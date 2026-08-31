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

        # Priority 1: a pre-obtained session cookie.
        # Preferred on cloud hosts: LinkedIn issues a device challenge for logins
        # from datacenter IPs, and repeated failed attempts can flag the account.
        # Generate these locally with `python tools/get_li_at.py`.
        if li_at:
            sess.cookies.set("li_at", li_at, domain=".linkedin.com", path="/")
            if jsessionid:
                sess.cookies.set("JSESSIONID", jsessionid, domain=".linkedin.com", path="/")
                print("[DEBUG] Using li_at + JSESSIONID from env")
            else:
                print("[DEBUG] Using li_at from env (no JSESSIONID — Voyager calls may 403)")
            return sess

        # Priority 2: programmatic login with email + password.
        # Works from residential IPs; datacenter IPs usually get a CHALLENGE.
        if email and password:
            print(f"[DEBUG] Logging in with LINKEDIN_EMAIL ({email[:4]}...)")
            if self._login_with_credentials(sess, email, password):
                return sess
            print("[DEBUG] Credential login failed.")

        print("[DEBUG] No usable credentials — running unauthenticated (public HTML only).")
        return sess

    # Mobile app headers — LinkedIn's /uas/authenticate is the programmatic login
    # endpoint used by the Android/iOS apps. It accepts JSESSIONID (from the GET)
    # as the CSRF token instead of the React-rendered loginCsrfParam on the web form.
    _MOBILE_HEADERS = {
        "X-Li-User-Agent": (
            "LIAuthLibrary:3.2.4 com.linkedin.android:4.1.854 "
            "Asus_ASUS_Z01QD:android_9"
        ),
        "User-Agent": "ANDROID OS",
        "X-User-Language": "en",
        "X-User-Locale": "en_US",
        "Accept-Language": "en-us",
        "X-Li-Track": (
            '{"clientVersion":"4.1.854","clientMinorVersion":"854",'
            '"osName":"Android OS","osVersion":"android_9",'
            '"model":"Asus_ASUS_Z01QD","displayDensity":"2.0",'
            '"displayWidth":"1080","displayHeight":"1920",'
            '"targetSdkVersion":"28","buildId":"PPR1.180610.011"}'
        ),
    }

    def _login_with_credentials(self, sess: requests.Session, email: str, password: str) -> bool:
        """
        Authenticate via LinkedIn's mobile API endpoint (/uas/authenticate).
        This is the same flow used by the LinkedIn Android/iOS apps — no browser
        CSRF token required. Works from datacenter IPs where the web form is blocked.
        """
        _AUTH_URL = "https://www.linkedin.com/uas/authenticate"
        try:
            # Step 1: GET to obtain a fresh JSESSIONID (used as CSRF in the POST)
            resp = sess.get(
                _AUTH_URL,
                headers=self._MOBILE_HEADERS,
                timeout=15,
            )
            print(f"[DEBUG] /uas/authenticate GET: {resp.status_code}")

            jsid = sess.cookies.get("JSESSIONID", "")
            if not jsid:
                print(f"[DEBUG] No JSESSIONID from GET — body: {resp.text[:300]!r}")
                return False
            print(f"[DEBUG] JSESSIONID obtained: {jsid[:20]}...")

            # Step 2: POST credentials with JSESSIONID as the CSRF token
            resp = sess.post(
                _AUTH_URL,
                data={
                    "session_key": email,
                    "session_password": password,
                    "JSESSIONID": jsid,
                },
                headers={
                    **self._MOBILE_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=15,
            )
            print(f"[DEBUG] /uas/authenticate POST: {resp.status_code}")

            li_at = sess.cookies.get("li_at", "")
            new_jsid = sess.cookies.get("JSESSIONID", jsid)
            print(f"[DEBUG] Cookies: li_at={'yes' if li_at else 'NO'}, JSESSIONID={'yes' if new_jsid else 'NO'}")

            if li_at:
                print("[DEBUG] Login successful!")
                return True

            # Inspect the response for a challenge we can complete automatically
            try:
                body = resp.json()
            except Exception:
                print(f"[DEBUG] Login response (non-JSON): {resp.text[:300]!r}")
                return False

            print(f"[DEBUG] Login response JSON: {body}")
            result = body.get("login_result", "")

            if result == "CHALLENGE":
                challenge_url = body.get("challenge_url", "")
                if challenge_url and self._follow_challenge(sess, challenge_url):
                    return True
                print(
                    "[DEBUG] Challenge could not be completed automatically. "
                    "This IP is untrusted by LinkedIn. Either set HTTPS_PROXY to a "
                    "residential proxy, or run tools/get_li_at.py locally and set "
                    "LINKEDIN_LI_AT + LINKEDIN_JSESSIONID as env vars."
                )
            elif result in ("BAD_PASSWORD", "INVALID_CREDENTIALS"):
                print("[DEBUG] Credentials rejected — check LINKEDIN_EMAIL / LINKEDIN_PASSWORD.")

            return False

        except requests.exceptions.TooManyRedirects:
            print("[DEBUG] Login redirect loop")
            return False
        except Exception as exc:
            print(f"[DEBUG] Login error: {type(exc).__name__}: {exc}")
            return False

    def _follow_challenge(self, sess: requests.Session, challenge_url: str) -> bool:
        """
        LinkedIn's 'direct-login-submit' challenge is a soft device check: following
        the URL with the same session cookies can complete the login without a PIN.
        Returns True if an li_at cookie was issued.
        """
        print(f"[DEBUG] Following challenge URL: {challenge_url[:110]}")
        try:
            resp = sess.get(
                challenge_url,
                headers={
                    **self._MOBILE_HEADERS,
                    "Referer": "https://www.linkedin.com/",
                },
                allow_redirects=True,
                timeout=15,
            )
            print(f"[DEBUG] Challenge GET: {resp.status_code}, url={str(resp.url)[:100]}")

            li_at = sess.cookies.get("li_at", "")
            if li_at:
                print("[DEBUG] Challenge cleared — li_at issued!")
                return True

            # Some variants need a POST back to the same endpoint
            resp = sess.post(
                challenge_url,
                headers={
                    **self._MOBILE_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": str(resp.url),
                },
                allow_redirects=True,
                timeout=15,
            )
            print(f"[DEBUG] Challenge POST: {resp.status_code}, url={str(resp.url)[:100]}")

            li_at = sess.cookies.get("li_at", "")
            if li_at:
                print("[DEBUG] Challenge cleared on POST — li_at issued!")
                return True

            print(f"[DEBUG] Challenge not cleared. Body: {resp.text[:300]!r}")
            return False

        except Exception as exc:
            print(f"[DEBUG] Challenge follow error: {type(exc).__name__}: {exc}")
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
        # LinkedIn stores JSESSIONID quoted ("ajax:123..."). The csrf-token header
        # must carry the value WITHOUT the surrounding quotes but WITH the ajax:
        # prefix — sending the quotes causes "CSRF check failed" 403s.
        jsessionid = sess.cookies.get("JSESSIONID", "").strip('"')
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
