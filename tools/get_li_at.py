"""
Obtain a LinkedIn session cookie from your own machine.

Run this locally — a home/office IP is trusted by LinkedIn, so the mobile login
endpoint usually issues cookies without a device challenge. Paste the printed
LINKEDIN_COOKIES value into Render's environment variables.

Usage:
    python tools/get_li_at.py                 # reads LINKEDIN_EMAIL / LINKEDIN_PASSWORD from .env
    python tools/get_li_at.py you@mail.com    # prompts for the password

If this returns CHALLENGE, LinkedIn's risk engine has flagged the account and
scripted logins will keep being refused. Copy the cookies out of a browser
instead — that session is already verified, so it survives far longer than one
minted here:

    1. Log in to linkedin.com in Chrome
    2. DevTools (F12) -> Application -> Storage -> Cookies -> https://www.linkedin.com
    3. Copy the values of `li_at`, `JSESSIONID`, `liap` and `bcookie`
    4. Set them as one env var, semicolon separated:
       LINKEDIN_COOKIES=li_at=AQED...; JSESSIONID="ajax:123..."; liap=true; bcookie=...
"""

import os
import sys
import getpass

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

AUTH_URL = "https://www.linkedin.com/uas/authenticate"

MOBILE_HEADERS = {
    "X-Li-User-Agent": (
        "LIAuthLibrary:3.2.4 com.linkedin.android:4.1.854 Asus_ASUS_Z01QD:android_9"
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


def main() -> int:
    if len(sys.argv) > 1:
        email = sys.argv[1]
        password = getpass.getpass("LinkedIn password: ")
    else:
        email = os.getenv("LINKEDIN_EMAIL")
        password = os.getenv("LINKEDIN_PASSWORD")

    if not (email and password):
        print("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env, or pass the email as an argument.")
        return 1

    sess = requests.Session()
    sess.verify = False

    resp = sess.get(AUTH_URL, headers=MOBILE_HEADERS, timeout=15)
    print(f"GET  /uas/authenticate -> {resp.status_code}")

    jsid = sess.cookies.get("JSESSIONID", "")
    if not jsid:
        print("No JSESSIONID returned. LinkedIn may be blocking this network.")
        return 1

    resp = sess.post(
        AUTH_URL,
        data={
            "session_key": email,
            "session_password": password,
            "JSESSIONID": jsid,
        },
        headers={**MOBILE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    print(f"POST /uas/authenticate -> {resp.status_code}")

    li_at = sess.cookies.get("li_at", "")
    jsid = sess.cookies.get("JSESSIONID", jsid)

    if not li_at:
        try:
            body = resp.json()
        except Exception:
            print(f"Login failed: {resp.text[:400]}")
            return 1

        print(f"Login failed: {body}")
        if body.get("login_result") == "CHALLENGE":
            print(
                "\nLinkedIn is challenging this login, which means the account has been\n"
                "flagged — repeated scripted logins do it. Sessions minted while flagged\n"
                "get invalidated almost immediately.\n\n"
                "Do this instead:\n"
                "  1. Open linkedin.com in a browser and clear any security prompt\n"
                "  2. Copy the cookies from DevTools (see this file's docstring)\n"
                "  3. Set them as LINKEDIN_COOKIES\n"
            )
        return 1

    # Export every cookie the login produced. li_at alone is not a session:
    # LinkedIn expects the supporting cookies (liap, bcookie, bscookie, lidc)
    # to travel with it, and rejects requests that arrive without them.
    cookie_string = "; ".join(f"{c.name}={c.value}" for c in sess.cookies)

    print("\nVerifying the session against /voyager/api/me ...")
    check = sess.get(
        "https://www.linkedin.com/voyager/api/me",
        headers={
            "csrf-token": (jsid or "").strip('"'),
            "x-restli-protocol-version": "2.0.0",
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Referer": "https://www.linkedin.com/feed/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        },
        timeout=15,
        allow_redirects=False,
    )
    if check.status_code == 200:
        print("  OK — the session works.\n")
    else:
        print(
            f"  WARNING: got {check.status_code}, not 200. LinkedIn issued a cookie but\n"
            "  is already refusing it, which means the account is flagged. Use the\n"
            "  browser method described in this file's docstring instead.\n"
        )

    print("Add this to Render -> Environment:\n")
    print(f"LINKEDIN_COOKIES={cookie_string}")
    print("\nOr, for the older two-variable form:\n")
    print(f"LINKEDIN_LI_AT={li_at}")
    print(f"LINKEDIN_JSESSIONID={jsid}")
    print("\nTreat these as passwords — anyone holding them is logged in as you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
