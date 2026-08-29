import os
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
        print(f"[DEBUG] 429 body: {resp.text[:300]}")
        raise RuntimeError(f"LinkedIn rate limit or session error: {resp.text[:200]}")

    resp.raise_for_status()
    return resp.json()


_PUBLIC_HEADERS = {
    # Use a realistic browser UA — LinkedIn returns 999 for obvious bot UAs
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


def _try_fetch_html(url: str, label: str, extra_headers: dict = None) -> str:
    """Shared fetch logic: tries curl_cffi (chrome120) first, then requests fallback."""
    headers = dict(_PUBLIC_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    html = ""

    try:
        from curl_cffi import requests as _curl
        for impersonate in ("chrome120", "chrome110", "safari15_5"):
            try:
                resp = _curl.get(url, headers=headers, impersonate=impersonate, timeout=15)
                print(f"[DEBUG] {label} ({impersonate}): status={resp.status_code}")
                if resp.status_code == 200:
                    html = resp.text
                    break
                if resp.status_code != 999:
                    break
            except Exception:
                continue
    except ImportError:
        pass

    if not html:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            resp = _requests.get(url, headers=headers, verify=False, timeout=15, allow_redirects=True)
            print(f"[DEBUG] {label} (requests): status={resp.status_code}")
            if resp.status_code == 200:
                html = resp.text
        except Exception as exc:
            print(f"[DEBUG] {label} error: {exc}")

    return html


def _get_public_html(public_id: str) -> str:
    """
    Fetch LinkedIn profile HTML without authentication. Tries three URLs:
    1. Main site  2. Mobile-lite site (different bot rules)
    LinkedIn renders full JSON-LD for unauthenticated requests for SEO purposes.
    """
    for url, label in [
        (f"https://www.linkedin.com/in/{public_id}/", "Main public HTML"),
        (f"https://www.linkedin.com/mwlite/in/{public_id}/", "mwlite HTML"),
        (f"https://www.linkedin.com/pub/{public_id}/en/", "pub HTML"),
    ]:
        html = _try_fetch_html(url, label)
        if html:
            has_headline = bool(re.search(r'"headline"\s*:\s*"[^"]{3,}"', html))
            has_ld = "<script type=\"application/ld+json\">" in html
            print(f"[DEBUG] {label} — headline JSON: {has_headline}, JSON-LD: {has_ld}")
            if has_headline or has_ld:
                return html
            print(f"[DEBUG] {label} returned but no usable profile data (bot-wall?)")

    print("[DEBUG] All public HTML attempts blocked (999 / no data)")
    return ""


def _get_profile_html(public_id: str) -> str:
    """Authenticated HTML fetch — used only for JSESSIONID warm-up side-effect."""
    sess = session.get_session()
    try:
        probe = sess.get(
            f"https://www.linkedin.com/in/{public_id}/",
            headers={"Referer": "https://www.linkedin.com/"},
            allow_redirects=False,
            timeout=10,
        )
        set_cookie = probe.headers.get('Set-Cookie', '')
        print(f"[DEBUG] Auth probe: status={probe.status_code}")
        if 'li_at=delete me' in set_cookie or ('li_at' in set_cookie and 'Max-Age=0' in set_cookie):
            raise PermissionError(
                "LinkedIn rejected the li_at cookie (sent 'delete me' signal). "
                "Your LINKEDIN_LI_AT is expired — re-extract it from "
                "DevTools → Application → Cookies → li_at."
            )
        jsid = sess.cookies.get("JSESSIONID", "")
        print(f"[DEBUG] JSESSIONID after auth visit: {jsid!r}")
        if probe.status_code == 200:
            return probe.text
    except PermissionError:
        raise
    except Exception as exc:
        print(f"[DEBUG] Auth probe error: {exc}")

    try:
        resp = sess.get(
            f"https://www.linkedin.com/in/{public_id}/",
            headers={"Referer": "https://www.linkedin.com/"},
            timeout=15,
        )
        jsid = sess.cookies.get("JSESSIONID", "")
        print(f"[DEBUG] Auth HTML fallback: {resp.status_code}, JSESSIONID={jsid!r}")
        return resp.text
    except _requests.exceptions.TooManyRedirects:
        print("[DEBUG] Auth HTML redirect loop")
        return ""
    except Exception as exc:
        print(f"[DEBUG] Auth HTML error: {exc}")
        return ""


def _esc(field: str, html: str) -> str | None:
    """
    Extract a simple string field from LinkedIn's double-escaped JSON in the HTML.
    The HTML embeds JSON as: \\"field\\":\\"value\\"
    In Python's string that's: \"field\":\"value\"
    """
    m = re.search(rf'\\"{ re.escape(field) }\\":\\"([^\\"]+)\\"', html)
    return m.group(1) if m else None


def _find_field(field: str, html: str) -> str | None:
    """Try double-escaped JSON first, then plain JSON."""
    val = _esc(field, html)
    if val:
        return val
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"\\]+)"', html)
    return m.group(1) if m else None


import json as _json

def _extract_from_json_ld(html: str) -> dict:
    """
    LinkedIn embeds structured profile data as JSON-LD in public (unauthenticated) pages.
    Schema: Person with name, jobTitle (headline), description (about), address, etc.
    """
    result = {}
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            obj = _json.loads(raw)
        except Exception:
            continue
        # Handle @graph array or direct object
        items = obj.get("@graph", [obj]) if isinstance(obj, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if "Person" not in t and "ProfilePage" not in t:
                continue
            # Direct Person fields
            person = item if "Person" in t else item.get("mainEntity", item)
            if not isinstance(person, dict):
                continue
            # givenName + familyName is cleaner than name (which includes company suffix)
            given = person.get("givenName", "")
            family = person.get("familyName", "")
            if (given or family) and "name" not in result:
                result["name"] = f"{given} {family}".strip()
            elif person.get("name") and "name" not in result:
                n = person["name"]
                if ' - ' in n:
                    n = n.rsplit(' - ', 1)[0].strip()
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
                result["profilePicture"] = img.get("contentUrl") or (img if isinstance(img, str) else None)
            # worksFor → experience stub
            works = person.get("worksFor", [])
            if isinstance(works, list) and works and "experience" not in result:
                result["experience_raw"] = works
            # alumniOf → education stub
            alumni = person.get("alumniOf", [])
            if isinstance(alumni, list) and alumni and "education" not in result:
                result["education_raw"] = alumni
            if result.get("name") or result.get("headline"):
                break
    print(f"[DEBUG] JSON-LD extracted: {list(result.keys())}")
    return result


def _extract_from_html(html: str) -> dict:
    result = {}
    if not html:
        return result

    m = re.search(r"<title>([^<|]+?)\s*\|\s*LinkedIn</title>", html)
    if m:
        name = m.group(1).strip()
        if ' - ' in name:
            name = name.rsplit(' - ', 1)[0].strip()
        result["name"] = name

    # og:description often contains "Headline · location · about snippet"
    m = re.search(r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']{10,})["\']', html)
    if not m:
        m = re.search(r'<meta\s+content=["\']([^"\']{10,})["\']\s+(?:property|name)=["\']og:description["\']', html)
    if m:
        og_desc = m.group(1).strip()
        print(f"[DEBUG] og:description: {og_desc[:120]!r}")
        # Format: "Headline · location · about..." OR just "about..."
        parts = [p.strip() for p in og_desc.split(' · ')]
        if len(parts) >= 2 and "headline" not in result:
            result["headline"] = parts[0]
        if len(parts) >= 3 and "location" not in result:
            result["location"] = parts[1]
        if len(parts) >= 1 and "about" not in result:
            result["about"] = og_desc

    # LinkedIn sometimes embeds JSON blobs inside <code id="bpr-guid-..."> tags —
    # these contain the full profile state including headline/summary/location.
    code_blocks = re.findall(r'<code[^>]*>(.*?)</code>', html, re.DOTALL)
    print(f"[DEBUG] <code> blocks found: {len(code_blocks)}")
    # Search each code block for profile fields
    json_code_blocks = [b.strip() for b in code_blocks if b.strip().startswith(('{', '['))]
    print(f"[DEBUG] JSON code blocks: {len(json_code_blocks)}")
    if json_code_blocks:
        print(f"[DEBUG] First JSON code block (200 chars): {json_code_blocks[0][:200]!r}")
    for block in json_code_blocks:
        for key, dest in (
            ("headline",  "headline"),
            ("summary",   "about"),
            ("locationName", "location"),
            ("geoLocationName", "location"),
        ):
            if dest not in result:
                val = _find_field(key, block)
                if val:
                    result[dest] = val

    # Extract fields from LinkedIn's embedded double-escaped JSON (full HTML scan)
    for key, dest in (
        ("firstName", "_firstName"),
        ("lastName",  "_lastName"),
        ("headline",  "headline"),
        ("summary",   "about"),
        ("locationName", "location"),
        ("geoLocationName", "location"),
    ):
        if dest in result:
            continue
        val = _find_field(key, html)
        if val and dest not in result:
            result[dest] = val

    # Diagnostic: show occurrence counts for missing fields
    for key, dest in [("headline", "headline"), ("summary", "about"), ("locationName", "location")]:
        if dest not in result:
            esc_c = html.count(f'\\"{ key }\\"')
            plain_c = html.count(f'"{ key }"')
            print(f"[DEBUG] '{key}' not found — escaped occurrences: {esc_c}, plain: {plain_c}")

    first = result.pop("_firstName", "")
    last  = result.pop("_lastName", "")
    if first or last:
        result.setdefault("name", f"{first} {last}".strip())

    def _best_srcset(srcset_raw: str) -> str | None:
        srcset = srcset_raw.replace("&amp;", "&")
        best_url, best_w = None, 0
        for part in [p.strip() for p in srcset.split(",")]:
            pieces = part.rsplit(" ", 1)
            if len(pieces) == 2:
                try:
                    w = int(pieces[1].rstrip("w"))
                    if w > best_w:
                        best_w, best_url = w, pieces[0]
                except ValueError:
                    pass
        return best_url

    for m in re.finditer(r'imageSrcSet="([^"]+)"', html):
        srcset_raw = m.group(1)
        first_url = srcset_raw.split(",")[0].split(" ")[0]
        if "displayphoto" in first_url and "profilePicture" not in result:
            url = _best_srcset(srcset_raw)
            if url:
                result["profilePicture"] = url
        elif "displaybackgroundimage" in first_url and "backgroundImage" not in result:
            url = _best_srcset(srcset_raw)
            if url:
                result["backgroundImage"] = url
        if "profilePicture" in result and "backgroundImage" in result:
            break

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


def _get_from_typeahead(public_id: str) -> dict:
    """
    Typeahead search by vanity name.
    Returns name + headline from hits (subtext field) and MiniProfile in included.
    """
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
        return {}
    data: dict = {}

    # Non-normalized: elements → hits (each hit has text, subtext, targetUrn)
    for el in result.get("elements", []):
        for hit in el.get("hits", [el]):
            urn = hit.get("targetUrn", "") or hit.get("objectUrn", "")
            if not any(x in urn for x in ("fsd_profile", "member")):
                continue
            text = hit.get("text", "")
            subtext = hit.get("subtext", "")
            if isinstance(text, dict):
                text = text.get("text", "")
            if isinstance(subtext, dict):
                subtext = subtext.get("text", "")
            if text and "name" not in data:
                data["name"] = text
            if subtext and "headline" not in data:
                data["headline"] = subtext
            break

    # Normalized: included may contain MiniProfile with occupation/picture
    for obj in result.get("included", []):
        if "MiniProfile" not in obj.get("$type", ""):
            continue
        is_exact = obj.get("publicIdentifier", "").lower() == public_id.lower()
        if not data or is_exact:
            first = obj.get("firstName", "")
            last  = obj.get("lastName", "")
            name  = f"{first} {last}".strip()
            if name:
                data["name"] = name
            if obj.get("occupation"):
                data["headline"] = obj["occupation"]
            pic = obj.get("picture")
            if pic:
                from app.parser import _best_image_url
                url = _best_image_url(pic)
                if url:
                    data["profilePicture"] = url
        if is_exact:
            break

    print(f"[DEBUG] Typeahead extracted: {list(data.keys())}")
    return data


def _get_dash_profile_by_urn_path(urn: str, public_id: str) -> dict | None:
    """Try Dash profiles endpoint with URN directly in the URL path."""
    import urllib.parse
    encoded = urllib.parse.quote(urn, safe="")
    data = _get(f"/identity/dash/profiles/{encoded}", public_id)
    if data:
        print(f"[DEBUG] Dash URN-path: keys={list(data.keys())[:10]}")
    return data


_DECORATION_PREFIX = "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-"
_DECORATION_IDS = [
    _DECORATION_PREFIX + str(v)
    # Try 200 → 111 first (newest), then 110 → 1 (older fallback)
    for v in list(range(200, 110, -1)) + list(range(110, 0, -1))
]

# Module-level cache: populated at startup from LinkedIn's JS bundles or .env.
_discovered_decoration: str | None = None
_discovered_graphql_query_id: str | None = os.getenv("LINKEDIN_GRAPHQL_QUERY_ID") or None
# Components query returns actual profile sections (headline, experience, education, skills)
_graphql_components_query_id: str | None = os.getenv("LINKEDIN_GRAPHQL_COMPONENTS_QUERY_ID") or None


def discover_decoration_from_bundles(html: str) -> str | None:
    """
    Fetch LinkedIn's static JS bundles and grep for the current
    FullProfileWithEntities version number AND GraphQL query IDs.
    Runs once at startup; results cached in module globals.
    """
    global _discovered_decoration, _discovered_graphql_query_id
    if _discovered_decoration and _discovered_graphql_query_id:
        return _discovered_decoration

    script_urls = re.findall(r'src="(https://static\.licdn\.com/[^"]+\.js[^"]*)"', html)
    print(f"[DEBUG] Bundle URLs found: {len(script_urls)}, first: {script_urls[0][:80] if script_urls else 'none'}")

    sess = session.get_session()
    for url in script_urls:
        try:
            resp = sess.get(url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue
            text = resp.text

            if not _discovered_decoration:
                m = re.search(r'FullProfileWithEntities-(\d+)', text)
                if m:
                    deco = _DECORATION_PREFIX + m.group(1)
                    print(f"[DEBUG] Discovered decoration: {deco}")
                    _discovered_decoration = deco

            if not _discovered_graphql_query_id:
                # LinkedIn's GraphQL queryIds look like voyagerIdentityDashProfiles.HASH
                m = re.search(r'voyagerIdentityDashProfiles\.([a-f0-9]{8,64})', text)
                if m:
                    qid = f"voyagerIdentityDashProfiles.{m.group(1)}"
                    print(f"[DEBUG] Discovered GraphQL queryId: {qid}")
                    _discovered_graphql_query_id = qid

            if _discovered_decoration and _discovered_graphql_query_id:
                break
        except Exception as exc:
            print(f"[DEBUG] Bundle fetch error: {exc}")

    if not _discovered_decoration:
        print("[DEBUG] Could not find decoration ID in JS bundles")
    if not _discovered_graphql_query_id:
        print("[DEBUG] Could not find GraphQL queryId in JS bundles")
    return _discovered_decoration


def _get_classic_profile(public_id: str) -> dict | None:
    """Classic Voyager REST — returns headline, locationName, summary, etc."""
    data = _get(f"/identity/profiles/{public_id}", public_id)
    if not data:
        return None
    # Normalized response wraps in data+included; unwrap if needed
    if "included" in data:
        # Extract the Profile entity from included
        profile = next(
            (o for o in data.get("included", []) if "Profile" in o.get("$type", "")),
            None,
        )
        print(f"[DEBUG] Classic profile (normalized): keys={list(profile.keys())[:12] if profile else 'none'}")
        return profile
    print(f"[DEBUG] Classic profile: keys={list(data.keys())[:12]}")
    return data


def _get_profile_view(public_id: str) -> dict | None:
    """Classic Voyager profileView — returns positionView, educationView, skillView, etc."""
    data = _get(f"/identity/profiles/{public_id}/profileView", public_id)
    if not data:
        return None
    print(f"[DEBUG] profileView keys: {list(data.keys())[:12]}")
    return data


def _get_dash_profile_by_urn(urn: str, public_id: str) -> dict | None:
    # Try q=memberIdentityUrn with the fsd_profile URN
    result = _dash_get(
        {"q": "memberIdentityUrn", "memberIdentityUrn": urn},
        public_id,
        "URN q=memberIdentityUrn no-deco",
    )
    return result


def _session_is_redirecting() -> bool:
    return False


def _dash_get(params: dict, public_id: str, label: str) -> dict | None:
    """One attempt against the Dash profiles endpoint. Returns data or None."""
    url = f"{BASE_URL}/identity/dash/profiles"
    sess = session.get_session()
    headers = session.get_voyager_headers()
    headers["Referer"] = f"https://www.linkedin.com/in/{public_id}/"
    try:
        resp = sess.get(url, headers=headers, params=params, timeout=15)
    except _requests.exceptions.TooManyRedirects:
        print(f"[DEBUG] Dash {label} → redirect loop, stopping")
        return None
    print(f"[DEBUG] Dash {label}: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        if data:
            print(f"[DEBUG] Dash succeeded ({label}), keys: {list(data.keys())[:8]}")
            return data
        print(f"[DEBUG] Dash 200 but empty body")
    if resp.status_code in (400, 403, 500):
        print(f"[DEBUG]   body: {resp.text[:300]}")
    return None


def _get_dash_profile_by_vanity(public_id: str) -> dict | None:
    # Try four q-parameter styles — LinkedIn has changed these over deployments
    q_styles = [
        {"q": "vanityName",       "vanityName":       public_id},
        {"q": "publicIdentifier", "publicIdentifier": public_id},
        {"q": "memberIdentityUrn", "memberIdentityUrn": f"urn:li:member:{public_id}"},
    ]
    decorations = [None] + _DECORATION_IDS[:4]  # no-deco first, then versioned

    for q_params in q_styles:
        for decoration in decorations:
            params = dict(q_params)
            if decoration:
                params["decorationId"] = decoration
            label = f"q={q_params['q']} deco={'none' if not decoration else decoration[-6:]}"
            result = _dash_get(params, public_id, label)
            if result is not None:
                return result
            # If first attempt with this q-style already 400, skip remaining decorations
            # (decoration change won't fix a bad q parameter)
            break

    return None


def _graphql_get(qid: str, variables: str, public_id: str, sess, headers: dict) -> dict | None:
    """Single GraphQL attempt with literal URL (parens/colons not URL-encoded)."""
    base = "https://www.linkedin.com/voyager/api/graphql"
    url = f"{base}?includeWebMetadata=true&variables={variables}&queryId={qid}"
    try:
        resp = sess.get(url, headers=headers, timeout=15)
        print(f"[DEBUG] GraphQL qid=...{qid[-12:]} vars={variables!r} → {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            included = data.get("included", [])
            print(f"[DEBUG]   included count={len(included)}")
            # Print all included types so we know what came back
            types = [obj.get("$type", "?").split(".")[-1] for obj in included]
            print(f"[DEBUG]   included types: {types[:10]}")
            # Print data sub-key structure
            inner = data.get("data") or {}
            print(f"[DEBUG]   data keys: {list(inner.keys())[:8]}")
            if included:
                profile_obj = next(
                    (o for o in included if o.get("$type", "").endswith(".Profile")), None
                )
                if profile_obj:
                    print(f"[DEBUG]   Profile keys: {list(profile_obj.keys())[:20]}")
                    print(f"[DEBUG]   headline={profile_obj.get('headline')!r}  firstName={profile_obj.get('firstName')!r}")
                    print(f"[DEBUG]   $recipeTypes={profile_obj.get('$recipeTypes', [])}")
            return data
        print(f"[DEBUG]   body: {resp.text[:200]}")
    except _requests.exceptions.TooManyRedirects:
        print("[DEBUG] GraphQL → redirect loop")
        raise
    except Exception as exc:
        print(f"[DEBUG] GraphQL error: {exc}")
    return None


def _get_profile_via_graphql(public_id: str, urn: str = "") -> dict | None:
    """Try LinkedIn's GraphQL endpoints for profile data."""
    sess = session.get_session()
    headers = session.get_voyager_headers()
    headers["Referer"] = f"https://www.linkedin.com/in/{public_id}/"

    # Extract the raw base64 member ID from the fsd_profile URN.
    # LinkedIn's browser sends (memberIdentity:BASE64_ID) — NOT the full URN.
    member_id = urn.split("urn:li:fsd_profile:")[-1] if urn else ""

    result = None

    # --- Try the profiles version-check query (returns versionTag; limited data) ---
    if _discovered_graphql_query_id:
        print(f"[DEBUG] GraphQL profiles queryId: {_discovered_graphql_query_id!r}")
        variables = f"(memberIdentity:{member_id})" if member_id else f"(publicIdentifier:{public_id})"
        try:
            result = _graphql_get(_discovered_graphql_query_id, variables, public_id, sess, headers)
        except _requests.exceptions.TooManyRedirects:
            return None

    # --- Try the components query (returns headline, experience, education, etc.) ---
    if _graphql_components_query_id:
        print(f"[DEBUG] GraphQL components queryId: {_graphql_components_query_id!r}")
        # Components query uses full URN or publicIdentifier
        variables_to_try = []
        if urn:
            variables_to_try += [
                f"(memberIdentityUrn:{urn},count:100,start:0)",
                f"(memberIdentityUrn:{urn})",
            ]
        variables_to_try.append(f"(publicIdentifier:{public_id})")
        for variables in variables_to_try:
            try:
                comp_result = _graphql_get(_graphql_components_query_id, variables, public_id, sess, headers)
                if comp_result:
                    # Merge components data into the result
                    if result:
                        # Add all included from components into the main result
                        result.setdefault("included", [])
                        for obj in comp_result.get("included", []):
                            if obj not in result["included"]:
                                result["included"].append(obj)
                    else:
                        result = comp_result
                    break
            except _requests.exceptions.TooManyRedirects:
                return result

    return result


def _extract_from_me(me_data: dict, public_id: str) -> dict:
    """
    Extract profile fields from /me MiniProfile.
    Only applies when the queried profile matches the logged-in user's publicIdentifier
    (i.e. the li_at cookie belongs to the same person being queried).
    """
    for obj in me_data.get("included", []):
        if "MiniProfile" not in obj.get("$type", ""):
            continue
        if obj.get("publicIdentifier", "").lower() != public_id.lower():
            return {}
        from app.parser import _best_image_url
        first = obj.get("firstName", "")
        last = obj.get("lastName", "")
        data = {}
        if first or last:
            data["name"] = f"{first} {last}".strip()
        if obj.get("occupation"):
            data["headline"] = obj["occupation"]
        pic = _best_image_url(obj.get("picture"))
        if pic:
            data["profilePicture"] = pic
        bg = _best_image_url(obj.get("backgroundImage"))
        if bg:
            data["backgroundImage"] = bg
        for urn_key in ("dashEntityUrn", "entityUrn", "objectUrn"):
            urn = obj.get(urn_key, "")
            if "fsd_profile" in urn or "member" in urn:
                data["urn"] = urn
                break
        print(f"[DEBUG] /me match — extracted: {list(data.keys())}")
        return data
    return {}


def _check_api_connectivity() -> dict | None:
    """Verify API access via /voyager/api/me. Returns the /me data dict on success, None on failure."""
    sess = session.get_session()
    headers = session.get_voyager_headers()
    try:
        resp = sess.get(f"{BASE_URL}/me", headers=headers, timeout=10)
        print(f"[DEBUG] /voyager/api/me → {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"[DEBUG] /me top-level keys: {list(data.keys())}")
            # Print 'data' sub-structure so we can see what LinkedIn returns
            inner = data.get("data") or {}
            print(f"[DEBUG] /me data keys: {list(inner.keys())[:10]}")
            included = data.get("included", [])
            print(f"[DEBUG] /me included count: {len(included)}")
            if included:
                print(f"[DEBUG] /me included[0] type: {included[0].get('$type','?')}")
                print(f"[DEBUG] /me included[0] keys: {list(included[0].keys())[:15]}")
            return data
        print(f"[DEBUG] /me body: {resp.text[:200]}")
    except _requests.exceptions.TooManyRedirects:
        print("[DEBUG] /me → redirect loop")
    except Exception as exc:
        print(f"[DEBUG] /me error: {exc}")
    return None


def fetch_all(url_or_id: str) -> dict:
    public_id = extract_public_id(url_or_id)

    # ── Step 1: Public HTML (no cookies, stable, good for deployment) ──────────
    # LinkedIn serves a fully server-rendered page to unauthenticated requests
    # containing JSON-LD with name, headline, about, location, experience, education.
    # This does NOT require queryIds that change with every LinkedIn deployment.
    public_html = _get_public_html(public_id)
    json_ld_data = _extract_from_json_ld(public_html)
    public_html_data = _extract_from_html(public_html)

    # Merge JSON-LD and HTML extractions (JSON-LD wins for structured fields)
    html_data: dict = {}
    html_data.update(public_html_data)
    for k, v in json_ld_data.items():
        if v and k not in html_data:
            html_data[k] = v
    print(f"[DEBUG] html_data after public fetch: {list(html_data.keys())}")

    # ── Step 2: Skip authenticated HTML probe ────────────────────────────────
    # Visiting linkedin.com/in/{id}/ with li_at from a cloud IP triggers LinkedIn's
    # security system and invalidates the cookie after ~2 uses. JSESSIONID obtained
    # at startup (feed warm-up) is sufficient for all Voyager API calls below.

    # ── Step 3: Authenticated Voyager API calls ──────────────────────────────
    me_data = _check_api_connectivity()
    api_ok = me_data is not None
    print(f"[DEBUG] API connectivity: {'OK' if api_ok else 'FAILED'}")

    # If the li_at cookie belongs to the queried profile, /me has their full data
    if me_data:
        me_fields = _extract_from_me(me_data, public_id)
        for k, v in me_fields.items():
            if v and k not in html_data:
                html_data[k] = v

    classic_profile = None
    profile_view = None
    dash_profile = None

    if api_ok:
        urn = html_data.get("urn", "")

        # ── GraphQL: URN confirmation + versionTag (2 calls total per request) ──
        # Typeahead/search (404), classic REST (410), Dash (400) all consistently
        # fail and make the session look like bot-probing — removed to keep the
        # cookie alive across multiple requests.
        if _discovered_graphql_query_id:
            dash_profile = _get_profile_via_graphql(public_id, urn=urn)
    else:
        print("[DEBUG] Skipping authenticated API calls")

    print(f"[DEBUG] html_data keys: {list(html_data.keys())}")
    if dash_profile:
        print(f"[DEBUG] dash_profile keys: {list(dash_profile.keys())}")

    # Succeed if we have at least a name, headline, or profile picture
    has_data = any(html_data.get(k) for k in ("name", "headline", "profilePicture"))
    if not has_data:
        raise LookupError(
            f"Profile '{public_id}' not found or is private. "
            "Make sure the profile is public."
        )

    return {
        "publicIdentifier": public_id,
        "htmlData": html_data,
        "classicProfile": {},
        "profileView": {},
        "dashProfile": dash_profile or {},
        "skills": {},
        "languages": {},
    }
