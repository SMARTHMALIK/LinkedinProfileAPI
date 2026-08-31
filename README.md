# LinkedIn Profile API

A FastAPI service that accepts a LinkedIn profile URL and returns structured JSON profile data. Purely reverse-engineered — it makes direct HTTP calls to LinkedIn's internal endpoints. **No browser, no headless Chrome, no Selenium/Playwright at any point.**

**Live:** https://linkedinprofileapi-xtu8.onrender.com/
**Docs:** https://linkedinprofileapi-xtu8.onrender.com/docs

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/SMARTHMALIK/LinkedinProfileAPI.git
cd LinkedinProfileAPI
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in your LinkedIn account credentials:

```
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=your_password
```

### 3. Generate a session cookie

LinkedIn issues a **device challenge** for logins coming from datacenter IPs, so email/password alone will not authenticate once deployed to a cloud host. Run this from your own machine — your home/office IP is already trusted, so it authenticates cleanly:

```bash
python tools/get_li_at.py
```

It prints two values:

```
LINKEDIN_LI_AT=AQEDAW0-7vsBrkGQAAAB...
LINKEDIN_JSESSIONID="ajax:9170211800230432869"
```

Add both to your `.env` for local use, and to your host's environment variables for deployment. Keep `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` set too — they are the automatic refresh path when the cookie expires.

### 4. Run

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive Swagger UI.

---

## API Documentation

### `GET /profile`

Fetch a LinkedIn profile by URL or vanity slug.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | Yes | Full profile URL or bare slug (e.g. `satyanadella`) |

**Request**

```
GET /profile?url=https://www.linkedin.com/in/satyanadella/
```

**Response** `200 OK`

```json
{
  "publicIdentifier": "satyanadella",
  "name": "Satya Nadella",
  "headline": "Chairman and CEO at Microsoft",
  "location": "United States",
  "about": "As chairman and CEO of Microsoft, I define my mission and that of my company as empowering every person and every organization on the planet to achieve more.",
  "connections": null,
  "followers": null,
  "profilePicture": "https://media.licdn.com/dms/image/v2/C5603AQHHUuOSlRVA1w/...",
  "backgroundImage": "https://media.licdn.com/dms/image/v2/D5616AQFVwYcBLAcPqQ/...",
  "experience": [
    {
      "title": "Chairman and CEO",
      "company": "Microsoft",
      "location": "Greater Seattle Area",
      "startDate": "2014-02",
      "endDate": null,
      "isCurrent": true,
      "description": null
    }
  ],
  "education": [
    {
      "school": "Manipal Institute of Technology, Manipal",
      "degree": "Bachelor's Degree",
      "fieldOfStudy": "Electrical Engineering",
      "startDate": null,
      "endDate": null,
      "grade": null,
      "description": null
    }
  ],
  "certifications": [],
  "skills": [],
  "languages": []
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `400` | Invalid or unparseable LinkedIn URL |
| `404` | Profile does not exist, or is not visible to the authenticated account |
| `429` | LinkedIn is throttling the account, or blocked the server's IP (HTTP 999) with no session available. The `detail` field distinguishes the two. Retry after a few minutes. |

A `429` is not fatal — the session is preserved and requests succeed again once LinkedIn releases the throttle. See [Rate limiting](#known-limitations).

### `GET /auth/status`

Reports whether the backend LinkedIn session is active.

```json
{ "authenticated": true, "mode": "voyager-api" }
```

### `POST /auth/retry`

Re-runs the login without redeploying. Useful after refreshing credentials or approving a device challenge.

```json
{ "login_succeeded": true, "authenticated": true }
```

### `GET /health`

Liveness check. Returns `{"status": "ok"}`.

### `GET /docs`

Interactive Swagger UI.

---

## Approach

### Authentication

LinkedIn's web login page is a React SPA — the `loginCsrfParam` hidden field is injected by JavaScript and is therefore invisible to plain HTTP requests. Instead, this project authenticates through **`/uas/authenticate`**, the endpoint used by the LinkedIn Android and iOS apps. It takes the `JSESSIONID` from an initial GET as its own CSRF token, so no browser-rendered form is needed:

```
GET  /uas/authenticate                       → JSESSIONID cookie
POST /uas/authenticate                       → li_at cookie
     session_key, session_password, JSESSIONID
