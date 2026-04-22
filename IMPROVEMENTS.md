# Boxarr Improvement Plan

> Generated from deep code review of `feature/mobile-ui-overhaul` (v1.7.1).  
> Work through this cycle-by-cycle. Check boxes as items land. Each section includes
> the exact file, the problem, and a concrete implementation approach.

---

## How to use this file

- Each item has a checkbox `- [ ]` — tick it when merged to main
- Items within a phase are roughly ordered by dependency (top = do first)
- Phase 5 items each deserve their own branch + PR
- Start a fresh Claude Code session per phase; paste the relevant section as context
- Backend changes should be followed by `black src/ && isort src/ && pytest` before commit

---

## Phase 1 — Foundation (do before PR #3 merges or immediately after)

These are small, low-risk, high-value. Most are 5–20 line changes.

---

### ✅ 1.1 — GZip Compression Middleware
- **File:** `src/api/app.py`
- **Problem:** All JSON and HTML responses sent uncompressed. A 100-movie week payload is 200–500 KB raw; gzip brings it to ~30–60 KB.
- **Approach:**
  ```python
  from fastapi.middleware.gzip import GZipMiddleware
  app.add_middleware(GZipMiddleware, minimum_size=500)
  ```
  Place this after CORS middleware but before route registration. `minimum_size=500` skips tiny responses where compression overhead outweighs savings.
- [ ] Done

---

### ✅ 1.2 — Security Headers Middleware
- **File:** `src/api/app.py`
- **Problem:** No `X-Frame-Options`, no `X-Content-Type-Options`, no `Content-Security-Policy`. Boxarr is commonly exposed via reverse proxy (Nginx/Traefik) to the internet.
- **Approach:** Add a lightweight middleware class (no external dep needed):
  ```python
  from starlette.middleware.base import BaseHTTPMiddleware

  class SecurityHeadersMiddleware(BaseHTTPMiddleware):
      async def dispatch(self, request, call_next):
          response = await call_next(request)
          response.headers["X-Content-Type-Options"] = "nosniff"
          response.headers["X-Frame-Options"] = "SAMEORIGIN"
          response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
          response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
          # CSP: tighten after inline scripts are moved to .js files (Phase 2)
          response.headers["Content-Security-Policy"] = (
              "default-src 'self'; "
              "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
              "style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: https:; "
              "connect-src 'self';"
          )
          return response

  app.add_middleware(SecurityHeadersMiddleware)
  ```
  Note: `unsafe-inline` is required until Phase 2 moves inline `<script>` blocks to `.js` files. Once that's done, tighten CSP to remove `unsafe-inline`.
- [ ] Done

---

### ✅ 1.3 — Fix `statusCheckInterval` Memory Leak
- **File:** `src/web/static/js/app.js`
- **Problem:** `statusCheckInterval` is assigned with `setInterval` but never cleared. Every page navigation (or hot-reload in dev) stacks another interval on top. After a few navigations, multiple overlapping polls hammer Radarr simultaneously.
- **Approach:**
  1. Find the `setInterval(checkConnection, 30000)` call (search for `checkConnection`).
  2. Clear before reassigning:
     ```js
     clearInterval(statusCheckInterval);
     statusCheckInterval = setInterval(checkConnection, 30000);
     ```
  3. Add Page Visibility API pause so polls stop when user switches tabs:
     ```js
     document.addEventListener('visibilitychange', () => {
       if (document.hidden) {
         clearInterval(statusCheckInterval);
       } else {
         clearInterval(statusCheckInterval);
         statusCheckInterval = setInterval(checkConnection, 30000);
         checkConnection(); // immediate check on tab focus
       }
     });
     ```
  4. Do the same audit for any other `setInterval` calls in app.js.
- [ ] Done

---

### ✅ 1.4 — External Links: Add `rel="noopener noreferrer"`
- **Files:** `src/web/templates/overview.html`, `src/web/templates/weekly.html`
- **Problem:** All IMDb and Wikipedia links use `target="_blank"` without `rel="noopener noreferrer"`. The opened tab can access `window.opener` and redirect the parent page — a well-known phishing vector.
- **Approach:** Search both templates for `target="_blank"` and add the rel attribute. Also add a Jinja macro to avoid repeating:
  ```jinja
  {# In base.html or a shared _macros.html #}
  {% macro ext_link(href, label) %}
  <a href="{{ href }}" target="_blank" rel="noopener noreferrer">{{ label }}</a>
  {% endmacro %}
  ```
- [ ] Done

---

### ✅ 1.5 — Pagination Touch Targets (44px minimum)
- **Files:** `src/web/static/css/style.css`, `src/web/templates/overview.html`, `src/web/templates/dashboard.html`
- **Problem:** Pagination `.page-btn` and `.page-link` elements are ~32px tall. iOS HIG and WCAG both specify 44×44px as the minimum touch target.
- **Approach:**
  ```css
  .page-btn, .page-link {
    min-height: 44px;
    min-width: 44px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  ```
  Same treatment for `.filter-btn` and `.year-btn` on the dashboard filter row.
