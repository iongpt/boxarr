# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.1] - 2026-11-16

### Fixed
- **The setup page shows the Test Connection result**: the result box was never made visible, so a failed test gave no reason. It now shows "✓ Connected successfully!" or the reason the test failed: Radarr's answer, an HTTP error, an unexpected answer or a network failure. A network failure disables Save until a test succeeds. An answer to a test that was replaced by an edit or a newer test is ignored (#171).
- **Manual installs read the settings they save**: an install run from a clone without `BOXARR_DATA_DIRECTORY` saved its settings to `/config/local.yaml` (the data directory named by the bundled `config/default.yaml`) and never read that file again, so every save was lost at the next start. Boxarr now reads the settings file in the data directory that the first settings file names. A `--config` file still applies after a save. Docker installs are unchanged (#171).
- **Hyphenated titles no longer match unrelated films**: a title was cut at any hyphen, so "Spider-Man" was matched as "Spider", "X-Men" as "X" and "Ant-Man and the Wasp" as "Ant-Man". A hyphen now starts a subtitle only with spaces on both sides ("Spider-Man - No Way Home") or when doubled (`--`). A hyphen with a space on one side only no longer cuts (#135, #172).
- **A charted film with an IMDb id is not linked to another film of its franchise**: when the charted film was not in Radarr, the title fallbacks could link it to a film of the same franchise that Radarr lists without an IMDb id ("Avatar: Fire and Ash" to "Avatar", "Superman" to "Superman II"). An entry with an IMDb id now matches only a library film with that id, or one with its whole title. Titles that differ only in spelling still match: a hyphen or a space, accents, "&" or "and" (#135, #172).

### Changed
- **Manual installs without `BOXARR_DATA_DIRECTORY`** (Docker is not affected):
  - A `/config/local.yaml` written by an earlier save, which earlier versions ignored, is read from the first start. All its settings apply, including the Radarr URL, the API key and auto-add. Check the setup page after upgrading.
  - A settings file of your own in `./config` that names `/config` as the data directory is no longer read once `/config/local.yaml` exists. A warning names it and lists its settings that went back to their defaults; copy them into `/config/local.yaml`. Until then, the first save starts from your file, so its settings carry over.
  - An edit made in place to the bundled `config/default.yaml` stops applying once `/config/local.yaml` exists.
- **A film that Radarr lists without an IMDb id, under another title than the chart's** ("F1: The Movie" against "F1"), shows "Not in Radarr" until the status refresh links it by its TMDB id. The refresh runs after every scheduled update and from "Refresh status". Auto-add does not add it again (#172).

## [2.2.0] - 2026-11-15

### Added
- **Several box office regions at once**: pick up to 5 Box Office Mojo regions in the setup page's new region picker, or set `box_office_region` to `domestic,FR,DE` or a YAML list (`BOXARR_FEATURES_BOX_OFFICE_REGION` takes the same forms). Every run fetches each region and stores the week as one combined list (#159, #160, #166):
  - A film that charts in several regions appears once. Regions are matched by Box Office Mojo's IMDb id, then by title.
  - The weekly page has `All | <region>` tabs. A region tab shows that region's own ranks and grosses. Cards carry a region tag ("#1 DOM") and an "Also: #3 RO · #2 GB" line. In weeks with several regions, the overview's best-rank badge ("#2 FR") and the widget name the region too.
  - `box_office_limit` applies to each region. `auto_add_limit` is one cap per run across all regions. The minimum-gross filter uses the film's best weekend gross in any region.
  - If a region fails, the others are still saved. The weekly page shows a banner, the run is marked partial, and a region that failed keeps the chart it had stored, marked as stale. A region that has no chart that weekend is not a failure.
  - `/api/boxoffice/current` and `/api/boxoffice/history/{year}/W{week}` take `?region=`. Run history, `/api/scheduler/status`, `/trigger` and `/update-week` report each region's status.
  - With one region configured, the stored week and the pages are the same as in 2.1.2, apart from the changes listed under Changed.

### Fixed
- **Saved settings apply without a restart**: after a new Radarr URL or API key was saved, Run Now and the weekly job kept calling the old Radarr (and auto-added films to it). Turning automatic updates on or off, or changing the schedule, did nothing until a restart. Now each run uses the settings it starts with, the Radarr library and quality-profile caches only answer the Radarr they came from, and Save applies the schedule to the running scheduler. If the scheduler cannot take the new settings, the save still succeeds and the setup page shows a warning. Reload now starts a scheduler that was off at startup (#137, #167).
- **The schedule runs in its configured timezone**: the job fired in the host's timezone (UTC in the Docker image), whatever `boxarr.scheduler.timezone` said. A scheduled run also picks its weekend by the date in that zone. The default schedule `0 23 * * 2` runs on **Wednesday** at 23:00 (APScheduler counts Monday as 0); the docs that said Tuesday are corrected and no schedule changes day (#145, #167).
- **"Add to Radarr", auto-add, Ignore and the metadata repair act on the film that charted**: a title search can return several films with the same title (Superman 1978 and 2025). Every lookup now takes the result with Box Office Mojo's IMDb id, then tries Radarr's IMDb lookup, and never uses a film with another IMDb id as the charted film. The status refresh also unlinks stored cards that point at a film with another IMDb id (#141, #168).
- **A new film is matched to your library even when an older film of the same title is in it**: since 2.1.2, a chart entry with an IMDb id stays unmatched when the library film it lands on has another id. The matcher now skips only that film and tries one with the same whole title. Auto-add no longer spends an attempt on a film already in Radarr (part of #135, #168).
- **The auto-add limit counts only the films actually added**: films that a filter drops, ignored films and films Radarr rejects no longer use a slot. A pass stops after three Radarr failures in a row (#139, #159).
- **Run history reports successful runs as successful** and lists the 20 newest runs; each week's `_latest` copy took a slot before (#150, #159).
- **Settings in `config.yaml` survive the first save**: Boxarr reads `local.yaml` when it exists, otherwise `config.yaml`. The setup page's first save created `local.yaml` from the form alone, so settings kept only in `config.yaml` (such as `url_base`, the scheduler timezone, `log_level`, `radarr.search_for_movie` and the genre root folders) went back to their defaults. The first save now starts from `config.yaml`. An install that already has a `local.yaml` is not changed: copy any setting still missing into `local.yaml`. Boxarr logs a warning while an unread `config.yaml` sits next to `local.yaml` (#166).
- **A hung Radarr no longer freezes Boxarr from `/api/boxoffice/current` or the metadata repair**, and the weekly page no longer piles up status requests while Radarr is slow (#138, #166).

### Changed
- **Installs with no timezone set now run at 23:00 New York time**: with neither `TZ` nor `boxarr.scheduler.timezone` set, the default zone America/New_York now applies. Before, these installs ran at 23:00 host time, which is UTC in Docker (18:00 or 19:00 in New York). Set `TZ` or `boxarr.scheduler.timezone` to choose another zone. A timezone name Boxarr does not know means the host's zone (#167).
- **Daylight-saving caveat**: a schedule close to a daylight-saving change can skip a week or run twice that weekend. This is an APScheduler 3.x behaviour. The default Wednesday 23:00 is not affected. Examples: Monday 00:00 or Sunday 01:00 in New York, Monday 00:00 in London, Sunday 02:00 in Berlin (#167).
- **When Box Office Mojo and TMDB disagree on a film's IMDb id, Boxarr treats them as different films**: the card is not linked through its TMDB id, auto-add skips it, and "Add to Radarr" refuses with a message naming both ids. Add such a film in Radarr directly. This replaces the 2.1.2 behaviour, which linked those films by TMDB id (#168).
- **An area code Box Office Mojo does not know fails the run**: Box Office Mojo answers an unknown code (such as `UK` instead of `GB`) with the US & Canada chart, which was stored as that region's week. The run now fails with "Box Office Mojo has no market 'UK' (did you mean GB?)" (#159).
- **Failed runs are saved to history**: the setup page's Last Run shows "(failed)", "(no chart)" or the regions that failed (#159, #160).
- **With a low auto-add limit, films further down the chart can be added**: a limit of 1 with an ignored #1 now adds #2 (#139).
- **A week stored before 2.2.0 whose regions are not all fetched is kept**: if any region fails while updating such a week, the stored week is left as it was and the run reports a failure. It is replaced once every region answers (#159).
- **Weeks stored before 2.2.0 do not have Box Office Mojo's IMDb id**: press Update Week on a week to get exact-film picks for it. Films ignored from a wrong card in an earlier version may have stored another film's TMDB id; ignore them again from the corrected card (#168).
- **The setup region picker calls the domestic chart "US & Canada"**, as every other page does. Search still finds it by "domestic", "usa" or "canada" (#166).
- **Dependencies**: `tzlocal` is now listed (Boxarr imports it directly), and `apscheduler` is capped below 4, whose API differs.
- **Responses**: `/api/config/save` can return a `warning`. `/api/scheduler/status` reports the timezone the job fires in. `/api/scheduler/reload` starts a stopped scheduler and succeeds when updates are off (#167).

### Downgrading
- 2.1.2 can read weeks written by 2.2.0 (the new keys are additive), but shows a week with several regions as one list. Its `/api/scheduler/status` reports every saved run as successful, so a failed run saved by 2.2.0 shows there as a success with 0/0 matched.

## [2.1.2] - 2026-11-15

### Security
- **Other websites can no longer read your Radarr API key or change Boxarr**: Boxarr allowed requests from any origin (wildcard CORS) and put the stored Radarr API key into the setup page. Any website open in a browser that could reach Boxarr could read the key and change settings, start runs or upgrade movies. Now:
  - The wildcard CORS policy is gone. Only the embeddable widgets (`GET /api/widget`, `GET /api/widget/json`) can still be read from other origins.
  - Every request that is not GET, HEAD or OPTIONS is checked first and refused with 403 when a browser sent it from another site. Boxarr's own pages mark their requests with an `X-Boxarr-Request` header, so they keep working behind any reverse proxy, over http or https, with no proxy changes. curl, scripts and other server-side callers are not affected.
  - The API key is never sent to the browser. The setup page shows an empty field ("Stored - leave blank to keep"). A blank key on Test or Save uses the stored key, but only with the stored Radarr URL; any other URL needs the key typed in.
  - The interactive API docs at `/api/docs` add the header themselves.
  - (#136, #163; part of #151. The web UI still has no login.)

### Fixed
- **New sequels, spin-offs and remakes are no longer shown as an older film you already have**: when the chart film and the library film both have an IMDb id and the ids differ, they no longer match by title. "Wicked: For Good" no longer matches "Wicked", and "Superman" (2025) no longer matches the 1978 film. With auto-add on, the new film can now be added. Weeks already stored keep their old links until you press Update Week (#135, #161).
- **A slow or unreachable Radarr no longer freezes Boxarr**: requests that talk to Radarr no longer block the whole server, so `/setup` and the other pages stay usable while Radarr hangs and you can save a working URL. The Docker healthcheck answers at once instead of timing out, and the container no longer turns unhealthy. Connecting to Radarr gives up after at most 10 seconds (the read timeout is unchanged), requests that arrive together share one library download, and startup no longer waits for Radarr (#138 part 1, #164).
- **Saving the setup page keeps the settings it does not show**: a plain Save rebuilt `local.yaml` from the form and deleted every other key, such as `radarr.search_for_movie`, `radarr.monitor_option` and the cache TTL. Saving now merges into the existing file. A file that cannot be read is backed up to `local.yaml.broken` first (#142, #157).
- **Auto-add honours `radarr.search_for_movie`**: auto-added movies always triggered a Radarr search, even with the setting off (#146).
- **Films with "Pictures", "Studios", "Films", "Entertainment", "Company"... in the title stay on the chart**: a leftover filter removed them and renumbered the rows below (#143).
- **`PORT=8889` (or `BOXARR_PORT=8889`) no longer crashes startup**: an unused `boxarr_api_port` setting rejected it. The Docker healthcheck now follows `BOXARR_PORT` / `PORT` instead of always probing 8888 (#147).
- **Log level settings work**: `BOXARR_LOG_LEVEL`, `log_level` in `local.yaml` and `--log-level` now change Boxarr's own logging, and `log_format` is applied. Level names accept any case and `WARN`; an unknown name logs a warning and uses INFO instead of crashing (#148).
- **The weekly page counter for films not in Radarr is labelled "Not in Radarr"** instead of "Missing", which the page's badges use for films in Radarr without a file (#149).

### Changed
- **`BOXARR_LOG_LEVEL` now takes precedence over `LOG_LEVEL`**: it was silently ignored before. Order: `--log-level` > `BOXARR_LOG_LEVEL` > `LOG_LEVEL` > `local.yaml` > INFO. If you set both variables, remove one (#148).
- **`docker-compose.yml` no longer defines its own healthcheck**: it was hard-coded to port 8888 and overrode the image's, which now follows the configured port (#147).
- **`radarr_connected` in `/api/health` is a cached value**: it can be up to about 30 seconds old and reads `false` until the first check answers (#164).
- **`box_office_region` accepts a list of regions**: this is groundwork for multi-region support in 2.2.0. In this release only the first configured region is fetched, and the setup page keeps its single selector (#158).

## [2.1.1] - 2026-11-14

### Fixed
- **Each market's Box Office Mojo weekend is fetched correctly**: Box Office Mojo numbers every market's weekends by the day that market's weekend starts, which is not always the ISO week Boxarr asks for:
  - **France** (weekends start on Wednesday) always requested the following weekend. Scheduled runs asked for a weekend that had not happened yet, and historical updates stored the next week's chart.
  - From **1 January 2027** (a Friday), the domestic chart and every market whose weekend starts on Friday (UK, Romania, Spain, Mexico, China, India, Poland, Mongolia, ...) would have run one week behind all year, and the New Year weekend would have failed.
  - Boxarr now maps each ISO week to the market's own weekend number and checks the weekend dates printed on the fetched page. For a market whose start day is not known yet, it corrects itself with one extra request.
  - Weeks already stored for France in 2026 keep the chart they were fetched with until you press Regenerate on that week (#133, #154).
- **"Add to Radarr" no longer rewrites stored weeks**: after every manual add, the weeks listing that film were fetched again from Box Office Mojo, with only 10 movies and with the region configured at that moment. Weeks with more movies were cut to 10, and weeks fetched under another region were replaced. Adding a movie now only links it in the stored weeks. The button also sends the card's TMDB id, so the exact film shown is added rather than the first title-search result (#153).
- **Historical range updates request the weeks you picked**:
  - Ranges crossing New Year sent the wrong year.
  - Years starting on a Friday to Sunday (such as 2021 and 2027) shifted the whole selection one week earlier.
  - Week 53 could not be selected. 2026 has one: its weekend is 1-3 January 2027.
  - The dashboard now uses real ISO-week arithmetic, independent of the browser's time zone (#144, #155).

### Changed
- **Test suite runs on pytest 9.2**: monkeypatched settings are restored correctly again (#152).

## [2.1.0] - 2026-08-26

### Added
- **Full Radarr Language Vocabulary**: The language filter now offers every language Radarr can report (~57, live-fetched from your Radarr with a bundled fallback) instead of a hardcoded 12-item list, in a searchable picker with per-region suggestions. Legacy entries like "Mandarin" are aliased to Radarr's actual names ("Chinese") so they finally match, and a whitelist that can never match anything now logs a warning instead of silently blocking every auto-add (#123, #127)
- **Minimum Weekend Gross Threshold**: Optional auto-add filter — set a minimum weekend gross (USD) and only movies at or above it are auto-added. Unknown gross fails open, and below-threshold movies keep their manual Add button on the weekly page (#124, #128)
- **Hide Ignored Movies**: Opt-in UI setting that removes ignored movies from weekly and overview lists entirely, with a "Show N ignored" reveal on weekly pages; the Overview "Ignored" tab always shows them so un-ignoring stays possible (#125, #129)

### Fixed
- **Box Office Mojo Column Parsing**: The chart parser read the per-theater Average as Total Gross and the theater Change as the theater count. Columns are now resolved by header name (colspan-aware, tolerant of layout changes) with a conservative positional fallback; previously stored weeks keep their old values until regenerated (#126)
- **Filter Lists No Longer Lost on Save**: Saving the setup page in blacklist mode no longer wipes the whitelist (and vice versa) for languages and genres, switching modes no longer loses in-session selections, and hand-edited config values without a rendered checkbox survive saves; the setup page also no longer loads an outdated app.js (#123, #127)
- **Setup Save Robustness**: An invalid value left in a collapsed filter section can no longer silently block saving, out-of-range thresholds (including `Infinity`) are rejected cleanly instead of breaking the setup page, and save failures now show the real error message instead of "Unknown error" (#130)
- **Overview Stat Coherence**: All six stat cards stay arithmetically consistent when ignoring/un-ignoring movies without a reload, and the Ignored tab shows the true total when hiding is enabled (#129, #130)

### Changed
- **Language matching is normalized**: comparisons are alias-aware and case-insensitive, so a whitelist containing "Mandarin" now auto-adds Chinese-language films it previously (wrongly) excluded — review your whitelist if you relied on that entry doing nothing
- **Regional grosses are labeled USD**: Box Office Mojo reports every regional chart in US dollars; the setup hint claiming local currency was wrong and has been corrected (#126)

## [2.0.0] - 2026-07-23

### Added
- **Selectable Box Office Region**: New setup dropdown to pick a Box Office Mojo region, driving an `?area=CODE` query so charts can track box office beyond US & Canada domestic (#109, #112)
- **Configurable Box Office Timeout**: New `boxoffice_timeout` setting (default 120s, range 5-600) plus a 3-attempt retry with backoff for more resilient Box Office Mojo requests (#110, #113)
- **UI Polish**: Async refresh button with a progress endpoint, a week tenure badge, and a recent-weeks grid on the overview page (#105)

### Fixed
- **Ignore Button**: Fixed single-quote escaping in the overview ignore-button `onclick` handler so movies with apostrophes in their titles can be ignored (#106, #111)
- **Event-Loop Blocking**: Manual box office routes now offload the blocking Box Office Mojo scraper to a worker thread, so a wedged upstream (compounded by request retries) no longer stalls the asyncio event loop and hangs every request, including `/api/health`
- **Timeout Preservation on Save**: Saving configuration from the settings UI now preserves the `boxoffice_timeout` (and Radarr request timeout) value instead of silently reverting it to the default
- **Correct Auto-Add Match**: Auto-add now selects the Radarr lookup result by IMDb id instead of the first candidate, preventing the wrong movie from being added (#115)
- **Year-Boundary Navigation**: Fixed previous-week navigation across the year boundary and URL-encoded the overview search links so titles with special characters resolve correctly (#116)
- **XSS Hardening**: Escaped dynamic widget output and switched toast rendering to a safe path so untrusted values can no longer inject markup (#117)
- **Sequel Mismatch**: The matcher no longer links a charting sequel to the original film already in the library (#118)

### Changed
- **Health-Check Logging**: Suppressed `uvicorn.access` log lines for `/api/health` to keep logs readable under frequent health probes (#107)
- **Config Resilience**: A corrupt `local.yaml` no longer crashes the app — the loader falls back to setup mode, backs up the unreadable file as `.broken`, and writes config atomically (#119)
- **Weekly JSON Integrity**: Weekly page data is now written atomically and read corruption-safe, writers are serialized under a shared lock, and movies deleted from Radarr are un-linked from stored weeks (#120)

## [1.7.0] - 2026-04-05

### Added
- **Ignored Status in Overview**: Added a 6th "Ignored" stat card to the Movie Overview stats grid and a matching filter button, so ignored movies are clearly counted and filterable (#101, #102)
- **Refresh Radarr Status for Stored Data**: Added a reusable sync helper that refreshes persisted `weekly_pages/*.json` entries from current Radarr state (#99, #104)
  - Scheduler automatically refreshes stored weekly pages after update runs so historical weeks stay in sync
  - New manual "Refresh Radarr Status" button on the Movies overview page for on-demand resync
- **CD Workflow Dispatch**: Added `workflow_dispatch` trigger to CD pipeline so branch images can be manually built from the Actions UI (#102)

### Fixed
- **Badge Overlap**: Ignored badge no longer covers week info badges on movie cards — repositioned to top-center (#100, #102)
- **Filter Bleed**: Ignored movies no longer leak into Missing and Not in Radarr filter results; stat counts now match filtered views (#102)
- **Stale Missing Status**: Downloaded movies in Radarr are now correctly removed from stale "Missing" results in historical weeks without manual regeneration (#99, #104)
- **Config Save Path**: Config saves now write to the active data directory (#104)

### Community
- Thanks to [@xFlawless11x](https://github.com/xFlawless11x) for contributing PR #102 and opening issues #99, #100, and #101 — all addressed in this release!

## [1.6.4] - 2026-03-07

### Fixed
- **Weekend Calculation Bug**: Fixed `get_weekend_dates()` returning the current (incomplete) weekend when run on Friday afternoon through Sunday (#96)
  - Box Office Mojo does not publish weekend data until Monday, so requesting the current weekend returned empty results
  - Replaced fragile hour-based Friday morning check with a clean weekday guard: Friday/Saturday/Sunday always return the previous completed weekend
  - Added 11 unit tests covering all days of the week and edge cases

## [1.0.3] - 2025-08-30

### Security
- **Privacy Fix**: Corrected author attribution in git history to use username instead of real name

## [1.0.2] - 2025-08-30 [DEPRECATED - Use v1.0.3]

### Added
- **Auto-Add Override**: New checkbox in Historical Week modal to control auto-add behavior per fetch without changing global settings (#12)
- **Scheduler Debug UI**: Comprehensive debug section in Settings showing real-time scheduler status, next run time, and manual controls (#11)
- **Configuration Status Indicators**: Dashboard now prominently displays current scheduler and auto-add settings (#12)
- **4 Operating Modes**: Clear explanation of behavior combinations (Fully Automatic, Scheduled Fetch, Manual with Auto-Add, Fully Manual) (#12)
- **Diagnostic Script**: New `scripts/check_scheduler.py` for troubleshooting scheduler issues (#11)

### Fixed
- **Scheduler Dynamic Reload**: Schedule changes now take effect immediately without Docker restart (#11)
- **User Confusion**: Added clear explanations and visual feedback for all automatic operations (#12)
- **Status Updates**: Fixed dashboard not updating after adding movies to Radarr (#12)
- **Concurrency Protection**: Prevented overlapping scheduler runs with `max_instances=1` (#11)
- **Timezone Handling**: Fixed APScheduler timezone compatibility issues (#11)
- **Loading States**: Added spinners and disabled buttons during operations (#12)
- **Error Messages**: More specific error messages for different failure scenarios (#12)

### Changed
- **Better User Feedback**: Loading indicators, specific error messages, and operation explanations (#12)
- **Scheduler API**: Added `/api/scheduler/status` and `/api/scheduler/reload` endpoints (#11)
- **Configuration Validation**: Cron expressions now validated before saving (#11)

## [1.0.1] - 2025-08-29

### Fixed
- **Metadata Loss Bug**: Fixed TMDB metadata being lost when adding movies to Radarr (#10)
- **Dashboard Empty State**: Fixed "Get Started" button to fetch previous week's data instead of non-existent current week (#10)
- **Timestamp Display**: Fixed field mismatch causing "Unknown" to display in dashboard (#9)
- **Black Formatting**: Fixed CI failures due to formatting issues (#10)

### Changed
- **Git Tracking**: Cleaned up runtime-generated files from version control (#10)

### Added
- **Documentation**: Enhanced README with real-world use case and compelling "Why Boxarr?" section (#9)

## [1.0.0] - 2025-08-29

### Added
- **Dynamic Jinja2 Templates**: Complete migration from static HTML generation to server-side rendering (#6, #7)
- **Enhanced Movie Matching**: Number-to-word conversion for better title recognition (e.g., "Fantastic 4" matches "Fantastic Four") (#6)
- **JSON Metadata Storage**: Replaced HTML generation with efficient JSON data files (#6)
- **Unified Design System**: Consistent styling with purple gradient theme across all pages (#7)
- **Scheduler Debug UI**: Visual indicators and controls for scheduler management (#8)

### Changed
- **Template-Based Architecture**: Migrated from `html_generator.py` to `json_generator.py` with Jinja2 templates (#6)
- **UI/UX Overhaul**: Modern card-based layouts with improved navigation and visual feedback (#7)
- **Code Consolidation**: All JavaScript moved to single `app.js` file for better maintainability (#7)

### Fixed
- **Critical Release Blockers**: Fixed API contract mismatches, enum comparisons, and CLI mode crashes (#8)
- **Version Consistency**: Unified version numbers across all configuration files (#8)
- **Movie Status Display**: Fixed incorrect "Pending" status for released movies (#8)
- **Duplicate UI Elements**: Removed duplicate Settings buttons and broken links (#7)
- **Connection Status**: Fixed display showing actual connection state (#7)

### Removed
- **Static HTML Generation**: Completely removed in favor of dynamic rendering (#6)
- **Dead Dependencies**: Removed unused sqlalchemy/alembic packages (#8)
- **Redundant Routes**: Removed `/settings` route (consolidated into `/setup`) (#7)