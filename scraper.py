import re
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

REMOTE_WORDS = ["remote", "work from home", "wfh", "fully remote"]
HYBRID_WORDS = ["hybrid"]
ONSITE_WORDS = ["on-site", "onsite", "on site", "in office", "in-office"]

# Class/id name fragments that commonly wrap a single job posting (fallback strategy)
JOB_HINTS = ["job", "listing", "posting", "position", "vacancy", "career"]

# URL path patterns that usually point at a single job's detail page
JOB_LINK_RE = re.compile(
    r"/(job|jobs|career|careers|position|positions|opening|openings|"
    r"vacancy|vacancies)/[^/]+/?(?:$|\?)",
    re.IGNORECASE,
)

# Company profile / employer page link patterns
COMPANY_LINK_RE = re.compile(r"/(company|companies|employer|employers)/[^/]+/?", re.I)

# Text that marks the "act on this posting" control inside a job card
CTA_MARKERS = ["view job", "apply now", "apply", "read more", "see details", "view details"]

# Text to ignore when guessing at a location line
IGNORE_LINE_WORDS = {"save", "share", "view job", "apply", "apply now", "read more"}


@dataclass
class JobPosting:
    title: str
    company: str
    location: str
    work_type: str  # "Remote" | "Hybrid" | "On-site" | "Unknown"
    url: str


def _classify_work_type(text: str) -> str:
    t = text.lower()
    if any(w in t for w in REMOTE_WORDS):
        return "Remote"
    if any(w in t for w in HYBRID_WORDS):
        return "Hybrid"
    if any(w in t for w in ONSITE_WORDS):
        return "On-site"
    return "Unknown"


def _normalize(href: str, base_url: str) -> str:
    return urljoin(base_url, href)


def _job_hrefs_within(tag) -> set:
    return {
        _normalize(a["href"], "")
        for a in tag.find_all("a", href=True)
        if JOB_LINK_RE.search(a["href"])
    }


def _find_card(anchor, own_href: str):
    """Climb from a title anchor to the smallest ancestor that looks like
    a single job card (contains a CTA marker, and doesn't also contain a
    *different* job's detail link)."""
    block = anchor
    best = anchor.parent or anchor
    for _ in range(8):
        parent = block.parent
        if parent is None:
            break
        block = parent
        hrefs_inside = _job_hrefs_within(block)
        if len(hrefs_inside) > 1:
            break  # this level already spans more than one job card
        best = block
        if any(m in block.get_text(" ", strip=True).lower() for m in CTA_MARKERS):
            break
    return best


def _extract_from_card(card, title: str, href: str) -> JobPosting:
    company = ""
    for company_link in card.find_all("a", href=COMPANY_LINK_RE):
        text = company_link.get_text(strip=True)
        if text:
            company = text
            break

    location = ""
    lines = [ln.strip() for ln in card.get_text("\n", strip=True).split("\n") if ln.strip()]
    for i, line in enumerate(lines):
        if title[: min(20, len(title))] in line and i + 1 < len(lines):
            for candidate in lines[i + 1 : i + 4]:
                low = candidate.lower()
                if low in IGNORE_LINE_WORDS or low == company.lower():
                    continue
                if candidate.startswith("$"):
                    continue
                if len(candidate) <= 60:
                    location = candidate
                break
            break

    work_type = _classify_work_type(card.get_text(" ", strip=True))
    return JobPosting(
        title=title[:140], company=company[:80], location=location[:80],
        work_type=work_type, url=href,
    )


def _fetch_page_jobs(url: str, timeout: int) -> tuple[list[JobPosting], BeautifulSoup]:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # --- primary strategy: job-detail-link based ---
    best_anchor_for_href: dict[str, object] = {}
    for a in soup.find_all("a", href=True):
        if not JOB_LINK_RE.search(a["href"]):
            continue
        href = _normalize(a["href"], url)
        text = a.get_text(strip=True)
        if not text:
            continue
        prev = best_anchor_for_href.get(href)
        if prev is None or len(text) > len(prev.get_text(strip=True)):
            best_anchor_for_href[href] = a

    postings: list[JobPosting] = []
    for href, anchor in best_anchor_for_href.items():
        title = anchor.get_text(strip=True)
        if len(title) < 3 or len(title) > 140:
            continue
        card = _find_card(anchor, href)
        postings.append(_extract_from_card(card, title, href))

    if postings:
        return postings, soup

    # --- fallback strategy: class/id name heuristics ---
    candidates = soup.find_all(["li", "div", "article"], class_=True)
    candidates += soup.find_all(["li", "div", "article"], id=True)
    seen_text = set()
    for tag in candidates:
        attrs = " ".join(
            [tag.get("class") and " ".join(tag.get("class")) or "", tag.get("id") or ""]
        ).lower()
        if not any(hint in attrs for hint in JOB_HINTS):
            continue
        link = tag.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 3 or len(title) > 140:
            continue
        full_text = tag.get_text(" ", strip=True)
        if full_text in seen_text:
            continue
        seen_text.add(full_text)
        href = _normalize(link["href"], url)
        postings.append(
            JobPosting(
                title=title, company="", location="",
                work_type=_classify_work_type(full_text), url=href,
            )
        )

    return postings, soup