- [ ] Done

---

### ✅ 1.6 — API Cache-Control Headers (Stable Data)
- **Files:** `src/api/routes/web.py` (or whichever route returns weekly page data)
- **Problem:** Every response returns with no `Cache-Control` header. Weekly box office data doesn't change — there's no reason the browser re-fetches it on every navigation.
- **Approach:** Add headers by data stability tier:
  ```python
  # Weekly page data — stable for a week
  response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"

  # Radarr status data — changes when user acts on it
  response.headers["Cache-Control"] = "private, max-age=60"

  # Configuration — never cache
  response.headers["Cache-Control"] = "no-store"
  ```
  For JSON API endpoints, add ETag support using a hash of the response body — enables 304 Not Modified responses that cost almost no bandwidth.
- [ ] Done

---

## Phase 2 — Code Quality

These reduce technical debt and make Phase 3/5 work significantly easier.

---

### ✅ 2.1 — Extract `dashboard.html` JavaScript to `dashboard.js`
- **Files:** `src/web/templates/dashboard.html` → `src/web/static/js/dashboard.js`
- **Problem:** `dashboard.html` is 1883 lines. The bottom ~800 lines are a `<script>` block containing the `RangeProcessor` class, all modal functions, SSE handler, metadata repair flow, progress tracking, and page-size logic. It cannot be tested, linted, or minified while embedded in HTML.
- **Approach:**
  1. Cut everything inside `{% block scripts %}<script>...</script>{% endblock %}` from dashboard.html.
  2. Paste into `src/web/static/js/dashboard.js`.
  3. Replace with a single script tag in the block:
     ```html
     {% block scripts %}
     <script src="{{ request.scope.get('root_path', '') }}/static/js/dashboard.js?v=1"></script>
     {% endblock %}
     ```
  4. Any Jinja variables referenced inside the old script block (like `{{ auto_add }}`, `{{ current_year }}`) need to be passed via `data-` attributes on a container div or a small inline JSON block:
     ```html
     <div id="dashboard-config"
          data-auto-add="{{ auto_add|lower }}"
          data-current-year="{{ current_year }}"
          data-auto-add-filters-active="{{ auto_add_filters_active|lower }}">
     </div>
     ```
     Then in dashboard.js:
     ```js
     const cfg = document.getElementById('dashboard-config').dataset;
     const autoAdd = cfg.autoAdd === 'true';
     ```
- [ ] Done

---

### ✅ 2.2 — Central `ApiClient` Class
- **File:** `src/web/static/js/app.js` (or new `src/web/static/js/api-client.js`)
- **Problem:** The same try/catch/error-display/spinner pattern is copy-pasted ~20 times. Each fetch has its own response validation, error message shape handling (`error.message || error.detail || 'Unknown error'`), and spinner toggle.
- **Approach:** A minimal class that all callers use:
  ```js
  class ApiClient {
    constructor(basePath) {
      this.basePath = basePath || '';
    }

    _url(endpoint) {
      if (!endpoint.startsWith('/')) endpoint = '/' + endpoint;
      return this.basePath + '/api' + endpoint;
    }

    async _request(method, endpoint, body, signal) {
      const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
        signal,
      };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const res = await fetch(this._url(endpoint), opts);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || err.message || `HTTP ${res.status}`);
      }
      return res.json();
    }

    get(endpoint, signal)          { return this._request('GET',    endpoint, undefined, signal); }
    post(endpoint, body, signal)   { return this._request('POST',   endpoint, body,      signal); }
    delete(endpoint, signal)       { return this._request('DELETE', endpoint, undefined, signal); }
  }

  window.api = new ApiClient(window.BOXARR_BASE_PATH);
  ```
  Then replace all ad-hoc fetches. Example replacement:
  ```js
  // Before (repeated pattern):
  const response = await fetch(apiUrl('/movies/ignore'), { method: 'POST', ... });
  if (!response.ok) throw new Error(...);
  const data = await response.json();

  // After:
  const data = await api.post('/movies/ignore', { tmdb_id, title });
  ```
- [ ] Done

---

### ✅ 2.3 — Remove Inline Style Debt from `setup.html`
- **File:** `src/web/templates/setup.html`, `src/web/static/css/style.css`
- **Problem:** The advanced settings section (genre-based root folder mappings) has grids, borders, colors, and display states all as inline `style=` attributes on ~40 elements. This is why the mobile overhaul in Phase 0 required `!important` hacks in `setup.html`'s `<style>` block.
- **Approach:**
  1. Create a new CSS section in `style.css` under `/* Setup Page — Advanced Settings */`
  2. For each recurring inline style pattern, create a named class:
     - `style="display: grid; grid-template-columns: 2fr 2fr auto; gap: 0.75rem; align-items: end;"` → `.rule-builder-grid`
     - `style="display: grid; grid-template-columns: 2fr auto 2fr 100px 120px; gap: 1rem;"` → `.mapping-rule-grid`
     - `style="background: var(--bg-secondary); padding: 1.25rem; border-radius: 8px;"` → `.settings-panel`
  3. Replace inline attributes with class names in the template.
  4. Remove the `!important` overrides from the `<style>` block in setup.html — they won't be needed.
