import os
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
}


class LinkedInSession:
    def __init__(self):
        self._session: requests.Session | None = None

    def login(self) -> requests.Session:
        li_at = os.getenv("LINKEDIN_LI_AT")
        if not li_at:
            raise EnvironmentError(
                "LINKEDIN_LI_AT must be set in your .env file. "
                "See README for how to extract it from your browser."
            )

        # JSESSIONID doubles as the CSRF token for all Voyager API calls.
        # If provided in .env, we skip any warm-up web request (avoids proxy issues).
        jsessionid = os.getenv("LINKEDIN_JSESSIONID", "")

        sess = requests.Session()
        sess.headers.update(HEADERS)
        sess.verify = False
        sess.max_redirects = 5

        sess.cookies.set("li_at", li_at, domain=".linkedin.com")
        if jsessionid:
            sess.cookies.set("JSESSIONID", jsessionid, domain=".linkedin.com")
            print(f"[DEBUG] Using JSESSIONID from .env: {jsessionid!r}")
        else:
            # Try a lightweight warm-up to get JSESSIONID set by LinkedIn.
            # This may fail on corporate proxy networks — a TooManyRedirects
            # exception here is expected and handled gracefully.
            try:
                resp = sess.get(
                    "https://www.linkedin.com/",
                    allow_redirects=True,
                    timeout=10,
                )
                jsid = sess.cookies.get("JSESSIONID", "")
                print(f"[DEBUG] Warm-up status: {resp.status_code}, JSESSIONID: {jsid!r}")
            except requests.exceptions.TooManyRedirects:
                print("[DEBUG] Warm-up redirect loop — set LINKEDIN_JSESSIONID in .env to fix this")
            except Exception as exc:
                print(f"[DEBUG] Warm-up error: {exc}")

        self._session = sess
        return sess

    def get_session(self) -> requests.Session:
        if self._session is None:
            self.login()
        return self._session

    def get_voyager_headers(self) -> dict:
        sess = self.get_session()
        jsessionid = sess.cookies.get("JSESSIONID", "")
        # LinkedIn compares csrf-token against JSESSIONID — keep the full value
        # including the "ajax:" prefix. Stripping it causes "CSRF check failed" 403.
        csrf = jsessionid
        print(f"[DEBUG] JSESSIONID={jsessionid!r}  csrf-token={csrf!r}")
        return {
            "csrf-token": csrf,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "x-li-track": '{"clientVersion":"1.13.1665"}',
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Origin": "https://www.linkedin.com",
        }


session = LinkedInSession()
