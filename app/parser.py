"""
Transforms raw LinkedIn Voyager Dash API responses into our clean response schema.

LinkedIn retired the classic /identity/profiles/{id} and /profileView endpoints
(they now return 410 Gone). Everything here maps the current Dash entities:
  com.linkedin.voyager.dash.identity.profile.{Profile,Position,Education,
                                              Skill,Certification,Language}

All parsing uses .get() throughout — missing fields silently become None/[].
"""

PROFICIENCY_MAP = {
    "ELEMENTARY": "Elementary",
    "LIMITED_WORKING": "Limited Working",
    "PROFESSIONAL_WORKING": "Professional Working",
    "FULL_PROFESSIONAL": "Full Professional",
    "NATIVE_OR_BILINGUAL": "Native or Bilingual",
}

# LinkedIn returns only an ISO country code on the Profile entity; the full
# "City, State, Country" string lives in the public HTML. This covers the
# common cases so an authenticated-only fetch still reports something useful.
_COUNTRY_NAMES = {
    "US": "United States", "IN": "India", "GB": "United Kingdom",
    "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France",
    "SG": "Singapore", "AE": "United Arab Emirates", "NL": "Netherlands",
    "IE": "Ireland", "JP": "Japan", "CN": "China", "BR": "Brazil",
}


# ---------- helpers ----------

def _localized(entity: dict, key: str) -> str | None:
    """
    Dash entities carry both a flat field and a multiLocale variant, e.g.
    `companyName` and `multiLocaleCompanyName: {"en_US": "..."}`.
    Prefer the flat value, fall back to any locale present.
    """
    val = entity.get(key)
    if val:
        return val
    multi = entity.get("multiLocale" + key[0].upper() + key[1:])
    if isinstance(multi, dict) and multi:
        return multi.get("en_US") or next(iter(multi.values()), None)
    return None


def _format_date(date_obj: dict | None) -> str | None:
    """Convert a Dash date {year, month} to 'YYYY-MM' or 'YYYY'."""
    if not date_obj:
        return None
    year = date_obj.get("year")
    month = date_obj.get("month")
    if year and month:
        return f"{year}-{month:02d}"
    if year:
        return str(year)
    return None


def _date_range(entity: dict) -> tuple[str | None, str | None]:
    """Return (startDate, endDate) from a Dash dateRange object."""
    dr = entity.get("dateRange") or {}
    return _format_date(dr.get("start")), _format_date(dr.get("end"))


def _best_image_url(image_block: dict | None) -> str | None:
    """
    Resolve a Dash image block to the highest-resolution URL.
    Shape: {displayImage: {vectorImage: {rootUrl, artifacts: [{width, ...}]}}}
    Also handles the bare vectorImage and displayImageReference wrappers.
    """
    if not isinstance(image_block, dict):
        return None

    for key in ("displayImage", "displayImageReference", "vectorImage", "image"):
        if key in image_block and isinstance(image_block[key], dict):
            image_block = image_block[key]
    if "vectorImage" in image_block and isinstance(image_block["vectorImage"], dict):
        image_block = image_block["vectorImage"]

    root = image_block.get("rootUrl", "")
    artifacts = image_block.get("artifacts", [])
    if not root or not artifacts:
        return None

    largest = max(artifacts, key=lambda a: a.get("width", 0))
    segment = largest.get("fileIdentifyingUrlPathSegment", "")
    return (root + segment) if segment else None


# ---------- section parsers ----------

def _parse_experience(positions: list) -> list:
    result = []
    for pos in positions:
        start, end = _date_range(pos)
        result.append({
            "title": _localized(pos, "title"),
            "company": _localized(pos, "companyName"),
            "location": (
                _localized(pos, "locationName")
                or _localized(pos, "geoLocationName")
            ),
            "startDate": start,
            "endDate": end,
            "isCurrent": end is None and start is not None,
            "description": _localized(pos, "description"),
        })
    return result