def _fetch_remoteok_jobs(url: str, timeout: int) -> list[JobPosting]:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    postings = []
    for entry in resp.json():
        title = entry.get("position", "")
        job_url = entry.get("url") or entry.get("apply_url") or ""
        if not title or not job_url:
            continue  # skips the leading API-terms notice entry
        location = entry.get("location") or "Remote"
        work_type = _classify_work_type(f"{location} {' '.join(entry.get('tags', []))}")
        postings.append(
            JobPosting(
                title=title[:140],
                company=entry.get("company", "")[:80],
                location=location[:80],
                work_type="Remote" if work_type == "Unknown" else work_type,
                url=job_url,
            )
        )
    return postings


def _fetch_remotive_jobs(url: str, timeout: int) -> list[JobPosting]:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    postings = []
    for entry in resp.json().get("jobs", []):
        title = entry.get("title", "")
        job_url = entry.get("url", "")
        if not title or not job_url:
            continue
        postings.append(
            JobPosting(
                title=title[:140],
                company=entry.get("company_name", "")[:80],
                location=(entry.get("candidate_required_location") or "Remote")[:80],
                work_type="Remote",
                url=job_url,
            )
        )
    return postings


def _fetch_wwr_rss_jobs(url: str, timeout: int) -> list[JobPosting]:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    postings = []
    for item in root.findall(".//item"):
        raw_title = (item.findtext("title") or "").strip()
        company, sep, job_title = raw_title.partition(":")
        company, job_title = (company.strip(), job_title.strip()) if sep else ("", raw_title)
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        region = (item.findtext("region") or "").strip()
        if not job_title or not link:
            continue
        work_type = _classify_work_type(region)
        postings.append(
            JobPosting(
                title=job_title[:140],
                company=company[:80],
                location=region[:80],
                work_type="Remote" if work_type == "Unknown" else work_type,
                url=link,
            )
        )
    return postings


def _find_next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    link = soup.find("a", rel="next")
    if link and link.get("href"):
        return _normalize(link["href"], current_url)
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True).lower() == "next":
            return _normalize(a["href"], current_url)
    return None


# Sources with their own single-call API/feed instead of paginated HTML.
_SINGLE_CALL_FETCHERS = {
    "remoteok.com": _fetch_remoteok_jobs,
    "remotive.com": _fetch_remotive_jobs,
    "weworkremotely.com": _fetch_wwr_rss_jobs,
}


def _dedupe(postings: list[JobPosting]) -> list[JobPosting]:
    seen_urls: set = set()
    deduped = []
    for job in postings:
        if job.url not in seen_urls:
            seen_urls.add(job.url)
            deduped.append(job)
    return deduped


def fetch_jobs(
    url: str, timeout: int = 15, max_pages: "int | None" = 1
) -> list[JobPosting]:
    """
    Fetch `url` (and optionally follow "next page" links up to max_pages
    pages total) and return a best-effort, de-duplicated list of
    JobPosting objects. Pass max_pages=None to follow "next page" links
    until none remain (fetch every page). Sources with a dedicated
    single-call API/feed (see _SINGLE_CALL_FETCHERS) ignore max_pages,
    since they return their full result set in one request. Raises
    requests.RequestException on network/HTTP failure of the first page.
    """
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, fetcher in _SINGLE_CALL_FETCHERS.items():
        if host == domain or host.endswith(f".{domain}"):
            return _dedupe(fetcher(url, timeout))

    all_postings: list[JobPosting] = []
    seen_urls: set = set()
    next_url = url
    pages_fetched = 0

    while next_url and (max_pages is None or pages_fetched < max_pages):
        try:
            postings, soup = _fetch_page_jobs(next_url, timeout)
        except requests.RequestException:
            if pages_fetched == 0:
                raise
            break

        for job in postings:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                all_postings.append(job)

        pages_fetched += 1
        more_allowed = max_pages is None or pages_fetched < max_pages
        next_url = _find_next_page_url(soup, next_url) if more_allowed else None

    return all_postings


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    for job in fetch_jobs(test_url):
        print(job)
