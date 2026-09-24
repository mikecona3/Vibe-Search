# Job Scraper

A desktop GUI (Tkinter) that pulls job listings from a few sources, then lets
you search, filter, and page through the results locally.

## Features

- **Multiple sources** — fetches from VibeCode Careers, We Work Remotely, and
  RemoteOK, individually or all at once, deduplicated across sources.
- **Search** — plain text search across title/company/location, with:
  - `"quoted phrases"` for an exact match
  - `AND` — all terms must appear (e.g. `python AND remote`)
  - `OR` — any group must match (e.g. `python OR java`)
  - bare words with no operator are implicitly AND'd (e.g. `vibe code`
    matches listings containing both words, not necessarily adjacent)
- **Work type filter** — multi-select Remote / Hybrid / On-site.
- **Location filter** — enter a location (e.g. `Buffalo, NY`) to show only
  jobs within a 25 mile radius. Uses OpenStreetMap's free Nominatim service
  to geocode locations, so the first search after fetching many new listings
  can take a little while (results are cached afterward). Listings with no
  resolvable location (e.g. "Remote", multi-country listings) are excluded
  once a location filter is active.
- **Pagination** — 50 results per page.

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`

## Usage

```
python3 job_scraper_gui.py
```

On macOS, a `Job Scraper.app` launcher (built locally, not tracked in this
repo — see `.gitignore`) can be double-clicked instead of using the
terminal; it just runs the script above from this folder, so editing the
`.py` files updates the app immediately, no rebuild needed.

## Building your own standalone app

The launcher above still needs Python installed. To build a real
standalone app for your OS (no Python install required to run it):

```
pip install pyinstaller
pyinstaller --windowed --onefile --name "Job Scraper" job_scraper_gui.py
```

This produces a native app under `dist/` for whichever OS you run it on
(macOS `.app`, Windows `.exe`, etc.) — PyInstaller doesn't cross-compile, so
build it on the OS you want to target. On macOS the result is unsigned, so
first launch requires right-click → Open instead of a normal double-click
(Gatekeeper will otherwise block it as being from an unidentified developer).