def _parse_education(educations: list) -> list:
    result = []
    for edu in educations:
        start, end = _date_range(edu)
        result.append({
            "school": _localized(edu, "schoolName"),
            "degree": _localized(edu, "degreeName"),
            "fieldOfStudy": _localized(edu, "fieldOfStudy"),
            "startDate": start,
            "endDate": end,
            "grade": _localized(edu, "grade"),
            "description": (
                _localized(edu, "description")
                or _localized(edu, "activities")
            ),
        })
    return result


def _parse_certifications(certs: list) -> list:
    result = []
    for cert in certs:
        start, end = _date_range(cert)
        result.append({
            "name": _localized(cert, "name"),
            "authority": _localized(cert, "authority"),
            "issuedDate": start,
            "expiryDate": end,
            "licenseNumber": _localized(cert, "licenseNumber"),
            "url": cert.get("url"),
        })
    return result


def _parse_skills(skills: list) -> list:
    return [
        {
            "name": _localized(s, "name"),
            "endorsements": s.get("endorsementCount", 0) or 0,
        }
        for s in skills
        if _localized(s, "name")
    ]


def _parse_languages(languages: list) -> list:
    out = []
    for lang in languages:
        name = _localized(lang, "name")
        if not name:
            continue
        prof = lang.get("proficiency")
        out.append({
            "name": name,
            "proficiency": PROFICIENCY_MAP.get(prof, prof),
        })
    return out


# ---------- base profile ----------

def _parse_base(profile: dict, html_data: dict) -> dict:
    first = profile.get("firstName") or ""
    last = profile.get("lastName") or ""
    name = f"{first} {last}".strip() or html_data.get("name")

    # Location: the Dash Profile only exposes an ISO country code, so prefer the
    # richer "City, State, Country" string scraped from the public page.
    country = (profile.get("location") or {}).get("countryCode")
    location = html_data.get("location") or _COUNTRY_NAMES.get(country, country)

    return {
        "name": name,
        "headline": _localized(profile, "headline") or html_data.get("headline"),
        "location": location,
        "about": _localized(profile, "summary") or html_data.get("about"),
        "connections": profile.get("connectionsCount"),
        "followers": profile.get("followersCount"),
        "profilePicture": (
            _best_image_url(profile.get("profilePicture"))
            or html_data.get("profilePicture")
        ),
        "backgroundImage": (
            _best_image_url(
                profile.get("backgroundPicture") or profile.get("backgroundImage")
            )
            or html_data.get("backgroundImage")
        ),
    }


# ---------- main entry point ----------

def build_response(raw: dict) -> dict:
    """
    Transform the dict returned by scraper.fetch_all() into the response schema.
    Authenticated Dash data takes priority; public-HTML values fill any gaps.
    Never raises — missing sections produce empty lists or None fields.
    """
    profile = raw.get("dashProfile") or {}
    html_data = raw.get("htmlData") or {}

    experience = _parse_experience(raw.get("positions") or [])
    education = _parse_education(raw.get("educations") or [])

    # Unauthenticated fallback: JSON-LD only exposes company/school names.
    if not experience:
        experience = [
            {"title": None, "company": org.get("name"), "location": None,
             "startDate": None, "endDate": None, "isCurrent": False,
             "description": None}
            for org in html_data.get("experience_raw", [])
            if isinstance(org, dict) and org.get("name")
        ]
    if not education:
        education = [
            {"school": s.get("name"), "degree": None, "fieldOfStudy": None,
             "startDate": None, "endDate": None, "grade": None, "description": None}
            for s in html_data.get("education_raw", [])
            if isinstance(s, dict) and s.get("name")
        ]

    return {
        "publicIdentifier": raw.get("publicIdentifier"),
        **_parse_base(profile, html_data),
        "experience": experience,
        "education": education,
        "certifications": _parse_certifications(raw.get("certifications") or []),
        "skills": _parse_skills(raw.get("skills") or []),
        "languages": _parse_languages(raw.get("languages") or []),
    }