- [ ] Done

---

### ✅ 2.4 — Search Debounce (form auto-submit, 300 ms)
- **File:** `src/web/templates/overview.html` (inline script at bottom) and `src/web/static/js/app.js`
- **Problem:** The search input form currently does a full page navigation on every submit. Even if it were wired to a live JS search, there's no debounce. Typing "Inception" = 9 requests; older responses can overwrite newer ones (race condition).
- **Approach:**
  ```js
  let searchController = null;
  let searchTimer = null;

  function handleSearchInput(value) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      if (searchController) searchController.abort();
      searchController = new AbortController();
      try {
        const data = await api.get(
          `/movies?search=${encodeURIComponent(value)}`,
          searchController.signal
        );
        renderMovieGrid(data.movies);
      } catch (e) {
        if (e.name !== 'AbortError') showToast(e.message, 'error');
      }
    }, 300);
  }
  ```
  Wire to the search input's `input` event. This replaces the current form-submit approach with live filtering.
- [ ] Done

---

### ✅ 2.5 — Consolidate CSS Variable Aliases
- **File:** `src/web/static/css/style.css`
- **Problem:** `--bg-color` and `--background` are both defined and used interchangeably (identical values). `--success` and `--success-color` are the same. `--error` and `--error-color` are the same. This creates silent drift risk when someone updates one but not the other.
- **Approach:**
  1. Decide canonical names: keep `--bg-color`, `--success-color`, `--error-color`, `--warning-color`.
  2. Remove the duplicates (`--background`, `--success`, `--error`, `--warning`).
  3. Find-replace all usages of the removed variables throughout the CSS and templates.
  4. Add fallback values everywhere: `color: var(--text-primary, #2d3748);` — if a variable ever fails to resolve, the fallback prevents invisible text.
- [ ] Done

---

## Phase 3 — Performance

These require touching Python backend code. Run `black` + `isort` + `pytest` after each.

---

