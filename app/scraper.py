"""
Fetches LinkedIn profile data via direct HTTP calls to LinkedIn's internal APIs.

Primary source is the authenticated Voyager Dash API. LinkedIn retired the
classic REST endpoints (/identity/profiles/{id} and /profileView now return
410 Gone), so everything goes through /identity/dash/*.

Public HTML is used as an unauthenticated fallback and to recover the full
"City, State, Country" location string that the Dash Profile entity omits.
"""

import json
import os
import re
import urllib.parse

import requests as _requests

from app.auth import session, SessionInvalid

BASE_URL = "https://www.linkedin.com/voyager/api"

_DEAD_SESSION_MSG = (
    "LinkedIn has rejected the stored session cookie — it is no longer valid. "
    "This is not throttling and will not clear on its own; the cookie has to be "
    "replaced. Copy a fresh li_at from a logged-in browser (see 'Refreshing the "
    "session' in the README) and update LINKEDIN_COOKIES / LINKEDIN_LI_AT."
)


def extract_public_id(url_or_id: str) -> str:
    """Pull the vanity slug out of a LinkedIn URL, or accept a bare slug."""
    cleaned = url_or_id.strip().rstrip("/")
    match = re.search(r"linkedin\.com/in/([^/?#\s]+)", cleaned)
    if match:
        return match.group(1).rstrip("/")
    if re.fullmatch(r"[A-Za-z0-9\-_\.]+", cleaned):
        return cleaned
    raise ValueError(
        f"Cannot extract a LinkedIn public identifier from: '{url_or_id}'. "
        "Expected a URL like https://www.linkedin.com/in/john-doe or a bare slug."
    )


# ─────────────────────────── authenticated API ───────────────────────────────

def _get(path: str, public_id: str = "", params: dict = None) -> dict | None:
    """
    One authenticated Voyager API call. Returns the parsed JSON, or None when
    the profile/section is unavailable (404/410) or the request was rejected.
    """
    url = f"{BASE_URL}{path}"
    referer = (
        f"https://www.linkedin.com/in/{public_id}/"
        if public_id
        else "https://www.linkedin.com/"
    )

    sess = session.get_session()
    headers = session.get_voyager_headers()
    headers["Referer"] = referer

    # A Voyager API call never legitimately redirects. LinkedIn answers a
    # rejected cookie by bouncing the request back to its own URL in a loop,
    # sending `Set-Cookie: li_at=delete me` on every hop, so treat any 3xx as a
    # dead session and stop rather than burning redirects to reach the same
    # conclusion.
    try:
        resp = sess.get(
            url, headers=headers, params=params, timeout=15, allow_redirects=False
        )
    except _requests.exceptions.TooManyRedirects:
        session.mark_invalid(f"{path} exhausted redirects")
        raise SessionInvalid(_DEAD_SESSION_MSG)

    if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")[:100]
        print(f"[DEBUG] GET {path} → {resp.status_code} redirect to {location}")
        session.mark_invalid(f"{path} redirected to {location}")
        raise SessionInvalid(_DEAD_SESSION_MSG)

    print(f"[DEBUG] GET {path} → {resp.status_code}")

    # Genuine absence — the profile or section does not exist / is not visible.
    if resp.status_code in (404, 410):
        return None

    if resp.status_code == 401:
        # Session expired mid-request — re-authenticate and retry once.
        if not session.relogin():
            session.mark_invalid(f"{path} returned 401 and re-login failed")
            raise SessionInvalid(_DEAD_SESSION_MSG)
        sess = session.get_session()
        headers = session.get_voyager_headers()
        headers["Referer"] = referer
        resp = sess.get(
            url, headers=headers, params=params, timeout=15, allow_redirects=False
        )

    if resp.status_code in (400, 403):
        print(f"[DEBUG] {resp.status_code} body: {resp.text[:300]}")
        return None

    if resp.status_code == 429:
        raise RuntimeError(f"LinkedIn rate limit: {resp.text[:200]}")

    resp.raise_for_status()
    return resp.json()


def _dash_profile(public_id: str) -> tuple[dict, str]:
    """
    Fetch the base Profile entity. Returns (profile_entity, profile_urn).
    The URN keys every subsequent section request.
    """
    data = _get(
        "/identity/dash/profiles",
        public_id,
        params={"q": "memberIdentity", "memberIdentity": public_id},
    )
    if not data:
        return {}, ""

    profile = next(
        (o for o in data.get("included", []) if o.get("$type", "").endswith(".Profile")),
        None,
    )
    if not profile:
        return {}, ""

    urn = profile.get("entityUrn", "")
    print(f"[DEBUG] Profile: {profile.get('firstName')} {profile.get('lastName')}")
    return profile, urn


