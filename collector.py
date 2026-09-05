"""
collector.py — Fetches current problem-statement data from the SIH portal.

CONFIRMED STRUCTURE (verified against real debug_page.html + screenshot):
  - Table lives at #dataTablePS, driven by jQuery DataTables.
  - Real column order (0-indexed):
      0 S.No.
      1 Organization
      2 Problem Statement Title  <-- contains a NESTED MODAL <div> with the
                                      full PS description inside the SAME <td>
                                      as the title <a> tag. Must extract the
                                      title from the <a> tag only, never
                                      td.get_text() on the whole cell.
      3 Category
      4 PS Number   (e.g. "SIH26001")
      5 Submitted Idea(s) Count -> format "7/500" (submitted / cap), not a
                                      plain integer.
      6 Theme
      7 Deadline for Idea Submission

  - CRITICAL GOTCHA: DataTables initializes with "Show 10 entries" by
    default and physically REMOVES the other pages' rows from the live
    DOM (confirmed empirically: a captured page.content() after full JS
    init contained only 10 <tr role="row"> data rows, even though
    pagination showed up to page 24 -- i.e. ~233 total PS exist but
    aren't in the DOM at once). So: page.content() right after page load
    only gives you page 1. There is no plain "all rows are already in the
    HTML" shortcut here -- that was a wrong assumption from an earlier
    debugging pass.

  - THE FIX: call the DataTables JS API directly to force show-all, since
    the visible <select> dropdown only offers 10/25/50/100 (no "All"
    option), but the underlying DataTables API always accepts -1 for
    "show all rows", regardless of what's in the dropdown:

        $('#dataTablePS').DataTable().page.len(-1).draw();

    Do this via page.evaluate() after the table has initialized, then
    wait for the row count to stop changing, THEN capture page.content().
    One page load, one JS call, done -- no clicking "Next" 24 times.

  - Also confirmed: cells[td] must be fetched with recursive=False. A
    plain tr.find_all("td") is recursive by default and will also match
    <td> elements from the modal's own nested description table (which
    lives inside the Title cell), silently shifting every column after
    Title. Must restrict to direct children of the row.

SETUP (one-time):
    pip install -r requirements.txt
    playwright install chromium
"""

import re
import sys

from bs4 import BeautifulSoup

SIH_URL = "https://www.sih.gov.in/sih2026PS"
PAGE_LOAD_TIMEOUT_MS = 60000
TABLE_SELECTOR = "#dataTablePS"
ROW_SELECTOR = f"{TABLE_SELECTOR} tbody tr[role='row']"


def fetch_problem_statements(debug=False):
    """
    Returns a list of dicts:
    [{"ps_id": "SIH26001", "title": "...", "organization": "...",
      "category": "Software", "theme": "...", "count": 7,
      "submission_cap": 500}, ...]
    """
    return _fetch_with_playwright(debug=debug)


def _fetch_with_playwright(debug=False):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not debug)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print(f"[collector] Loading {SIH_URL} ...")
        page.goto(SIH_URL, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="load")

        # Wait for DataTables to finish its initial render (default: page 1,
        # 10 rows) before we try to touch its API.
        page.wait_for_selector(ROW_SELECTOR, timeout=PAGE_LOAD_TIMEOUT_MS)

        # Force "show all" via the DataTables API directly -- the dropdown
        # UI only exposes 10/25/50/100, but the API accepts -1 regardless.
        print("[collector] Forcing DataTables to show all rows...")
        page.evaluate(
            f"() => {{ $('{TABLE_SELECTOR}').DataTable().page.len(-1).draw(); }}"
        )

        # Wait for the row count to stabilize after the redraw, instead of a
        # blind sleep -- more reliable across slow connections.
        prev_count = -1
        stable_checks = 0
        for _ in range(40):  # up to ~8s of polling (40 * 200ms)
            page.wait_for_timeout(200)
            current_count = page.eval_on_selector_all(ROW_SELECTOR, "els => els.length")
            if current_count == prev_count:
                stable_checks += 1
                if stable_checks >= 3:
                    break
            else:
                stable_checks = 0
            prev_count = current_count

        row_count = page.eval_on_selector_all(ROW_SELECTOR, "els => els.length")
        print(f"[collector] DataTables now showing {row_count} rows after show-all.")

        html = page.content()

        if debug:
            page.screenshot(path="debug_screenshot.png", full_page=True)
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("[collector] Saved debug_screenshot.png and debug_page.html")

        browser.close()

    results = parse_ps_table(html)
    print(f"[collector] Total PS collected: {len(results)}")

    if row_count and len(results) != row_count:
        print(
            f"[collector] WARNING: DOM reported {row_count} rows but parser "
            f"extracted {len(results)}. Some rows may have been skipped by "
            f"the < 8 cells guard -- worth checking."
        )

    return results


