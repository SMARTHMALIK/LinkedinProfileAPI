"""
Transforms raw LinkedIn Voyager API responses into our clean response schema.
All parsing uses .get() throughout — missing fields silently become None/[].
"""

PROFICIENCY_MAP = {
    "ELEMENTARY": "Elementary",
    "LIMITED_WORKING": "Limited Working",
    "PROFESSIONAL_WORKING": "Professional Working",
    "FULL_PROFESSIONAL": "Full Professional",
    "NATIVE_OR_BILINGUAL": "Native or Bilingual",
}


# ---------- helpers ----------

def _format_date(date_obj: dict | None) -> str | None:
    """Convert LinkedIn date dict {year, month} to 'YYYY-MM' or 'YYYY'."""
    if not date_obj:
        return None
    year = date_obj.get("year")
    month = date_obj.get("month")
    if year and month:
        return f"{year}-{month:02d}"
    if year:
        return str(year)
    return None


def _period_dates(time_period: dict | None) -> tuple[str | None, str | None]:
    """Return (startDate, endDate) strings from a Voyager timePeriod object."""
    if not time_period:
        return None, None
    return (
        _format_date(time_period.get("startDate")),
        _format_date(time_period.get("endDate")),
    )


def _best_image_url(image_block: dict | None) -> str | None:
    """
    Resolve a Voyager vectorImage block to a URL.
    Picks the largest artifact by width.
    Handles both direct vectorImage dicts and displayImageReference wrappers.
    """
    if not image_block:
        return None

    # unwrap displayImageReference if present
    if "displayImageReference" in image_block:
        image_block = image_block["displayImageReference"]
    if "vectorImage" in image_block:
        image_block = image_block["vectorImage"]

    root = image_block.get("rootUrl", "")
    artifacts = image_block.get("artifacts", [])
    if not root or not artifacts:
        return None

    largest = max(artifacts, key=lambda a: a.get("width", 0))
    segment = largest.get("fileIdentifyingUrlPathSegment", "")
    return (root + segment) if segment else None


# ---------- section parsers ----------

def _parse_experience(profile_view: dict) -> list:
    positions = profile_view.get("positionView", {}).get("elements", [])
    result = []
    for pos in positions:
        start, end = _period_dates(pos.get("timePeriod"))
        result.append({
            "title": pos.get("title"),
            "company": pos.get("companyName"),
            "location": pos.get("locationName"),
            "startDate": start,
            "endDate": end,
            "isCurrent": end is None and start is not None,
            "description": pos.get("description"),
        })
    return result


def _parse_education(profile_view: dict) -> list:
    educations = profile_view.get("educationView", {}).get("elements", [])
    result = []
    for edu in educations:
        start, end = _period_dates(edu.get("timePeriod"))
        result.append({
            "school": edu.get("schoolName"),
            "degree": edu.get("degreeName"),
            "fieldOfStudy": edu.get("fieldOfStudy"),
            "startDate": start,
            "endDate": end,
            "grade": edu.get("grade"),
            "description": edu.get("description"),
        })
    return result


def _parse_certifications(profile_view: dict) -> list:
    certs = profile_view.get("certificationView", {}).get("elements", [])
    result = []
    for cert in certs:
        start, end = _period_dates(cert.get("timePeriod"))
        result.append({
            "name": cert.get("name"),
            "authority": cert.get("authority"),
            "issuedDate": start,
            "expiryDate": end,
            "licenseNumber": cert.get("licenseNumber"),
            "url": cert.get("url"),
        })
    return result


def _parse_skills(skills_data: dict) -> list:
    return [
        {
            "name": s.get("name"),
            "endorsements": s.get("endorsementCount", 0),
        }
        for s in skills_data.get("elements", [])
        if s.get("name")
    ]


def _parse_languages(languages_data: dict) -> list:
    return [
        {
            "name": lang.get("name"),
            "proficiency": PROFICIENCY_MAP.get(
                lang.get("proficiency", ""),
                lang.get("proficiency"),  # fall back to raw value if unmapped
            ),
        }
        for lang in languages_data.get("elements", [])
        if lang.get("name")
    ]