### 3.1 — Async HTTP: Migrate `requests` → `httpx`
- **Files:** `src/core/boxoffice.py`, `src/core/radarr.py`
- **Problem:** `boxoffice.py` uses `requests` (synchronous) inside what are likely async FastAPI route handlers. When a scrape fires, the entire asyncio event loop blocks — no other requests can be served during that window. Same risk in `radarr.py`.
- **Approach:**
  1. Add `httpx[asyncio]` to `requirements.txt` (it's likely already there; confirm).
  2. In `radarr.py`, replace the `requests.Session()` pattern:
     ```python
     # Before
     import requests
     response = requests.get(url, headers=headers, timeout=30)

     # After
     import httpx
     async with httpx.AsyncClient(timeout=30) as client:
         response = await client.get(url, headers=headers)
     ```
  3. In `boxoffice.py`, wrap the BeautifulSoup fetch similarly.
  4. For any sync context that can't be made async (e.g., called from the scheduler), use:
     ```python
     result = await asyncio.to_thread(blocking_function, args)
     ```
  5. Propagate `async def` up the call chain to route handlers.
- [ ] Done

---

### ✅ 3.2 — TTL Cache for Radarr Quality Profiles and Root Folders
- **Files:** `src/core/radarr.py`, or a new `src/core/cache.py`
- **Problem:** Quality profiles and root folders are fetched from Radarr on every page load — the setup page, the overview page (per movie), and the weekly page. These change extremely rarely (user adds a new profile maybe once a month).
- **Approach:**
  ```python
  from cachetools import TTLCache
  from functools import wraps

  _quality_profiles_cache = TTLCache(maxsize=1, ttl=300)   # 5-minute TTL
  _root_folders_cache     = TTLCache(maxsize=1, ttl=300)

  async def get_quality_profiles(radarr_client) -> list:
      if 'profiles' in _quality_profiles_cache:
          return _quality_profiles_cache['profiles']
      profiles = await radarr_client.fetch_quality_profiles()
      _quality_profiles_cache['profiles'] = profiles
      return profiles
  ```
  Invalidate both caches on `/api/config` save (when user might have changed Radarr URL).
  `cachetools` is a common Python dep; if not already in requirements, add it.
- [ ] Done

---

### 3.3 — Bulk Radarr Movie Fetch for Status Refresh
- **Files:** `src/api/routes/movies.py`, `src/core/radarr.py`
- **Problem:** The "Refresh Radarr Status" button on the overview page likely calls Radarr's `/api/v3/movie/{id}` endpoint once per movie. 100 movies = 100 API calls. Radarr has `/api/v3/movie` (no ID) which returns **all** movies in one call.
- **Approach:**
  ```python
  async def refresh_all_statuses(radarr_client, local_movies):
      # One call to get everything
      radarr_movies = await radarr_client.get_all_movies()
      # Build lookup dict
      by_tmdb = {m['tmdbId']: m for m in radarr_movies}
      by_id   = {m['id']:     m for m in radarr_movies}

      updates = []
      for movie in local_movies:
          radarr = by_tmdb.get(movie.tmdb_id) or by_id.get(movie.radarr_id)
          if radarr:
              updates.append({
                  'tmdb_id': movie.tmdb_id,
                  'has_file': radarr.get('hasFile', False),
                  'status': radarr.get('status'),
                  'quality_profile_id': radarr.get('qualityProfileId'),
              })
      return updates
  ```
  This drops the refresh from O(n) Radarr API calls to O(1).
- [ ] Done

---

### 3.4 — Lazy-Render Tenure Badge Popovers
- **Files:** `src/web/templates/overview.html`, `src/api/routes/movies.py` (new endpoint)
- **Problem:** The tenure badge popover (all weeks a movie appeared) is fully server-rendered in HTML for every movie on the page. With 200 movies × 10 weeks each = 2000 hidden DOM nodes that are parsed, laid out, and held in memory even though they're invisible. Also the popover can clip off-screen because it uses hardcoded `position: absolute; top: 100%; right: 0`.
- **Approach:**
  1. Add a lightweight endpoint:
     ```python
     @router.get("/api/movies/{tmdb_id}/weeks")
     async def get_movie_weeks(tmdb_id: int):
         # return list of week strings this movie appeared in
         weeks = movie_service.get_weeks_for_movie(tmdb_id)
         return {"weeks": weeks}
     ```
  2. In the template, replace the full popover HTML with a placeholder:
     ```html
     <div class="tenure-badge" data-tmdb-id="{{ movie.tmdb_id }}">
         <span>📅</span>
         <span>{{ movie.weeks|length }} week{% if movie.weeks|length != 1 %}s{% endif %}</span>
         <div class="tenure-popover" aria-hidden="true"></div>
     </div>
     ```
  3. In JS, load on first hover:
     ```js
     document.addEventListener('mouseenter', async (e) => {
         const badge = e.target.closest('.tenure-badge');
         if (!badge) return;
         const popover = badge.querySelector('.tenure-popover');
         if (popover.dataset.loaded) return;
         const { weeks } = await api.get(`/movies/${badge.dataset.tmdbId}/weeks`);
         popover.innerHTML = renderWeekList(weeks);
         popover.dataset.loaded = 'true';
     }, true);
     ```
  4. For positioning, add `position: relative` to `.movie-card` and constrain the popover to the viewport using `getBoundingClientRect()` to flip it left/right as needed.
- [ ] Done

---

### 3.5 — Static File Asset Fingerprinting
- **Files:** `src/api/app.py` or `src/api/routes/web.py`, `src/web/templates/base.html`, `setup.html`
- **Problem:** CSS/JS are served at `/static/css/style.css?v=3` — a manual query-string version that must be bumped by hand. Proxies don't always respect query string cache-busting. The correct pattern is content-hash in the filename: `style.a1b2c3d4.css`.
- **Approach:**
  1. On app startup, compute MD5 of each static file and store in a dict:
     ```python
     import hashlib
     from pathlib import Path

     def compute_asset_hashes(static_dir: Path) -> dict[str, str]:
         hashes = {}
         for f in static_dir.rglob('*'):
             if f.is_file():
                 h = hashlib.md5(f.read_bytes()).hexdigest()[:8]
                 hashes[str(f.relative_to(static_dir))] = h
         return hashes
     ```
  2. Pass the hash dict to Jinja context as `asset_hashes`.
  3. In templates: `href="/static/css/style.css?v={{ asset_hashes['css/style.css'] }}"` — auto-updates whenever the file changes.
  4. Set far-future cache headers on static files:
     ```python
     # In StaticFiles mount or a custom middleware
     response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
     ```
  5. This means browsers cache CSS/JS for a year; the hash change in the URL forces a fresh fetch when you deploy.
- [ ] Done

---

## Phase 4 — UX Polish

---

### ⚠️ 4.1 — Skeleton Loading States for Movie Grid (CSS done; template wiring pending)
- **Files:** `src/web/static/css/style.css`, `src/web/templates/overview.html`, `src/web/templates/weekly.html`
- **Problem:** On page load with 100+ movies, there's a blank screen while the server renders and the browser paints. No visual feedback. On slow home networks this can be 2–4 seconds of nothing.
- **Approach:**
  Add CSS skeleton shimmer:
  ```css
  @keyframes shimmer {
    0%   { background-position: -200% 0; }
    100% { background-position:  200% 0; }
  }

  .skeleton-card {
    background: linear-gradient(
      90deg,
      var(--border-color) 25%,
      var(--bg-color)     50%,
      var(--border-color) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.5s infinite;
    border-radius: 12px;
  }

  .skeleton-poster  { aspect-ratio: 2/3; border-radius: 12px 12px 0 0; }
  .skeleton-line    { height: 12px; border-radius: 4px; margin: 8px; }
  .skeleton-line.short { width: 60%; }
  ```
  In the template, when `movies` is empty or during an initial load state, render 10 skeleton cards instead of the empty grid. When the data arrives, replace with real cards (or use the server-render path — show skeletons via JS before the Jinja data is populated during a fetch-triggered update).

  **Inspiration:** Netflix, Plex, Overseerr, Jellyfin all use skeleton cards. It's the single highest-impact perceived performance change.
- [ ] Done

---

### 4.2 — Collapse 4 Dashboard Modals into a Single Wizard
- **File:** `src/web/templates/dashboard.html`
- **Problem:** The "Update Historical Data" flow is a wizard (Select type → Configure → Confirm → Progress → Summary) but built as 4 independent modals with manual state threading. The Escape key doesn't correctly identify which modal is "on top." State from modal 1 (range selection) must be manually passed to modal 2 (confirmation) via global variables.
- **Approach:**
  1. Single `<div id="updateWizard" class="modal">` container.
  2. Inside, a `<div class="wizard-step" data-step="1|2|3|4">` for each step.
  3. A `WizardManager` class with `next()`, `back()`, `goTo(step)` methods that swap visible step divs.
  4. State lives on the wizard instance, not in globals:
     ```js
     class WizardManager {
       constructor(modalId) {
         this.el = document.getElementById(modalId);
         this.state = {};
         this.currentStep = 1;
       }
       next()       { this.goTo(this.currentStep + 1); }
       back()       { this.goTo(this.currentStep - 1); }
       goTo(step)   {
         this.el.querySelectorAll('.wizard-step').forEach(s => s.hidden = true);
         this.el.querySelector(`[data-step="${step}"]`).hidden = false;
         this.currentStep = step;
       }
       open()       { this.goTo(1); this.el.classList.add('show'); }
       close()      { this.el.classList.remove('show'); this.state = {}; }
     }
     const wizard = new WizardManager('updateWizard');
     ```
  5. Step 1: mode switcher (single/range). Step 2: form fields. Step 3: confirm + current-behavior panel. Step 4: progress (replaces progress modal). Step 5: summary (replaces summary modal).
  6. One Escape key handler: `wizard.close()`.
- [ ] Done

---

### ✅ 4.3 — ARIA Labels and Focus Trap in Modals
- **Files:** `src/web/templates/dashboard.html`, `src/web/templates/overview.html` (if it gains modals), `src/web/static/js/app.js`
- **Problem:** Modals have no `role="dialog"`, no `aria-modal="true"`, no `aria-labelledby`. Keyboard users can tab outside open modals. Screen readers don't announce modal context.
- **Approach:**
  HTML changes per modal:
  ```html
  <div id="historicalModal" class="modal" role="dialog" aria-modal="true" aria-labelledby="historicalModalTitle">
    <div class="modal-content">
      <div class="modal-header">
        <h3 id="historicalModalTitle">Update Historical Data</h3>
        <button class="modal-close" aria-label="Close dialog">&times;</button>
      </div>
  ```
  JS focus trap (add to `app.js`):
  ```js
  function trapFocus(modal) {
    const focusable = modal.querySelectorAll(
      'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];
    modal.addEventListener('keydown', function handler(e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    first?.focus();
  }
  ```
  Call `trapFocus(modal)` when any modal opens.

  Also add `role="alert"` and `aria-live="polite"` to the toast container:
  ```html
  <div id="toastContainer" role="status" aria-live="polite" aria-atomic="false"></div>
  ```
- [ ] Done

---

### ✅ 4.4 — Fluid Typography with `clamp()`
- **File:** `src/web/static/css/style.css`
- **Problem:** Font sizes step hard between breakpoints (3 media queries for heading sizes). `clamp()` gives smooth scaling between a min and max with zero media queries needed.
- **Approach:**
  ```css
  .header h1,
  .overview-title,
  .week-title,
  .dashboard-title   { font-size: clamp(1.25rem, 4vw, 1.75rem); }

  .setup-title       { font-size: clamp(1.25rem, 3.5vw, 1.875rem); }

  .stat-value        { font-size: clamp(1.5rem, 4vw, 2rem); }

  body               { font-size: clamp(0.875rem, 1.5vw, 1rem); }
  ```
  Remove the individual font-size overrides from the 480px and 768px breakpoints once `clamp()` handles the range.
- [ ] Done

---

### ✅ 4.5 — Toast Queue with Maximum Visible Count
- **File:** `src/web/static/js/app.js`
- **Problem:** Toasts can stack infinitely. A failed batch operation (e.g., 20 movies fail to add) triggers 20 simultaneous error toasts that cover the entire screen.
- **Approach:**
  ```js
  const TOAST_MAX = 3;
  const toastQueue = [];

  function showToast(message, type = 'info', duration = 4000) {
    toastQueue.push({ message, type, duration });
    if (document.querySelectorAll('.toast').length < TOAST_MAX) {
      flushToastQueue();
    }
  }

  function flushToastQueue() {
    if (!toastQueue.length) return;
    const { message, type, duration } = toastQueue.shift();
    const toast = createToastEl(message, type);
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.remove();
      flushToastQueue(); // show next queued toast
    }, duration);
  }
  ```
  Also add a "Retry" button to error toasts where a `retryFn` is passed:
  ```js
  showToast('Failed to add movie', 'error', 5000, () => addToRadarr(title, year));
  ```
- [ ] Done

---

### ✅ 4.6 — Respect `prefers-reduced-motion`
- **File:** `src/web/static/css/style.css`
- **Problem:** Users who have vestibular disorders or motion sensitivity set `prefers-reduced-motion: reduce` in their OS. Boxarr ignores this and plays all animations regardless.
- **Approach:**
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
    .skeleton-card { animation: none; background: var(--border-color); }
  }
  ```
  One block covers every animation in the entire stylesheet.
- [ ] Done

---

### ✅ 4.7 — GPU-Accelerated Animations (Replace Layout Properties)
- **File:** `src/web/static/css/style.css`
- **Problem:** Any animation using `left`, `top`, `width`, `height`, or `margin` triggers a full layout recalculation on every animation frame. Only `transform` and `opacity` are composited on the GPU and run at 60fps without layout cost.
- **Approach:** Audit every `@keyframes` block. Replace:
  ```css
  /* Before — causes reflow */
  @keyframes slideIn { from { left: -100%; } to { left: 0; } }

  /* After — GPU composited */
  @keyframes slideIn {
    from { transform: translateX(-100%); opacity: 0; }
    to   { transform: translateX(0);     opacity: 1; }
  }
  ```
  For the modal entrance animation, add `will-change: transform, opacity` before the animation triggers, and remove it after (`transitionend` event).
- [ ] Done

---

## Phase 5 — Big-Swing Features

Each item here is its own branch and PR. They're independent of each other but all depend on Phases 1–3 being done (especially the ApiClient and async backend work).

---

### 5.1 — Movie Detail Slide-Over Panel
- **Scope:** New JS component + new API endpoint
- **Problem:** Clicking a movie card does nothing. Every industry-standard app (Overseerr, Plex, Jellyfin) has a detail view — ratings, overview text, cast, streaming availability, full Radarr status.
- **Approach:**
  1. New endpoint: `GET /api/movies/{tmdb_id}/detail` — returns TMDB data (overview, backdrop, cast, ratings), current Radarr status, all weeks appeared.
  2. New CSS: `.slide-over` panel that slides in from the right at 400px wide on desktop, full-screen on mobile. Uses `transform: translateX(100%)` → `translateX(0)` transition.
  3. JS: clicking any `.movie-card` opens the panel, fetches detail, renders.
  4. Panel content:
     - Backdrop image header
     - Title, year, genres, runtime
     - IMDb / RT rating badges
     - Radarr status + quality profile
     - "Add to Radarr" / "Upgrade" / "Ignore" buttons (reuse existing action functions)
     - Weeks appeared list (reuse tenure data from 3.4)
     - Overview text
  5. Close on Escape, backdrop click, or explicit close button.

  **Inspiration:** Overseerr's slide-over is the gold standard. Plex uses a similar full-detail modal.
- [ ] Done

---

### 5.2 — Bulk Action Toolbar on Overview Page
- **Scope:** `src/web/templates/overview.html`, `src/web/static/js/app.js`, new API endpoint
- **Problem:** To add 10 missing movies to Radarr, the user must click "Add to Radarr" 10 times individually. No multi-select exists.
- **Approach:**
  1. Add checkbox to each movie card (hidden by default, appears on hover or when "Select" mode is active).
  2. "Select" mode toggle button in the overview header.
  3. When any checkbox is checked, a bulk action toolbar slides up from the bottom of the viewport (fixed position):
     ```html
     <div class="bulk-toolbar" aria-label="Bulk actions">
       <span id="selectedCount">0 selected</span>
       <button onclick="bulkAddToRadarr()">Add All to Radarr</button>
       <button onclick="bulkIgnore()">Ignore All</button>
       <button onclick="clearSelection()">Clear</button>
     </div>
     ```
  4. New endpoint: `POST /api/movies/bulk-add` accepts `{ tmdb_ids: [123, 456, ...] }`, processes sequentially with progress SSE stream.
  5. After bulk action completes, refresh the grid.

  **Inspiration:** Radarr's movie list has multi-select; Sonarr has bulk episode management.
- [ ] Done

---

### 5.3 — Statistics & Analytics Dashboard
- **Scope:** New route `GET /analytics`, new template `analytics.html`, new API endpoints
- **Problem:** No insight into how well Boxarr is performing. "What % of top-10 movies do I have?" "What genres appear most?" "How many movies did I add per month?"
- **Approach:**
  Use Chart.js (CDN, ~60KB gzipped) for charts — no build step required.

  Charts to implement:
  - **Match rate over time** (line): % of each week's top-10 that matched Radarr — shows data quality trend
  - **Movies added per month** (bar): how many movies were added via Boxarr each month
  - **Status breakdown** (donut): Downloaded / Missing / In Cinemas / Not in Radarr across all weeks
  - **Top genres** (horizontal bar): most common genres in box office top 10 over all time
  - **Weeks per movie distribution** (bar): how many movies appeared for 1 week vs 2 vs 3+

  New backend aggregation endpoint:
  ```python
  GET /api/analytics/summary
  # Returns pre-aggregated stats from all weekly JSON files
  # Cache this response aggressively (changes only when new week added)
  ```
  The analytics page links from the navbar ("Stats" or chart icon).

  **Inspiration:** Radarr's statistics page, Tautulli's analytics dashboard.
- [ ] Done

---

### 5.4 — Activity / Audit Log
- **Scope:** New `src/core/activity_log.py`, new route, new template or section on dashboard
- **Problem:** No persistent record of what Boxarr has done. "When did I add that movie?" "Did the scheduler run last Tuesday?" "Why is this movie missing?" Currently: no way to know.
- **Approach:**
  1. `ActivityLog` class appending JSON lines to `config/activity.log` (JSON Lines format — one JSON object per line, easy to tail and parse):
     ```python
     import json
     from datetime import datetime, timezone

     class ActivityLog:
         def log(self, event_type: str, details: dict):
             entry = {
                 "ts": datetime.now(timezone.utc).isoformat(),
                 "event": event_type,
                 **details
             }
             with open(self.path, 'a') as f:
                 f.write(json.dumps(entry) + '\n')
     ```
  2. Event types to log: `movie.added`, `movie.ignored`, `movie.status_changed`, `week.fetched`, `scheduler.triggered`, `scheduler.completed`, `config.saved`, `radarr.unreachable`.
  3. New endpoint: `GET /api/activity?limit=50&event=movie.added` — reads last N lines, filters by event type.
  4. Activity feed widget on dashboard (collapsible panel, last 10 events):
     ```
     [2 hrs ago]  ✅ Week 2026W15 fetched — 10 movies, 8 matched
     [3 hrs ago]  ➕ Added "Sinners" to Radarr
     [1 day ago]  ⚙️  Configuration saved
     ```
  5. Full activity log page linked from dashboard footer or navbar.

  **Inspiration:** Radarr and Sonarr both have rich history/activity views. It's among the most-used features in mature *arr setups.
- [ ] Done

---

### 5.5 — Keyboard Shortcuts + Help Overlay
- **Scope:** `src/web/static/js/app.js`, `src/web/templates/base.html`
- **Problem:** No keyboard navigation. Power users and those who prefer not to reach for the mouse have no shortcuts. Every mature media app has them.
- **Approach:**
  Shortcuts to implement:
  | Key | Action |
  |-----|--------|
  | `/` | Focus search input |
  | `?` | Open keyboard shortcuts overlay |
  | `Escape` | Close modal / clear search |
  | `←` `→` | Previous / next week (on weekly page) |
  | `r` | Trigger Refresh Radarr Status (on overview page) |
  | `g d` | Go to Dashboard (chord: press g then d within 500ms) |
  | `g o` | Go to Overview |
  | `g s` | Go to Settings |

  Implementation:
  ```js
  const shortcuts = new Map([
    ['/',       () => document.querySelector('.search-input')?.focus()],
    ['?',       () => shortcutsOverlay.toggle()],
    ['Escape',  () => closeTopModal()],
  ]);

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const handler = shortcuts.get(e.key);
    if (handler) { e.preventDefault(); handler(); }
  });
  ```

  Help overlay: `<div id="shortcutsOverlay" role="dialog" aria-label="Keyboard shortcuts">` with a two-column table of all shortcuts. Triggered by `?`. Closes on Escape or backdrop click.

  **Inspiration:** Radarr, Sonarr, GitHub, Linear all implement `?` for shortcut help. It's a power-user delight that costs very little to build.
- [ ] Done

---

## Ongoing / Cross-Cutting Concerns

These don't fit a single phase but should be addressed as files are touched.

---

### O.1 — Rate Limiting on Mutation Endpoints
- **Files:** `src/api/app.py`, mutation routes
- **Problem:** `/api/scheduler/trigger` kicks off a full scrape + Radarr sync. No protection against it being called in a loop (by a bug or a curious user).
- **Approach:** `slowapi` is a FastAPI-compatible rate limiting library:
  ```python
  from slowapi import Limiter, _rate_limit_exceeded_handler
  from slowapi.util import get_remote_address
  from slowapi.errors import RateLimitExceeded

  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter
  app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

  @router.post("/scheduler/trigger")
  @limiter.limit("3/minute")
  async def trigger_scheduler(request: Request): ...
  ```
  Apply `3/minute` to scheduler trigger, `10/minute` to config save, `60/minute` to read endpoints.
- [ ] Done

---

### O.2 — Standardize Error Response Shape
- **Files:** All route files
- **Problem:** Some endpoints return `{"detail": "..."}` (FastAPI default), some return `{"success": false, "message": "..."}`, some return bare strings. The JS has to handle all three which is why many handlers do `err.detail || err.message || 'Unknown error'`.
- **Approach:**
  ```python
  # src/api/models.py (or existing models file)
  from pydantic import BaseModel
  from typing import Optional, Any

  class ErrorResponse(BaseModel):
      success: bool = False
      message: str
      code: Optional[str] = None
      details: Optional[Any] = None

  class SuccessResponse(BaseModel):
      success: bool = True
      message: Optional[str] = None
      data: Optional[Any] = None
  ```
  Use these as return type annotations on all endpoints. JS can then always check `data.success` and `data.message`.
- [ ] Done

---

### O.3 — Docker Layer Caching Optimization
- **File:** `Dockerfile`
- **Problem:** If `COPY . .` appears before `RUN pip install`, every code change invalidates the pip cache layer — full reinstall on every build.
- **Approach (canonical pattern):**
  ```dockerfile
  FROM python:3.11-slim AS base

  WORKDIR /app

  # 1. Copy only dependency manifests first
  COPY requirements.txt requirements-prod.txt ./

  # 2. Install deps — this layer is cached until requirements change
  RUN pip install --no-cache-dir -r requirements.txt

  # 3. Copy source code — only this layer rebuilds on code changes
  COPY . .

  EXPOSE 8080
  CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
  ```
- [ ] Done

---

### ✅ O.4 — `prefers-color-scheme` in `<meta>` for Status Bar
- **File:** `src/web/templates/base.html`
- **Problem:** On iOS Safari, the browser chrome (address bar, status bar) defaults to white regardless of Boxarr's dark mode. The `theme-color` meta tag controls this.
- **Approach:**
  ```html
  <!-- In base.html <head> -->
  <meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#1e293b" media="(prefers-color-scheme: dark)">
  ```
  Small touch but makes the app feel native on mobile Safari and Android Chrome.
- [ ] Done

---

## Summary Checklist by Priority

> Last updated: 2026-04-22 — v1.7.3 on `feature/phase2-should-do` ([PR #5](https://github.com/xFlawless11x/boxarr/pull/5))

### Must-do before Phase 5 work begins
- [x] 1.1 GZip middleware
- [x] 1.2 Security headers
- [x] 1.3 Interval memory leak
- [x] 2.1 Extract dashboard.js
- [x] 2.2 ApiClient class
- [ ] 3.1 httpx async migration ← **next priority**
- [ ] 3.3 Bulk Radarr fetch

### Should-do for code health
- [x] 1.4 External link rel attributes
- [x] 1.5 Touch targets 44px
- [x] 1.6 Cache-Control headers
- [x] 2.3 Remove setup.html inline styles
- [x] 2.4 Search debounce (form auto-submit, 300 ms; full AbortController live search still possible as enhancement)
- [x] 2.5 CSS variable aliases
- [x] 3.2 TTL cache Radarr profiles + root folders
- [ ] 3.4 Lazy tenure popovers
- [ ] 3.5 Asset fingerprinting

### UX polish
- [~] 4.1 Skeleton loading states — CSS + shimmer done; template wiring (showing skeletons before JS-triggered fetch) still pending
- [ ] 4.2 Modal wizard (consolidate 4 modals → single step wizard)
- [x] 4.3 ARIA + focus trap (role/aria-modal/aria-labelledby + MutationObserver trap)
- [x] 4.4 clamp() typography
- [x] 4.5 Toast queue (max 3 visible, overflow queued, CSS-driven animations)
- [x] 4.6 prefers-reduced-motion
- [x] 4.7 GPU-accelerated animations (will-change on modal + toast)

### Big swings (own PRs)
- [ ] 5.1 Movie detail slide-over
- [ ] 5.2 Bulk action toolbar
- [ ] 5.3 Statistics dashboard
- [ ] 5.4 Activity log
- [ ] 5.5 Keyboard shortcuts

### Cross-cutting
- [ ] O.1 Rate limiting
- [ ] O.2 Standardize error shape
- [ ] O.3 Docker layer caching
- [x] O.4 theme-color meta tag