def _parse_submission_count(raw):
    """Parse '7/500' -> (7, 500). Falls back to (0, 0) on unexpected format."""
    raw = (raw or "").strip()
    m = re.match(r"(\d+)\s*/\s*(\d+)", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Fallback: maybe it really is a plain integer some day
    if raw.isdigit():
        return int(raw), 0
    return 0, 0


def parse_ps_table(html):
    """Parse the #dataTablePS HTML into a list of PS dicts. Pure function --
    no browser dependency -- so it can be unit tested against saved HTML."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="dataTablePS")
    if table is None:
        raise ValueError("table#dataTablePS not found in page HTML")

    tbody = table.find("tbody")
    rows = tbody.find_all("tr", recursive=False) if tbody else []

    results = []
    for tr in rows:
        # recursive=False is mandatory here -- see module docstring gotcha.
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 8:
            continue  # skip DataTables' "no data" placeholder row etc.

        title_link = cells[2].find("a")
        title = title_link.get_text(strip=True) if title_link else cells[2].get_text(strip=True)

        s_no = cells[0].get_text(strip=True)
        organization = cells[1].get_text(strip=True)
        category = cells[3].get_text(strip=True)
        ps_id = cells[4].get_text(strip=True)
        submitted, cap = _parse_submission_count(cells[5].get_text(strip=True))
        theme = cells[6].get_text(strip=True)
        deadline = cells[7].get_text(strip=True)

        if not ps_id.upper().startswith("SIH"):
            continue  # not a real data row

        results.append({
            "s_no": s_no,
            "ps_id": ps_id,
            "title": title,
            "organization": organization,
            "category": category,
            "count": submitted,
            "submission_cap": cap,
            "theme": theme,
            "deadline": deadline,
        })

    return results


def _demo_fetch():
    """Fake data generator so you can test DB + notifications + analytics today,
    without waiting on the real scraper. Simulates counts slowly increasing."""
    import random

    if not hasattr(_demo_fetch, "_state"):
        _demo_fetch._state = {
            "SIH26001": {"title": "AI-Based early warning and landslide Risk Monitoring System in NER", "count": 7},
            "SIH26002": {"title": "AI-Based Smart Logistics and Accessibility Intelligence Platform", "count": 3},
            "SIH26007": {"title": "Safe and Efficient Operation of Mine Vehicles in Fog", "count": 1},
            "SIH26010": {"title": "Survey/Resurvey of Rural Agricultural Land in India", "count": 1},
        }

    results = []
    for ps_id, info in _demo_fetch._state.items():
        if random.random() < 0.3:
            info["count"] += random.randint(1, 3)
        results.append({
            "ps_id": ps_id,
            "title": info["title"],
            "organization": "Demo Ministry",
            "category": "Software",
            "theme": "Smart Automation",
            "count": info["count"],
            "submission_cap": 500,
        })
    return results


if __name__ == "__main__":
    debug_mode = "--debug" in sys.argv
    demo_mode = "--demo" in sys.argv
    test_file = None
    for arg in sys.argv[1:]:
        if arg.endswith(".html"):
            test_file = arg

    if test_file:
        # Offline test mode: parse a saved HTML file directly, no browser.
        with open(test_file, "r", encoding="utf-8") as f:
            html = f.read()
        results = parse_ps_table(html)
    elif demo_mode:
        results = _demo_fetch()
    else:
        results = fetch_problem_statements(debug=debug_mode)

    print(f"\n--- {len(results)} problem statements ---")
    for ps in results[:10]:
        print(ps)
    if len(results) > 10:
        print(f"... and {len(results) - 10} more")
