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

### 3. Supply a session cookie

LinkedIn issues a **device challenge** for logins coming from datacenter IPs, so email/password alone will not authenticate once deployed to a cloud host. The service needs a cookie obtained elsewhere.

**Recommended — copy the cookies from a logged-in browser:**

1. Log in to linkedin.com in Chrome
2. DevTools (<kbd>F12</kbd>) → Application → Cookies → `https://www.linkedin.com`
3. Copy `li_at`, `JSESSIONID`, `liap` and `bcookie` into a single variable:

```
LINKEDIN_COOKIES=li_at=AQED...; JSESSIONID="ajax:1234567890123456789"; liap=true; bcookie=v=2&...
```

`li_at` on its own is not a session. LinkedIn expects it to arrive alongside its supporting cookies and rejects requests that are missing them. A browser session has also already cleared device verification, so it survives far longer than one minted by a script.

**Alternative — mint one programmatically:**

```bash
python tools/get_li_at.py
```

Run it from your own machine; a home/office IP is trusted, so it usually authenticates without a challenge. It verifies the session before printing it and outputs a ready-to-paste `LINKEDIN_COOKIES` line.

If it reports `CHALLENGE`, LinkedIn has flagged the account and scripted logins will keep being refused — use the browser method above.

Add the value to `.env` for local use and to your host's environment variables for deployment.

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
| `503` | LinkedIn has **rejected** the stored cookie. Not recoverable by waiting — the cookie must be replaced. See [Refreshing the session](#refreshing-the-session). |

The `429` / `503` split matters. A `429` clears on its own; a `503` never will, because LinkedIn has explicitly invalidated the credential and is answering every API call with a redirect carrying `Set-Cookie: li_at=delete me`.

### `GET /auth/status`

Reports whether the backend LinkedIn session is active.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `validate` | bool | `false` | Check the session against LinkedIn instead of reporting cached state. Costs one API call. |

```json
{ "authenticated": true, "mode": "voyager-api", "validated": false }
```

Pass `?validate=true` when you need certainty. The cached answer is set at startup and updated whenever a request discovers the cookie has been rejected.

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

### Detecting a rejected session

A cookie sitting in the jar is not evidence of a working session. When LinkedIn rejects a cookie it does **not** return `401`; it redirects the API call back to its own URL in a loop, sending this on every hop:

```
302 Found
Location:   <the same /voyager/api/... URL>
Set-Cookie: li_at=delete me; Domain=.www.linkedin.com; Max-Age=0
Set-Cookie: liap=delete me;  Domain=.linkedin.com;     Max-Age=0
```

The deletion is scoped to `.www.linkedin.com` while the cookie is set on `.linkedin.com`, so it never matches and the dead cookie stays in the jar looking healthy. Trusting its presence makes the service report itself as authenticated while every request fails.

The service therefore treats **any** 3xx on a Voyager call as a rejected session — those endpoints never legitimately redirect — marks the session invalid, and answers `503` with instructions to replace the cookie. It also validates against `/voyager/api/me` at startup so `/auth/status` is accurate before the first request arrives.

---

## Refreshing the session

When `/profile` returns `503`, or `/auth/status?validate=true` reports `authenticated: false`, the cookie has been rejected and must be replaced.

1. Open linkedin.com in a browser and clear any security prompt on the account
2. DevTools → Application → Cookies → `https://www.linkedin.com`
3. Copy `li_at`, `JSESSIONID`, `liap`, `bcookie`
4. Update `LINKEDIN_COOKIES` in your host's environment variables

**What causes rejection.** LinkedIn's risk engine flags accounts that log in repeatedly from scripts or drive high API volume. A flagged account has its scripted sessions invalidated almost immediately — the symptom is a fresh cookie that works for one request and then fails on every subsequent one. Waiting does not help; the account has to settle, and sessions should come from a browser rather than `/uas/authenticate`.

Once flagged, `python tools/get_li_at.py` will also return `CHALLENGE` even from a trusted IP, which is a useful confirmation of what is happening.

---

## Known Limitations

- **Datacenter IPs are blocked for unauthenticated requests.** LinkedIn returns HTTP 999 to cloud provider IP ranges (Render, AWS, GCP, Azure). This does not affect the authenticated Dash API, which is the primary data source — but it does mean the public-HTML fallback is unavailable when deployed, so `location` degrades to a country name. Setting `HTTPS_PROXY` to a residential proxy restores it.

- **Cloud logins hit a device challenge.** Because of the above, `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` alone cannot authenticate from a cloud host — LinkedIn responds `{"login_result": "CHALLENGE"}`. The session has to be obtained from a trusted IP and supplied as `LINKEDIN_COOKIES`.

- **Session rejection is the main operational cost.** `li_at` is nominally long-lived, but LinkedIn's risk engine invalidates it as soon as the account is flagged — and it signals this with a redirect loop, not a `401`, so it is easy to misread as throttling. The service detects it and answers `503`; recovery is manual. See [Refreshing the session](#refreshing-the-session).

- **Visibility is scoped to the authenticated account.** The API returns what your LinkedIn account can see. Fields a member has restricted, or sections only visible to 1st-degree connections, come back empty. Skills, certifications and languages are commonly restricted this way — the endpoints return `200` with an empty `included` array rather than an error.

- **Private profiles** return `404`.

- **No `connections` / `followers` counts.** These are not present on the Dash Profile entity and would require additional network-info calls.

- **LinkedIn changes these endpoints without notice.** They are internal APIs with no stability guarantee — the `410 Gone` on the classic REST endpoints is exactly this happening. Endpoint paths may need revisiting if calls begin failing.

- **Request volume is six times what it looks like.** Each `/profile` call costs up to **six** Voyager requests — one to resolve the vanity slug to a URN, five for the sections — and nothing is cached. Sustained use is what gets an account flagged, and a flagged account has its scripted sessions invalidated on sight, producing the "works once, then fails forever" pattern.

  A cache and a request queue would both help materially; neither is implemented here. If LinkedIn does return an explicit `429`, the service surfaces it as `429` and a throttled *section* degrades to an empty list rather than failing the whole request, so a partial fetch still returns the base profile.

- **Recovery from a rejected session is manual.** The cookie can only be replaced from a trusted IP — a browser, or `tools/get_li_at.py` on a local machine. A cloud host cannot refresh its own session, because `/uas/authenticate` is challenged from datacenter IPs. Automating this would require routing the server's traffic through a residential proxy.

---

## Project Structure

```
app/
  main.py      FastAPI routes, error mapping, startup login + session validation
  auth.py      LinkedIn session — cookie auth, mobile-API login, CSRF headers,
               rejected-session tracking
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