def _dash_section(section: str, urn: str, public_id: str, type_suffix: str) -> list:
    """
    Fetch one profile section. Responses are LinkedIn's normalized format —
    a flat `included` array of typed entities — so filter by `$type` suffix.
    """
    if not urn:
        return []
    try:
        data = _get(
            f"/identity/dash/{section}",
            public_id,
            params={"q": "viewee", "profileUrn": urn},
        )
    except SessionInvalid:
        # A dead session affects every remaining call, so surface it instead of
        # quietly returning a profile with all its sections mysteriously empty.
        raise
    except RuntimeError as exc:
        # A throttled section shouldn't discard the profile we already have —
        # return what we can and leave this section empty.
        print(f"[DEBUG] {section} unavailable: {exc}")
        return []
    if not data:
        return []
    items = [
        o for o in data.get("included", [])
        if o.get("$type", "").endswith(type_suffix)
    ]
    print(f"[DEBUG] {section}: {len(items)} items")
    return items


# ──────────────────────── unauthenticated public HTML ────────────────────────

_PUBLIC_HEADERS = {
    # A realistic browser UA — LinkedIn answers 999 to obvious bot user agents.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _get_proxies() -> dict | None:
    """
    Residential proxy from env. LinkedIn blocks datacenter IPs at the network
    level for unauthenticated requests, so a proxy is needed for the public-HTML
    fallback when deployed. Example: HTTPS_PROXY=http://user:pass@host:port
    """
    proxy = (
        os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )
    return {"http": proxy, "https": proxy} if proxy else None


def _try_fetch_html(url: str, label: str) -> tuple[str, int]:
    """
    Fetch a page, trying curl_cffi (Chrome TLS fingerprint) before plain
    requests. Returns (html, status). Status 999 means the IP is blocked.
    Never raises — every failure is caught and reported through the status.
    """
    headers = dict(_PUBLIC_HEADERS)
    html = ""
    last_status = 0
    proxies = _get_proxies()

    try:
        from curl_cffi import requests as _curl
        for impersonate in ("chrome120", "chrome110", "safari15_5"):
            try:
                kwargs = dict(
                    headers=headers, impersonate=impersonate,
                    timeout=15, allow_redirects=True,
                )
                if proxies:
                    kwargs["proxies"] = proxies
                resp = _curl.get(url, **kwargs)
                last_status = resp.status_code
                print(f"[DEBUG] {label} ({impersonate}): {resp.status_code}")
                if resp.status_code == 200:
                    html = resp.text
                    break
                if resp.status_code != 999:
                    break
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                # curl_cffi's TooManyRedirects does not subclass Exception in all
                # versions, so BaseException is required to reliably catch it.
                print(f"[DEBUG] {label} ({impersonate}): {type(exc).__name__}")
                if "redirect" in str(exc).lower():
                    last_status = 302
                    break  # redirect chain to login — private or deleted
                continue
    except ImportError:
        pass
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        print(f"[DEBUG] {label} curl_cffi error: {type(exc).__name__}: {exc}")

    if not html:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            with _requests.Session() as tmp:
                tmp.verify = False
                tmp.max_redirects = 2
                if proxies:
                    tmp.proxies.update(proxies)
                resp = tmp.get(url, headers=headers, timeout=15, allow_redirects=True)
            last_status = resp.status_code
            print(f"[DEBUG] {label} (requests): {resp.status_code}")
            if resp.status_code == 200:
                html = resp.text
        except _requests.exceptions.TooManyRedirects:
            print(f"[DEBUG] {label} → redirect chain (private/deleted profile)")
            last_status = 302
        except Exception as exc:
            print(f"[DEBUG] {label} requests error: {exc}")

    return html, last_status


def _get_public_html(public_id: str) -> tuple[str, bool]:
    """
    Fetch profile HTML without authentication, trying three URL shapes.
    LinkedIn server-renders JSON-LD on these pages for search-engine indexing.

    Returns (html, ip_blocked). ip_blocked is True when LinkedIn answered 999,
    which means the server's IP is blocked rather than the profile missing.
    """
    saw_999 = False
    for url, label in [
        (f"https://www.linkedin.com/in/{public_id}/", "Main public HTML"),
        (f"https://www.linkedin.com/mwlite/in/{public_id}/", "mwlite HTML"),
        (f"https://www.linkedin.com/pub/{public_id}/en/", "pub HTML"),
    ]:
        html, status = _try_fetch_html(url, label)
        if status == 999:
            saw_999 = True
        if html:
            has_headline = bool(re.search(r'"headline"\s*:\s*"[^"]{3,}"', html))
            has_ld = '<script type="application/ld+json">' in html
            if has_headline or has_ld:
                return html, False
            print(f"[DEBUG] {label}: 200 but no profile data (bot-wall)")

    print(f"[DEBUG] Public HTML unavailable (ip_blocked={saw_999})")
    return "", saw_999


# ───────────────────────────── HTML extraction ───────────────────────────────

def _find_field(field: str, html: str) -> str | None:
    """
    Read a string field out of the JSON LinkedIn embeds in its HTML.
    The markup double-escapes it as \\"field\\":\\"value\\", so try that
    form first and fall back to plain JSON.
    """
    m = re.search(rf'\\"{re.escape(field)}\\":\\"([^\\"]+)\\"', html)
    if m:
        return m.group(1)
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"\\]+)"', html)
    return m.group(1) if m else None


def _extract_from_json_ld(html: str) -> dict:
    """
    Parse the schema.org Person block LinkedIn renders for SEO. Yields name,
    headline, about, location, profile picture, and company/school name stubs.
    """
    result = {}
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            obj = json.loads(raw)
        except Exception:
            continue

        items = obj.get("@graph", [obj]) if isinstance(obj, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if "Person" not in t and "ProfilePage" not in t:
                continue
            person = item if "Person" in t else item.get("mainEntity", item)
            if not isinstance(person, dict):
                continue

            # givenName + familyName is cleaner than name, which appends the company
            given = person.get("givenName", "")
            family = person.get("familyName", "")
            if (given or family) and "name" not in result:
                result["name"] = f"{given} {family}".strip()
            elif person.get("name") and "name" not in result:
                n = person["name"]
                if " - " in n:
                    n = n.rsplit(" - ", 1)[0].strip()
                result["name"] = n

            if person.get("jobTitle") and "headline" not in result:
                result["headline"] = person["jobTitle"]
            if person.get("description") and "about" not in result:
                result["about"] = person["description"]

            addr = person.get("address") or {}
            if isinstance(addr, dict) and addr.get("addressLocality") and "location" not in result:
                result["location"] = addr["addressLocality"]

            if person.get("image") and "profilePicture" not in result:
                img = person["image"]
                result["profilePicture"] = (
                    img.get("contentUrl") if isinstance(img, dict) else img
                )

            works = person.get("worksFor", [])
            if isinstance(works, list) and works:
                result.setdefault("experience_raw", works)
            alumni = person.get("alumniOf", [])
            if isinstance(alumni, list) and alumni:
                result.setdefault("education_raw", alumni)

            if result.get("name") or result.get("headline"):
                break

    print(f"[DEBUG] JSON-LD fields: {list(result.keys())}")
    return result


def _best_srcset(srcset_raw: str) -> str | None:
    """Pick the widest URL out of an HTML srcset attribute."""
    srcset = srcset_raw.replace("&amp;", "&")
    best_url, best_w = None, 0
    for part in (p.strip() for p in srcset.split(",")):
        pieces = part.rsplit(" ", 1)
        if len(pieces) == 2:
            try:
                w = int(pieces[1].rstrip("w"))
            except ValueError:
                continue
            if w > best_w:
                best_w, best_url = w, pieces[0]
    return best_url


def _extract_from_html(html: str) -> dict:
    """Scrape profile fields from the page's meta tags and embedded JSON."""
    result: dict = {}
    if not html:
        return result

    m = re.search(r"<title>([^<|]+?)\s*\|\s*LinkedIn</title>", html)
    if m:
        name = m.group(1).strip()
        if " - " in name:
            name = name.rsplit(" - ", 1)[0].strip()
        result["name"] = name

    # og:description holds "Headline · Location · About snippet"
    m = re.search(
        r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']{10,})["\']',
        html,
    ) or re.search(
        r'<meta\s+content=["\']([^"\']{10,})["\']\s+(?:property|name)=["\']og:description["\']',
        html,
    )
    if m:
        og_desc = m.group(1).strip()
        parts = [p.strip() for p in og_desc.split(" · ")]
        if len(parts) >= 2:
            result.setdefault("headline", parts[0])
        if len(parts) >= 3:
            result.setdefault("location", parts[1])
        result.setdefault("about", og_desc)

    # Embedded JSON, both inside <code> blobs and across the whole document
    code_blocks = [
        b.strip() for b in re.findall(r"<code[^>]*>(.*?)</code>", html, re.DOTALL)
        if b.strip().startswith(("{", "["))
    ]
    for source in code_blocks + [html]:
        for key, dest in (
            ("firstName", "_firstName"),
            ("lastName", "_lastName"),
            ("headline", "headline"),
            ("summary", "about"),
            ("locationName", "location"),
            ("geoLocationName", "location"),
        ):
            if dest not in result:
                val = _find_field(key, source)
                if val:
                    result[dest] = val

    first = result.pop("_firstName", "")
    last = result.pop("_lastName", "")
    if first or last:
        result.setdefault("name", f"{first} {last}".strip())

    for m in re.finditer(r'imageSrcSet="([^"]+)"', html):
        srcset_raw = m.group(1)
        first_url = srcset_raw.split(",")[0].split(" ")[0]
        if "displayphoto" in first_url and "profilePicture" not in result:
            if url := _best_srcset(srcset_raw):
                result["profilePicture"] = url
        elif "displaybackgroundimage" in first_url and "backgroundImage" not in result:
            if url := _best_srcset(srcset_raw):
                result["backgroundImage"] = url
        if "profilePicture" in result and "backgroundImage" in result:
            break

    return result


# ──────────────────────────────── entry point ────────────────────────────────

def fetch_all(url_or_id: str) -> dict:
    """
    Collect everything known about a profile.

    Raises ValueError for a malformed URL, LookupError when the profile is
    missing or private, and RuntimeError when LinkedIn blocked the server's IP
    and no authenticated session was available to fall back on.
    """
    public_id = extract_public_id(url_or_id)

    # 1. Authenticated Dash API — the primary source.
    profile, urn = {}, ""
    positions = educations = skills = certifications = languages = []

    authenticated = session.is_authenticated()
    if authenticated:
        print("[DEBUG] Authenticated — using Voyager Dash API")
        # No re-login attempt here: an empty result means the profile does not
        # exist or is not visible, not that the session broke. _get() already
        # re-authenticates on a genuine 401.
        profile, urn = _dash_profile(public_id)

        if urn:
            positions      = _dash_section("profilePositions",      urn, public_id, ".Position")
            educations     = _dash_section("profileEducations",     urn, public_id, ".Education")
            skills         = _dash_section("profileSkills",         urn, public_id, ".Skill")
            certifications = _dash_section("profileCertifications", urn, public_id, ".Certification")
            languages      = _dash_section("profileLanguages",      urn, public_id, ".Language")
    else:
        print("[DEBUG] No session — falling back to public HTML")

    # 2. Public HTML — the unauthenticated fallback, and the only source for the
    #    city-level location string that the Dash Profile entity omits.
    html_data: dict = {}
    ip_blocked = False
    if not profile or not profile.get("headline"):
        try:
            public_html, ip_blocked = _get_public_html(public_id)
        except Exception as exc:
            print(f"[DEBUG] public HTML error: {exc}")
            public_html = ""

        html_data.update(_extract_from_html(public_html))
        for k, v in _extract_from_json_ld(public_html).items():
            if v and k not in html_data:
                html_data[k] = v

    # 3. Fail only when neither source produced anything.
    if not profile and not any(
        html_data.get(k) for k in ("name", "headline", "profilePicture")
    ):
        # A cookie was supplied and LinkedIn rejected it. Say that, rather than
        # reporting the IP block we only hit because we fell back to public HTML —
        # otherwise the fix looks like "add a cookie" when one is already set.
        if session.was_rejected():
            raise SessionInvalid(_DEAD_SESSION_MSG)

        # An authenticated Dash lookup that comes back empty is a definitive
        # "no such profile" — don't let the blocked HTML fallback mask that as
        # an IP-block error, or every mistyped URL would report 429.
        if ip_blocked and not authenticated:
            raise RuntimeError(
                "LinkedIn returned HTTP 999 — this server's IP is blocked by LinkedIn. "
                "Provide LINKEDIN_LI_AT (generate it with tools/get_li_at.py) so the "
                "API can use the authenticated endpoints, or set HTTPS_PROXY to a "
                "residential proxy URL."
            )
        raise LookupError(f"Profile '{public_id}' not found or is private.")

    return {
        "publicIdentifier": public_id,
        "htmlData": html_data,
        "dashProfile": profile,
        "positions": positions,
        "educations": educations,
        "skills": skills,
        "certifications": certifications,
        "languages": languages,
    }
