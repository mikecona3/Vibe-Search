# Vibe Code & Developer Search! Made Easy.

A desktop GUI that pulls job listings from a few sources, then lets you search, filter, and page through the results locally.

## Features
- **Multiple sources** — fetches from VibeCode Careers, We Work Remotely, and RemoteOK, individually or all at once, deduplicated across sources.
- **Search** — plain text search across title/company/location, with:
  - `"quoted phrases"` for an exact match
  - `AND` — all terms must appear (e.g. `python AND remote`)
  - `OR` — any group must match (e.g. `python OR java`)
  - bare words with no operator are implicitly AND'd (e.g. `vibe code` matches listings containing both words, not necessarily adjacent)
- **Work type filter** — multi-select Remote / Hybrid / On-site.
- **Location filter** — enter a location (e.g. `Buffalo, NY`) to show only jobs within a 25 mile radius. Uses OpenStreetMap's free Nominatim
  service to geocode locations, so the first search after fetching many new listings can take a little while (results are cached afterward).
  Listings with no resolvable location (e.g. "Remote", multi-country listings) are excluded once a location filter is active.
- **Pagination** — 50 results per page.

## Requirements
- Python 3.10+
- `pip install -r requirements.txt`

## Building your own standalone app

The launcher above still needs Python installed. To build a real standalone app for your OS (no Python install required to run it):
```
pip install pyinstaller
pyinstaller --windowed --onefile --name "Job Scraper" job_scraper_gui.py
```
