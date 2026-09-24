import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

from geocoding import geocode, haversine_miles
from scraper import fetch_jobs, JobPosting

WORK_TYPE_OPTIONS = ["Remote", "Hybrid", "On-site"]
RESULTS_PER_PAGE = 50
LOCATION_RADIUS_MILES = 25
LOCATION_DEBOUNCE_MS = 600
JOB_SITES = {
    "VibeCode Careers": "https://vibecodecareers.com/jobs/",
    "We Work Remotely": "https://weworkremotely.com/remote-jobs.rss",
    "RemoteOK": "https://remoteok.com/api",
}

# Matches a quoted phrase (kept whole, quotes included) or a single bare word.
_SEARCH_TOKEN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')


def _strip_quotes(term: str) -> str:
    term = term.strip()
    if len(term) >= 2 and term[0] == term[-1] and term[0] in "\"'":
        term = term[1:-1].strip()
    return term


def _parse_search_query(raw: str) -> list[list[str]]:
    """Parse search text into OR-groups of AND-terms: every term in a group
    must be found (AND) for at least one group to match (OR). Bare words with
    no operator are implicitly AND'd together (each must appear, not
    necessarily adjacent) -- e.g. "vibe code" requires both "vibe" and
    "code" somewhere in the text. A quoted phrase is kept whole as a single
    literal term, even if it contains the words AND/OR.
    """
    tokens = _SEARCH_TOKEN_RE.findall(raw.strip())
    or_groups: list[list[str]] = [[]]
    for token in tokens:
        if token.upper() == "OR":
            or_groups.append([])
        else:
            or_groups[-1].append(token)

    parsed_groups = []
    for group_tokens in or_groups:
        terms = [
            _strip_quotes(token).lower()
            for token in group_tokens
            if token.upper() != "AND"
        ]
        if terms:
            parsed_groups.append(terms)
    return parsed_groups


class JobScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Job Listing Scraper")
        self.geometry("900x560")
        self.minsize(700, 420)

        self.all_jobs: list[JobPosting] = []
        self.filtered_jobs: list[JobPosting] = []
        self.current_page = 0
        self.search_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self._location_debounce_id: str | None = None
        self._location_filter_token = 0
        self.all_sites_var = tk.BooleanVar(value=True)
        self.site_vars: dict[str, tk.BooleanVar] = {
            name: tk.BooleanVar(value=True) for name in JOB_SITES
        }
        self.site_popup: tk.Toplevel | None = None
        self.all_work_types_var = tk.BooleanVar(value=True)
        self.work_type_vars: dict[str, tk.BooleanVar] = {
            name: tk.BooleanVar(value=True) for name in WORK_TYPE_OPTIONS
        }
        self.work_type_popup: tk.Toplevel | None = None

        self._build_top_bar()
        self._build_results_table()
        self._build_pagination_bar()

        self.search_var.trace_add("write", lambda *_: self._apply_filters())
        self.location_var.trace_add("write", lambda *_: self._on_location_changed())

    # ---------- UI construction ----------

    def _build_top_bar(self):
        search_frame = ttk.Frame(self, padding=(10, 10, 10, 0))
        search_frame.pack(fill="x")

        ttk.Label(search_frame, text="Search:").pack(side="left")
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8)

        ttk.Label(search_frame, text="Location:").pack(side="left")
        location_entry = ttk.Entry(search_frame, textvariable=self.location_var, width=18)
        location_entry.pack(side="left", padx=8)

        self.fetch_btn = ttk.Button(
            search_frame, text="Search Jobs", command=self._on_fetch
        )
        self.fetch_btn.pack(side="left")

        site_frame = ttk.Frame(self, padding=10)
        site_frame.pack(fill="x")

        ttk.Label(site_frame, text="Job Search Website:").pack(side="left")

        self.site_button = ttk.Button(
            site_frame, text=self._site_button_text(), command=self._toggle_site_popup
        )
        self.site_button.pack(side="left", padx=(8, 24))

        ttk.Label(site_frame, text="Work type:").pack(side="left")
        self.work_type_button = ttk.Button(
            site_frame,
            text=self._work_type_button_text(),
            command=self._toggle_work_type_popup,
        )
        self.work_type_button.pack(side="left", padx=8)

    def _build_results_table(self):
        columns = ("title", "company", "location", "work_type")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for col, label, width in [
            ("title", "Title", 320),
            ("company", "Company", 180),
            ("location", "Location", 160),
            ("work_type", "Work Type", 100),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")

        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.tree.bind("<Double-1>", self._on_row_open)

        self.status_var = tk.StringVar(value="Click Search Jobs to get started.")
        ttk.Label(self, textvariable=self.status_var, padding=(10, 0)).pack(
            fill="x", anchor="w"
        )

    def _build_pagination_bar(self):
        frame = ttk.Frame(self, padding=(10, 0))
        frame.pack()

        self.prev_btn = ttk.Button(frame, text="< Prev", command=self._go_prev_page)
        self.prev_btn.pack(side="left")

        self.page_label_var = tk.StringVar(value="Page 1 of 1")
        ttk.Label(frame, textvariable=self.page_label_var).pack(side="left", padx=10)

        self.next_btn = ttk.Button(frame, text="Next >", command=self._go_next_page)
        self.next_btn.pack(side="left")

    # ---------- behavior ----------

    def _site_button_text(self) -> str:
        selected = [name for name, var in self.site_vars.items() if var.get()]
        if not selected:
            return "Select sites ▾"
        if len(selected) == len(JOB_SITES):
            return "All sites ▾"
        if len(selected) == 1:
            return f"{selected[0]} ▾"
        return f"{len(selected)} sites selected ▾"

    def _refresh_site_button(self):
        self.site_button.config(text=self._site_button_text())

    def _open_checklist_popup(self, anchor, title, all_var, option_vars, on_toggle, on_close):
        """Build a small popup below `anchor` with an "All" checkbox plus one
        checkbox per entry in `option_vars`. `on_toggle` runs after any
        checkbox changes (e.g. to refresh a button label or re-filter
        results); `on_close` runs when the popup is dismissed."""
        popup = tk.Toplevel(self)
        popup.title(title)
        popup.transient(self)
        popup.resizable(False, False)

        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        popup.geometry(f"+{x}+{y}")

        content = ttk.Frame(popup, padding=10)
        content.pack(fill="both", expand=True)

        def on_all_toggle():
            value = all_var.get()
            for var in option_vars.values():
                var.set(value)
            on_toggle()

        def on_option_toggle():
            all_var.set(all(var.get() for var in option_vars.values()))
            on_toggle()

        ttk.Checkbutton(
            content, text="All", variable=all_var, command=on_all_toggle
        ).pack(anchor="w", pady=(0, 4))
        ttk.Separator(content, orient="horizontal").pack(fill="x", pady=4)
        for name, var in option_vars.items():
            ttk.Checkbutton(
                content, text=name, variable=var, command=on_option_toggle
            ).pack(anchor="w")

        ttk.Button(content, text="Done", command=on_close).pack(pady=(10, 0))
        popup.protocol("WM_DELETE_WINDOW", on_close)
        return popup

    def _toggle_site_popup(self):
        if self.site_popup is not None and self.site_popup.winfo_exists():
            self._close_site_popup()
        else:
            self.site_popup = self._open_checklist_popup(
                self.site_button,
                "Select Job Sites",
                self.all_sites_var,
                self.site_vars,
                self._refresh_site_button,
                self._close_site_popup,
            )

    def _close_site_popup(self):
        if self.site_popup is not None:
            self.site_popup.destroy()
            self.site_popup = None

    def _work_type_button_text(self) -> str:
        selected = [name for name, var in self.work_type_vars.items() if var.get()]
        if not selected:
            return "Select work types ▾"
        if len(selected) == len(WORK_TYPE_OPTIONS):
            return "All work types ▾"
        if len(selected) == 1:
            return f"{selected[0]} ▾"
        return f"{len(selected)} work types selected ▾"

    def _refresh_work_type_button(self):
        self.work_type_button.config(text=self._work_type_button_text())
        self._apply_filters()

    def _toggle_work_type_popup(self):
        if self.work_type_popup is not None and self.work_type_popup.winfo_exists():
            self._close_work_type_popup()
        else:
            self.work_type_popup = self._open_checklist_popup(
                self.work_type_button,
                "Select Work Types",
                self.all_work_types_var,
                self.work_type_vars,
                self._refresh_work_type_button,
                self._close_work_type_popup,
            )

    def _close_work_type_popup(self):
        if self.work_type_popup is not None:
            self.work_type_popup.destroy()
            self.work_type_popup = None

    def _on_fetch(self):
        selected_names = [name for name, var in self.site_vars.items() if var.get()]
        if not selected_names:
            messagebox.showwarning(
                "No site selected", "Please select at least one job site."
            )
            return
        urls = [JOB_SITES[name] for name in selected_names]

        self.fetch_btn.config(state="disabled")
        label = "all job sites" if len(selected_names) == len(JOB_SITES) else ", ".join(
            selected_names
        )
        self.status_var.set(f"Fetching all jobs from {label} ...")
        threading.Thread(target=self._fetch_worker, args=(urls,), daemon=True).start()

    def _fetch_worker(self, urls: list[str]):
        jobs: list[JobPosting] = []
        seen_urls: set = set()
        error = None
        for url in urls:
            try:
                for job in fetch_jobs(url, max_pages=None):
                    if job.url not in seen_urls:
                        seen_urls.add(job.url)
                        jobs.append(job)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                error = str(exc)
                break
        self.after(0, self._fetch_done, jobs, error)

    def _fetch_done(self, jobs: list[JobPosting], error: str | None):
        self.fetch_btn.config(state="normal")
        if error:
            self.status_var.set(f"Error: {error}")
            messagebox.showerror("Fetch failed", error)
            return

        self.all_jobs = jobs
        self.status_var.set(
            f"Found {len(jobs)} candidate postings."
            if jobs
            else "No postings detected on that page (site may need JS rendering)."
        )
        self._apply_filters()

    def _on_location_changed(self):
        if self._location_debounce_id is not None:
            self.after_cancel(self._location_debounce_id)
        self._location_debounce_id = self.after(LOCATION_DEBOUNCE_MS, self._apply_filters)

    def _apply_filters(self):
        self._location_debounce_id = None
        search_groups = _parse_search_query(self.search_var.get())
        selected_types = {name for name, var in self.work_type_vars.items() if var.get()}
        show_all_types = len(selected_types) == len(WORK_TYPE_OPTIONS)

        filtered = []
        for job in self.all_jobs:
            if not show_all_types and job.work_type not in selected_types:
                continue
            haystack = f"{job.title} {job.company} {job.location}".lower()
            if search_groups and not any(
                all(term in haystack for term in and_terms) for and_terms in search_groups
            ):
                continue
            filtered.append(job)

        self.filtered_jobs = filtered
        self.current_page = 0
        self._render_page()

        self._location_filter_token += 1
        token = self._location_filter_token
        location_query = self.location_var.get().strip()
        if not location_query:
            return

        self.status_var.set(f'Finding jobs within {LOCATION_RADIUS_MILES} mi of "{location_query}" ...')
        threading.Thread(
            target=self._location_filter_worker,
            args=(filtered, location_query, token),
            daemon=True,
        ).start()

    def _location_filter_worker(self, jobs: list[JobPosting], location_query: str, token: int):
        origin = geocode(location_query)
        if origin is None:
            self.after(0, self._location_filter_failed, location_query, token)
            return

        unique_locations = sorted({job.location for job in jobs if job.location})
        total = len(unique_locations)
        coords_by_location: dict[str, "tuple[float, float] | None"] = {}
        for i, loc in enumerate(unique_locations, start=1):
            if token != self._location_filter_token:
                return  # a newer location query superseded this one
            coords_by_location[loc] = geocode(loc)
            self.after(
                0,
                self.status_var.set,
                f'Geocoding locations ({i}/{total}) for "{location_query}" ...',
            )

        matched = [
            job
            for job in jobs
            if coords_by_location.get(job.location) is not None
            and haversine_miles(origin, coords_by_location[job.location])
            <= LOCATION_RADIUS_MILES
        ]

        if token != self._location_filter_token:
            return
        self.after(0, self._location_filter_done, matched, location_query, token)

    def _location_filter_done(self, matched: list[JobPosting], location_query: str, token: int):
        if token != self._location_filter_token:
            return
        self.filtered_jobs = matched
        self.current_page = 0
        self._render_page()
        self.status_var.set(
            f'Showing {len(matched)} job(s) within {LOCATION_RADIUS_MILES} mi of "{location_query}".'
        )

    def _location_filter_failed(self, location_query: str, token: int):
        if token != self._location_filter_token:
            return
        self.status_var.set(f'Could not find a location matching "{location_query}".')

    def _total_pages(self) -> int:
        if not self.filtered_jobs:
            return 1
        return (len(self.filtered_jobs) - 1) // RESULTS_PER_PAGE + 1

    def _render_page(self):
        total_pages = self._total_pages()
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        start = self.current_page * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        page_items = self.filtered_jobs[start:end]

        self.tree.delete(*self.tree.get_children())
        for job in page_items:
            self.tree.insert(
                "", "end", values=(job.title, job.company, job.location, job.work_type)
            )

        self.page_label_var.set(f"Page {self.current_page + 1} of {total_pages}")
        self.prev_btn.config(state="normal" if self.current_page > 0 else "disabled")
        self.next_btn.config(
            state="normal" if self.current_page < total_pages - 1 else "disabled"
        )

    def _go_prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._render_page()

    def _go_next_page(self):
        if self.current_page < self._total_pages() - 1:
            self.current_page += 1
            self._render_page()

    def _on_row_open(self, _event):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        title = values[0]
        match = next((j for j in self.all_jobs if j.title == title), None)
        if match and match.url:
            webbrowser.open(match.url)


if __name__ == "__main__":
    app = JobScraperApp()
    app.mainloop()