# ---------- main entry point ----------

def _parse_experience_from_json_ld(raw: list) -> list:
    """Parse worksFor list from JSON-LD (company names only — partial data)."""
    result = []
    for org in raw:
        if isinstance(org, dict) and org.get("name"):
            result.append({
                "title": None,
                "company": org.get("name"),
                "location": None,
                "startDate": None,
                "endDate": None,
                "isCurrent": None,
                "description": None,
            })
    return result


def _parse_education_from_json_ld(raw: list) -> list:
    """Parse alumniOf list from JSON-LD (school names only — partial data)."""
    result = []
    for school in raw:
        if isinstance(school, dict) and school.get("name"):
            result.append({
                "school": school.get("name"),
                "degree": None,
                "fieldOfStudy": None,
                "startDate": None,
                "endDate": None,
                "grade": None,
                "description": None,
            })
    return result


def build_response(raw: dict) -> dict:
    """
    Transform the dict returned by scraper.fetch_all() into our clean response schema.
    Priority order for each field: classic Voyager REST > GraphQL/Dash > HTML data.
    Never raises — missing sections produce empty lists or None fields.
    """
    html_data = raw.get("htmlData", {})
    classic = raw.get("classicProfile", {})
    pv = raw.get("profileView", {})       # classic profileView (positionView, educationView, etc.)
    dash = raw.get("dashProfile", {})

    # profileView wraps base profile under "profile" key; extract it
    pv_base = pv.get("profile", {}) if pv else {}

    # GraphQL normalized JSON: extract Profile entity from 'included' list
    if "included" in dash:
        profile_entity = next(
            (o for o in dash.get("included", []) if o.get("$type", "").endswith(".Profile")),
            None,
        )
        dash_profile = profile_entity or {}
    else:
        dash_elements = dash.get("elements", [])
        dash_profile = dash_elements[0] if dash_elements else {}

    # For base fields (headline, location, about): classic profile > profileView base > dash > html
    base_source = classic or pv_base or dash_profile
    base = _parse_base(base_source, html_data)

    # For sections: profileView has positionView/educationView/skillView/etc.
    sections_source = pv if pv else dash_profile

    # Experience: profileView > Dash > JSON-LD stub
    experience = _parse_experience(sections_source)
    if not experience:
        experience = _parse_experience_from_json_ld(html_data.get("experience_raw", []))

    # Education: profileView > Dash > JSON-LD stub
    education = _parse_education(sections_source)
    if not education:
        education = _parse_education_from_json_ld(html_data.get("education_raw", []))

    # Skills and languages live inside profileView as skillView/languageView
    skills_src = pv.get("skillView", raw.get("skills", {})) if pv else raw.get("skills", {})
    lang_src = pv.get("languageView", raw.get("languages", {})) if pv else raw.get("languages", {})

    return {
        "publicIdentifier": raw.get("publicIdentifier"),
        **base,
        "experience": experience,
        "education": education,
        "certifications": _parse_certifications(sections_source),
        "skills": _parse_skills(skills_src),
        "languages": _parse_languages(lang_src),
    }


def _parse_base(profile: dict, html_data: dict = None) -> dict:
    html_data = html_data or {}
    first = profile.get("firstName", "")
    last = profile.get("lastName", "")
    name = f"{first} {last}".strip() or html_data.get("name")
    return {
        "name": name,
        "headline": profile.get("headline") or html_data.get("headline"),
        "location": (
            profile.get("locationName")
            or profile.get("geoLocationName")
            or html_data.get("location")
        ),
        "about": profile.get("summary") or html_data.get("about"),
        "connections": profile.get("connectionsCount"),
        "followers": profile.get("followersCount"),
        "profilePicture": (
            _best_image_url(profile.get("profilePicture"))
            or html_data.get("profilePicture")
        ),
        "backgroundImage": (
            _best_image_url(
                profile.get("backgroundImage") or profile.get("backgroundPicture")
            )
            or html_data.get("backgroundImage")
        ),
    }
