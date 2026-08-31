"""
Obtain a LinkedIn session cookie (li_at + JSESSIONID) from your own machine.

Run this locally — your home/office IP is already trusted by LinkedIn, so the
mobile login endpoint issues cookies without a device challenge. Paste the
printed values into Render's environment variables.

Usage:
    python tools/get_li_at.py                 # reads LINKEDIN_EMAIL / LINKEDIN_PASSWORD from .env
    python tools/get_li_at.py you@mail.com    # prompts for the password
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
            print(f"Login failed: {resp.json()}")
        except Exception:
            print(f"Login failed: {resp.text[:400]}")
        return 1

    print("\nLogin successful. Add these to Render -> Environment:\n")
    print(f"LINKEDIN_LI_AT={li_at}")
    print(f"LINKEDIN_JSESSIONID={jsid}")
    print("\n(Keep LINKEDIN_EMAIL / LINKEDIN_PASSWORD set too — they are used to")
    print(" refresh the session automatically when this cookie expires.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
