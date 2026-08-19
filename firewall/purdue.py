"""Purdue campus emergency status, for the West Lafayette build.

Purdue publishes campus status at https://www.purdue.edu/emergency/ ("Campus
Safety Status"). It is a WordPress site, so there are three ways in, and only
one of them works:

  /feed/                      dead. Two years stale, and the single item in it
                              is the ITaP boilerplate "Getting Started" post.
                              Verified 2026-08-19.
  /wp-json/wp/v2/posts        the same one boilerplate post. Alerts are not
                              posts; they are edits to the front page.
  /wp-json/wp/v2/pages?slug=home    the front page as JSON, with modified_gmt.
                              This is the live status, so this is what we read.

The status itself is prose in <h2> headings, one per campus:

    <h1>Campus Emergency Status</h1>
    <h2>West Lafayette location under normal operations</h2>
    <h2>Indianapolis location under normal operations</h2>
    <h3>Timely warning messages at Purdue</h3>     <- first section, stop here

So: cut the content at the first <h3>, read the <h2>s above it, and treat
anything that does not say "normal operations" as an alert. Deliberately
inverted -- unrecognised wording escalates rather than reads as normal --
because the failure that matters is a live emergency rendering as "all clear".

No requests dependency: this is one GET, and mock mode still installs nothing.
"""
import html as _html
import json
import re
import sys
import time
import urllib.request

from . import core

PAGE_URL = "https://www.purdue.edu/emergency/"
API_URL = PAGE_URL + "wp-json/wp/v2/pages?slug=home"
UA = "firewall/0.1 (ambient dispatch display; polls every few minutes)"

# Other Purdue campuses share this page. Two hours away is not your problem.
OTHER_CAMPUS = re.compile(r"indianapolis|fort wayne|northwest|\bpfw\b|\bpnw\b", re.I)
NORMAL = re.compile(r"normal operations", re.I)


def _text(fragment):
    """Markup fragment to one line of plain text."""
    stripped = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", _html.unescape(stripped)).strip()


def parse(rendered):
    """Rendered page content to {status, headline, detail}.

    status is 'normal', 'alert', or 'unknown' when the page no longer looks
    like the page this parser was written against.
    """
    head = re.split(r"<h3\b", rendered or "", 1)[0]
    lines = []
    for m in re.finditer(r"<h2[^>]*>(.*?)</h2>\s*(?:<p[^>]*>(.*?)</p>)?", head, re.I | re.S):
        headline, detail = _text(m.group(1)), _text(m.group(2))
        if headline:
            lines.append((headline, detail))

    local = [ln for ln in lines if not OTHER_CAMPUS.search(ln[0])] or lines
    if not local:
        return {"status": "unknown",
                "headline": "Campus status unreadable",
                "detail": "purdue.edu/emergency did not contain a status line.",
                "url": PAGE_URL}

    alerting = [ln for ln in local if not NORMAL.search(ln[0])]
    headline, detail = alerting[0] if alerting else local[0]
    return {"status": "alert" if alerting else "normal",
            "headline": headline,
            "detail": detail,
            "url": PAGE_URL}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch(timeout=20):
    """Current campus status. Raises on a total failure to reach the page."""
    try:
        body = json.loads(_get(API_URL, timeout))
        page = body[0] if isinstance(body, list) and body else {}
        info = parse((page.get("content") or {}).get("rendered") or "")
        info["modified"] = page.get("modified_gmt")
        if info["status"] != "unknown":
            return info
    except Exception as e:                      # noqa: BLE001 - fall through
        print(f"[purdue] wp-json unavailable ({type(e).__name__}: {e}), "
              f"reading the page itself", file=sys.stderr)

    # Same headings, same cut-at-the-first-h3 rule, so the same parser works on
    # the served HTML. Kept as a fallback because the status is worth one retry
    # through a different door before the screen goes quiet about it.
    info = parse(_get(PAGE_URL, timeout))
    info["modified"] = None
    return info


def poll(cfg):
    """Refresh campus status forever. Runs alongside the dispatch source."""
    interval = max(30, int(cfg.get("purdue_poll_seconds", 120)))
    print(f"[purdue] campus status from {PAGE_URL} every {interval}s")
    while True:
        try:
            info = fetch()
            info["checked"] = time.time()
            core.set_purdue(info)
            if info["status"] != "normal":
                print(f"[purdue] {info['status']}: {info['headline']}")
        except Exception as e:
            # Purdue being unreachable is not the dispatch source failing, so
            # this never touches core health -- it would repaint the whole
            # screen as "dispatch source down", which would be a lie. Keep the
            # last good reading and let it age; the display greys it out.
            print(f"[purdue] fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
            last = core.snapshot().get("purdue")
            core.set_purdue({**(last or {"status": "unknown",
                                         "headline": "Campus status unavailable",
                                         "detail": "", "url": PAGE_URL,
                                         "checked": None}),
                             "error": f"{type(e).__name__}: {e}"[:160]})
        time.sleep(interval)