```

Requests carry the LinkedIn Android client's `X-Li-User-Agent` and `X-Li-Track` headers.

`JSESSIONID` doubles as the CSRF token for every subsequent API call and must be sent as the `csrf-token` header with the surrounding quotes stripped but the `ajax:` prefix intact.

### Data retrieval — Voyager Dash API

LinkedIn **retired** the classic REST endpoints; `/voyager/api/identity/profiles/{id}` and `/profileView` now return **`410 Gone`**. The current API surface is the Dash namespace, which this project uses:

| Data | Endpoint |
|---|---|
| Base profile | `/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity={slug}` |
| Experience | `/voyager/api/identity/dash/profilePositions?q=viewee&profileUrn={urn}` |
| Education | `/voyager/api/identity/dash/profileEducations?q=viewee&profileUrn={urn}` |
| Skills | `/voyager/api/identity/dash/profileSkills?q=viewee&profileUrn={urn}` |
| Certifications | `/voyager/api/identity/dash/profileCertifications?q=viewee&profileUrn={urn}` |
| Languages | `/voyager/api/identity/dash/profileLanguages?q=viewee&profileUrn={urn}` |

The base profile call resolves the vanity slug to an internal URN (`urn:li:fsd_profile:ACoAA...`), which then keys every section request.

Responses use LinkedIn's normalized JSON format — a `data` envelope plus a flat `included` array of typed entities. The parser filters `included` by `$type` suffix and maps each entity to the response schema, handling the `multiLocale*` field variants (`companyName` vs `multiLocaleCompanyName: {"en_US": ...}`) and resolving images by picking the widest artifact from the nested `displayImage.vectorImage.artifacts` array.

### Public HTML fallback

When no session is available, the service falls back to LinkedIn's unauthenticated public pages and parses the `<script type="application/ld+json">` schema.org `Person` block that LinkedIn renders for search-engine indexing. This yields name, headline, about, location and profile picture, but only company and school *names* for experience and education.

This path is also consulted when authenticated to recover the full `"City, State, Country"` location string, which the Dash Profile entity does not expose.

Requests use `curl_cffi` with a Chrome TLS fingerprint, since LinkedIn returns HTTP 999 to clients whose TLS handshake identifies them as a scripting library.

---

## Known Limitations

- **Datacenter IPs are blocked for unauthenticated requests.** LinkedIn returns HTTP 999 to cloud provider IP ranges (Render, AWS, GCP, Azure). This does not affect the authenticated Dash API, which is the primary data source — but it does mean the public-HTML fallback is unavailable when deployed, so `location` degrades to a country name. Setting `HTTPS_PROXY` to a residential proxy restores it.

- **Cloud logins hit a device challenge.** Because of the above, `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` alone cannot authenticate from a cloud host — LinkedIn responds `{"login_result": "CHALLENGE"}`. This is why `tools/get_li_at.py` exists: generate the session from a trusted IP and supply it as `LINKEDIN_LI_AT`.

- **Session expiry.** `li_at` is long-lived but is invalidated if LinkedIn's risk engine flags the session. When Voyager calls start returning 401, the service attempts an automatic re-login; if that is challenged, re-run `tools/get_li_at.py` and update the environment variable.

- **Visibility is scoped to the authenticated account.** The API returns what your LinkedIn account can see. Fields a member has restricted, or sections only visible to 1st-degree connections, come back empty. Skills, certifications and languages are commonly restricted this way — the endpoints return `200` with an empty `included` array rather than an error.

- **Private profiles** return `404`.

- **No `connections` / `followers` counts.** These are not present on the Dash Profile entity and would require additional network-info calls.

- **LinkedIn changes these endpoints without notice.** They are internal APIs with no stability guarantee — the `410 Gone` on the classic REST endpoints is exactly this happening. Endpoint paths may need revisiting if calls begin failing.

- **Rate limiting is the practical constraint.** Each `/profile` request costs up to **six** Voyager calls (one to resolve the profile, five for the sections) and nothing is cached, so request volume against LinkedIn is six times what it looks like. LinkedIn throttles per-account as well as per-IP, and it does so by **redirecting API calls to the login page** rather than returning `429`. Once throttled, calls keep failing for anywhere from a few minutes to a few hours before the account is released.

  Two consequences worth knowing:

  - Rapid successive requests — a loop, or hitting Refresh repeatedly — will trigger it. This is expected behaviour, not a fault in the service; give it a few minutes.
  - While throttled, **`/uas/authenticate` is challenged too**, so `tools/get_li_at.py` cannot mint a replacement session until the throttle clears. Generate a session *before* heavy testing rather than during it.

  The service reports throttling as `429` with an explanatory message and distinguishes it from a genuine `404`. A throttled *section* call degrades to an empty list rather than failing the whole request, so a partially-throttled fetch still returns the base profile.

  For production use this wants a cache in front of it and a request queue; neither is implemented here.

---

## Project Structure

```
app/
  main.py      FastAPI routes, error mapping, lifespan startup login
  auth.py      LinkedIn session — mobile-API login, cookie auth, CSRF headers
  scraper.py   Voyager Dash API calls + public HTML fallback
  parser.py    Maps Dash entities to the response schema
  models.py    Pydantic response models
tools/
  get_li_at.py Generates a session cookie from a trusted IP
static/
  index.html   Minimal web UI
```

## Tech Stack

FastAPI · Uvicorn · Pydantic v2 · requests · curl_cffi · python-dotenv
