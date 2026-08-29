import re
import requests as _requests
from app.auth import session

BASE_URL = "https://www.linkedin.com/voyager/api"


def extract_public_id(url_or_id: str) -> str:
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


def _get(path: str, public_id: str = "", params: dict = None) -> dict | None:
    url = f"{BASE_URL}{path}"
    referer = (
        f"https://www.linkedin.com/in/{public_id}/"
        if public_id
        else "https://www.linkedin.com/"
    )

    sess = session.get_session()
    headers = session.get_voyager_headers()
    headers["Referer"] = referer

    try:
        resp = sess.get(url, headers=headers, params=params, timeout=15)
    except _requests.exceptions.TooManyRedirects:
        print(f"[DEBUG] GET {url} → redirect loop (corporate proxy?)")
        return None

    print(f"[DEBUG] GET {url} → {resp.status_code}")

    if resp.status_code in (404, 410):
        return None

    if resp.status_code == 401:
        session.login()
        sess = session.get_session()
        resp = sess.get(url, headers=headers, params=params, timeout=15)

    if resp.status_code in (400, 403):
        print(f"[DEBUG] {resp.status_code} body: {resp.text[:800]}")
        return None

    if resp.status_code == 429:
        raise RuntimeError("LinkedIn rate limit hit. Try again later.")

    resp.raise_for_status()
    return resp.json()


def _get_profile_html(public_id: str) -> str:
    sess = session.get_session()
    try:
        resp = sess.get(
            f"https://www.linkedin.com/in/{public_id}/",
            headers={"Referer": "https://www.linkedin.com/"},
            timeout=15,
        )
        print(f"[DEBUG] Profile HTML status: {resp.status_code}")
        # Capture JSESSIONID if LinkedIn set it in this response
        jsid = sess.cookies.get("JSESSIONID", "")
        print(f"[DEBUG] JSESSIONID after HTML visit: {jsid!r}")
        return resp.text
    except _requests.exceptions.TooManyRedirects:
        print("[DEBUG] Profile HTML redirect loop — skipping HTML, using API only")
        return ""
    except Exception as exc:
        print(f"[DEBUG] Profile HTML error: {exc}")
        return ""


def _extract_from_html(html: str) -> dict:
    result = {}
    if not html:
        return result

    m = re.search(r"<title>([^<|]+?)\s*\|\s*LinkedIn</title>", html)
    if m:
        result["name"] = m.group(1).strip()

    m = re.search(r'imageSrcSet="([^"]+)"', html)
    if m:
        srcset = m.group(1).replace("&amp;", "&")
        parts = [p.strip() for p in srcset.split(",")]
        best_url, best_w = None, 0
        for part in parts:
            pieces = part.rsplit(" ", 1)
            if len(pieces) == 2:
                try:
                    w = int(pieces[1].rstrip("w"))
                    if w > best_w:
                        best_w, best_url = w, pieces[0]
                except ValueError:
                    pass
        if best_url:
            result["profilePicture"] = best_url

    # Require at least 8 chars after fsd_profile: to avoid matching the literal
    # string "urn:li:fsd_profile:urn" that sometimes appears in HTML templates.
    urn_matches = re.findall(r'urn:li:fsd_profile:[A-Za-z0-9\-_]{8,}', html)
    if urn_matches:
        result["urn"] = urn_matches[0]
        print(f"[DEBUG] Found URN in HTML: {urn_matches[0]}")

    return result


def _resolve_urn(public_id: str) -> str | None:
    result = _get(
        "/typeahead/hitsV2",
        public_id,
        params={
            "keywords": public_id,
            "origin": "GLOBAL_SEARCH_HEADER",
            "q": "TYPE_AHEAD_QUERY",
            "type": "PEOPLE",
        },
    )
    if not result:
        return None
    for el in result.get("elements", []):
        hits = el.get("hits", [el])
        for hit in hits:
            urn = hit.get("targetUrn", "") or hit.get("objectUrn", "")
            if "fsd_profile" in urn or "member" in urn:
                print(f"[DEBUG] Resolved URN: {urn}")
                return urn
    return None


_DECORATION_IDS = [
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93",
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91",
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-86",
]


def _get_dash_profile_by_urn(urn: str, public_id: str) -> dict | None:
    for decoration in _DECORATION_IDS:
        result = _get(
            "/identity/dash/profiles",
            public_id,
            params={
                "q": "memberIdentityUrn",
                "memberIdentityUrn": urn,
                "decorationId": decoration,
            },
        )
        if result:
            return result
    return None


def _get_dash_profile_by_vanity(public_id: str) -> dict | None:
    # Try both q=vanityName and q=publicIdentifier — LinkedIn has used both
    for q_param in ("vanityName", "publicIdentifier"):
        for decoration in _DECORATION_IDS:
            result = _get(
                "/identity/dash/profiles",
                public_id,
                params={
                    "q": q_param,
                    q_param: public_id,
                    "decorationId": decoration,
                },
            )
            if result:
                return result
    return None


def fetch_all(url_or_id: str) -> dict:
    public_id = extract_public_id(url_or_id)

    # HTML visit: gets JSESSIONID as a side effect; HTML content itself is
    # a bare React shell for authenticated users so we only extract fallbacks
    html = _get_profile_html(public_id)
    html_data = _extract_from_html(html)

    # Try Dash API with vanity name first (no URN resolution needed)
    dash_profile = _get_dash_profile_by_vanity(public_id)

    # Fall back to URN-based lookup if vanity failed
    if not dash_profile:
        urn = html_data.get("urn") or _resolve_urn(public_id)
        if urn:
            dash_profile = _get_dash_profile_by_urn(urn, public_id)

    if not dash_profile:
        raise LookupError(
            f"Profile '{public_id}' not found or is private. "
            "Make sure the profile is public and your li_at cookie is valid."
        )

    print(f"[DEBUG] dash_profile keys: {list(dash_profile.keys())}")

    return {
        "publicIdentifier": public_id,
        "htmlData": html_data,
        "dashProfile": dash_profile,
        "skills": {},
        "languages": {},
    }
