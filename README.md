# LinkedIn Profile API

A FastAPI server that accepts a LinkedIn profile URL and returns structured JSON profile data using pure HTTP calls to LinkedIn's internal endpoints

---
## Deployed Link:
```
https://linkedinprofileapi-xtu8.onrender.com/
```

## Setup Instructions

### 1. Clone and install

```bash
git clone https://github.com/SMARTHMALIK/LinkedinProfileAPI.git
cd LinkedinProfileAPI
pip install -r requirements.txt
```

### 2. Extract LinkedIn cookies

You need a LinkedIn account. After logging in:

1. Open Chrome → go to `https://www.linkedin.com`
2. Open **DevTools** (`F12`) → **Application** → **Cookies** → `https://www.linkedin.com`
3. Copy the value of `li_at`
4. Copy the value of `JSESSIONID` (looks like `ajax:1234567890123456789`)

### 3. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```
LINKEDIN_LI_AT=your_li_at_value_here
LINKEDIN_JSESSIONID=ajax:your_jsessionid_here
```

### 4. Run

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` to use the interactive Swagger UI.

---

## API Documentation

### `GET /profile`

Fetch a LinkedIn profile by URL or vanity slug.

**Query parameter:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | Yes | Full LinkedIn profile URL or bare slug (e.g. `john-doe`) |

**Example request:**

```
GET /profile?url=https://www.linkedin.com/in/john-doe
```

**Example response:**

```json
{
  "publicIdentifier": "john-doe",
  "name": "John Doe",
  "headline": "Software Engineer at Google",
  "location": "San Francisco, CA",
  "about": "Passionate about building scalable systems...",
  "connections": null,
  "followers": null,
  "profilePicture": "https://media.licdn.com/dms/image/...",
  "backgroundImage": "https://media.licdn.com/dms/image/...",
  "experience": [
    {
      "title": "Software Engineer",
      "company": "Google",
      "location": "Mountain View, CA",
      "startDate": "2021-06",
      "endDate": null,
      "isCurrent": true,
      "description": null
    }
  ],
  "education": [
    {
      "school": "MIT",
      "degree": "B.S.",
      "fieldOfStudy": "Computer Science",
      "startDate": "2017-08",
      "endDate": "2021-05",
      "grade": null,
      "description": null
    }
  ],
  "certifications": [],
  "skills": [],
  "languages": []
}
```

**Error responses:**

| Status | Meaning |
|--------|---------|
| `400` | Invalid or unparseable LinkedIn URL |
| `404` | Profile not found or is private |
| `429` | LinkedIn rate limit reached |
| `503` | LinkedIn session expired (re-extract `li_at`) |

### `GET /health`

Liveness check. Returns `{"status": "ok"}`.

### `GET /docs`

Interactive Swagger UI with live request testing.

---

## Approach

This API is a purely reverse-engineered solution that directly hits LinkedIn's endpoints using Python `requests` — no browser is launched at any point.

**Data is collected from three sources:**

**1. Public HTML (primary)**
LinkedIn renders complete profile data server-side for unauthenticated requests so that search engines (Google, Bing) can index public profiles. We fetch `https://www.linkedin.com/in/{id}/` without any cookies and extract the `<script type="application/ld+json">` blocks embedded in the HTML. These contain name, headline, location, about, experience (with company names), education (with school names), and profile picture — all structured as schema.org `Person` objects.

**2. Authenticated HTML (secondary)**
A logged-in page visit using the `li_at` session cookie. Even though LinkedIn's authenticated profile page is a Single Page Application (no server-rendered data), the initial HTML shell contains pre-rendered image tags with profile picture and background image URLs, as well as the user's internal URN (`urn:li:fsd_profile:...`). This visit also refreshes the `JSESSIONID` cookie needed for API calls.

**3. Voyager API (tertiary)**
LinkedIn's internal REST API at `/voyager/api/` — the same API the LinkedIn web app uses. Called with `li_at` + `JSESSIONID` cookies and the `csrf-token` header. Used for:
- `/voyager/api/me` — verifies session health
- `/voyager/api/typeahead/hitsV2` — fallback for name and headline via search

**Session management:**
The `li_at` cookie is a long-lived LinkedIn session token. `JSESSIONID` doubles as the CSRF token and must be passed as both a cookie and `csrf-token` header (including the `ajax:` prefix). The server warms up the session at startup by visiting LinkedIn's feed page.

---

## Known Limitations

- **`li_at` expiry** — The session cookie expires roughly every 12 months. Once expired, all authenticated API calls return 403 and must be re-extracted from the browser.
- **IP rate limiting** — LinkedIn returns HTTP 999 (bot-blocked) when too many requests originate from the same IP in a short window. Local development IPs can be temporarily blocked after heavy testing. Deployed servers on cloud platforms (Render.com) use fresh IPs and are unaffected.
- **Private profiles** — Only public LinkedIn profiles are supported. Profiles set to private return no data.
- **Experience and education detail** — When public HTML is the only data source, experience entries contain company names only (no job titles or dates) and education entries contain school names only (no degree or field of study). This is a limitation of what LinkedIn embeds in its JSON-LD schema.
- **Skills, certifications, languages** — These sections are not available in LinkedIn's public HTML. They require a working authenticated Voyager API response, which depends on LinkedIn's internal API stability.
- **LinkedIn API changes** — LinkedIn's internal Voyager API endpoints . Classic REST endpoints (`/voyager/api/identity/profiles/`) were removed in 2025 (HTTP 410). The public HTML approach is the most stable fallback as it is driven by LinkedIn's SEO requirements.
