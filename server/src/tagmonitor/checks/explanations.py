"""Plain-English explanations for every check outcome, shared by the dashboard and emails.

This module is the single source. docs/check-explanations.md is generated from it
(`python -m tagmonitor.checks.explanations > docs/check-explanations.md`), and a test fails if
the two drift apart or if any check can return a code that has no explanation.
"""

from dataclasses import dataclass

from tagmonitor.checks.registry import ALL_CHECKS


@dataclass(frozen=True)
class Explanation:
    meaning: str  # what we saw
    why_it_matters: str
    how_to_fix: str


_ADS_PAGE_VIEW = (
    "Ad platforms learn who converts from these signals. Without them your conversions go "
    "unreported and the platform's automatic bidding optimizes for the wrong people, while "
    "you keep paying for clicks."
)

_MESSAGE_MATCH = (
    "People click an ad expecting what it promised. If the page doesn't show it right away, "
    "they leave, and you've paid for the click. Google Ads also rates each ad's landing page "
    "experience, which feeds into what you pay per click."
)

# Keyed by (check_key, code). "*" entries apply to every check.
EXPLANATIONS: dict[tuple[str, str], Explanation] = {
    ("*", "not_evaluated"): Explanation(
        "We couldn't run this check because the page itself didn't load.",
        "Everything on the page is unavailable while it's down, so we report the outage once "
        "(under Page health) instead of flagging every check.",
        "Fix the Page health problem first; this check will run again on the next visit.",
    ),
    ("*", "check_crashed"): Explanation(
        "This check hit an internal error on our side.",
        "It says nothing about your page. We've logged it and will look into it.",
        "Nothing to do on your side.",
    ),
    # -- Meta Pixel -------------------------------------------------------------------------
    ("meta_pixel", "firing"): Explanation(
        "Your Meta Pixel loaded and sent a PageView when we visited the page.",
        _ADS_PAGE_VIEW,
        "Nothing to do.",
    ),
    ("meta_pixel", "duplicate_pageview"): Explanation(
        "Your Meta Pixel sends PageView more than once per visit.",
        "Every visit is counted twice, so audiences and reports are inflated, and cost per "
        "result looks better (or worse) than it is.",
        "The pixel is usually installed twice, e.g. once by your theme or a plugin and once "
        "by hand or through Tag Manager. Remove one copy. Meta's Pixel Helper browser "
        "extension shows every copy it finds.",
    ),
    ("meta_pixel", "installed_not_firing"): Explanation(
        "The Meta Pixel code loads on the page, but it never sends a PageView.",
        _ADS_PAGE_VIEW,
        "Look for a pixel snippet that sets up the pixel (fbq('init', ...)) but is missing "
        "fbq('track', 'PageView'), a JavaScript error that stops the code before it runs, or "
        "a cookie-consent tool that blocks the pixel until visitors click accept.",
    ),
    ("meta_pixel", "script_blocked"): Explanation(
        "The Meta Pixel script (fbevents.js) failed to download.",
        _ADS_PAGE_VIEW,
        "Check whether a security setting on your site (a Content-Security-Policy, firewall "
        "or security plugin) blocks connect.facebook.net, and allow it.",
    ),
    ("meta_pixel", "wrong_pixel"): Explanation(
        "The pixel ID you told us to expect isn't on the page. A different pixel, or none, is.",
        "Events go to a different ad account's pixel (often an old one or an agency's), so "
        "your campaigns don't see them.",
        "In Meta Events Manager, copy your pixel ID and make sure it's the one in your site's "
        "pixel code or Tag Manager. If you changed pixels on purpose, update the expected ID "
        "in tag-monitor.",
    ),
    ("meta_pixel", "not_installed"): Explanation(
        "There's no Meta Pixel on this page.",
        "If you run Meta ads to this page, they can't measure or optimize for results.",
        "Install the pixel from Meta Events Manager (or through Tag Manager), or ignore this "
        "if you don't advertise on Meta.",
    ),
    # -- GA4 -----------------------------------------------------------------------------------
    ("google_ga4", "firing"): Explanation(
        "Google Analytics 4 received a page_view when we visited the page.",
        "GA4 is how you see which ads and pages turn into customers.",
        "Nothing to do.",
    ),
    ("google_ga4", "duplicate_pageview"): Explanation(
        "GA4 records page_view more than once per visit.",
        "Pageviews double, bounce rate drops to near zero, and conversion rates look lower "
        "than they really are.",
        "GA4 is usually installed twice, e.g. a gtag.js snippet and a Tag Manager tag for the "
        "same measurement ID. Keep one of them.",
    ),
    ("google_ga4", "installed_not_firing"): Explanation(
        "The GA4 tag loads, but it never sends a page_view.",
        "Visits to this page are missing from Google Analytics and from any Google Ads "
        "conversions imported from GA4.",
        "Check that gtag('config', 'G-...') runs after the gtag.js script, that no JavaScript "
        "error stops it, and that a consent tool isn't blocking it for every visitor.",
    ),
    ("google_ga4", "script_blocked"): Explanation(
        "The GA4 script (gtag.js) failed to download.",
        "No analytics data is collected from this page.",
        "Check that nothing on your site blocks googletagmanager.com (Content-Security-Policy, "
        "firewall or security plugin).",
    ),
    ("google_ga4", "wrong_id"): Explanation(
        "The GA4 measurement ID you told us to expect isn't on the page.",
        "Data goes to a different GA4 property, often an old one, so your reports look empty.",
        "In GA4 go to Admin > Data streams, copy the measurement ID (G-...) and make sure it's "
        "the one on your site. If you switched properties on purpose, update the expected ID.",
    ),
    ("google_ga4", "not_installed"): Explanation(
        "We didn't see Google Analytics 4 on this page.",
        "Without analytics you can't tell which ads bring customers.",
        "Add GA4 with gtag.js or Tag Manager, or ignore this if you use other analytics. "
        "(GA4 sent through a server-side container on your own domain isn't visible to us.)",
    ),
    # -- Google Ads ------------------------------------------------------------------------------
    ("google_ads", "firing"): Explanation(
        "Your Google Ads tag sent a hit when we visited the page.",
        _ADS_PAGE_VIEW,
        "Nothing to do.",
    ),
    ("google_ads", "installed_not_firing"): Explanation(
        "The Google Ads tag (AW-...) loads, but it never sends a hit.",
        _ADS_PAGE_VIEW,
        "Make sure gtag('config', 'AW-...') runs on the page, and check for JavaScript errors "
        "or a consent tool that blocks it for every visitor.",
    ),
    ("google_ads", "script_blocked"): Explanation(
        "The Google Ads tag script failed to download.",
        _ADS_PAGE_VIEW,
        "Check that nothing on your site blocks googletagmanager.com.",
    ),
    ("google_ads", "wrong_id"): Explanation(
        "The Google Ads conversion ID you told us to expect isn't on the page.",
        "Conversions are reported to a different Google Ads account, or nowhere.",
        "In Google Ads go to Goals > Conversions > Tag setup and compare the AW- ID with the "
        "one on your site.",
    ),
    ("google_ads", "not_installed"): Explanation(
        "We didn't see a Google Ads tag on this page.",
        "If you run Google Ads to this page, conversions and remarketing can't work.",
        "Add the Google Ads tag with gtag.js or Tag Manager, or ignore this if you don't run "
        "Google Ads.",
    ),
    # -- Google Tag Manager ----------------------------------------------------------------------
    ("google_gtm", "loaded"): Explanation(
        "Your Google Tag Manager container loaded.",
        "Tag Manager is often what loads all your other tags.",
        "Nothing to do.",
    ),
    ("google_gtm", "script_blocked"): Explanation(
        "Your Google Tag Manager container (gtm.js) failed to load.",
        "Every tag inside the container (analytics, ads, pixels) stops working at once.",
        "Check that the container ID in your site's code is correct and published, and that "
        "nothing blocks googletagmanager.com.",
    ),
    ("google_gtm", "not_installed"): Explanation(
        "There's no Google Tag Manager container on this page.",
        "That's fine if you install tags directly.",
        "Nothing to do unless you expected Tag Manager here.",
    ),
    # -- Speed -----------------------------------------------------------------------------------
    ("page_speed", "fast"): Explanation(
        'The main content appeared within 2.5 seconds on a phone (Google\'s "good" threshold '
        "for Largest Contentful Paint).",
        "Fast pages keep the visitors you paid for.",
        "Nothing to do.",
    ),
    ("page_speed", "needs_improvement"): Explanation(
        "The main content took between 2.5 and 4 seconds to appear on a phone.",
        "Every extra second on mobile loses visitors before they see your offer.",
        "Compress and resize the large image at the top of the page, serve it in a modern "
        "format (WebP/AVIF), and remove scripts the page doesn't need. Google PageSpeed "
        "Insights lists the biggest wins for your page.",
    ),
    ("page_speed", "slow"): Explanation(
        'The main content took more than 4 seconds to appear on a phone (Google\'s "poor" '
        "threshold).",
        "Many visitors leave before the page appears, so you pay for clicks that never see "
        "your offer.",
        "Start with the hero image and any sliders or videos above the fold, then heavy "
        "third-party scripts (chat widgets, extra trackers). Google PageSpeed Insights "
        "shows what delays your largest element.",
    ),
    ("page_speed", "no_lcp"): Explanation(
        "The browser didn't report when the main content appeared, so we couldn't time it.",
        "It happens on pages that show almost nothing, or only an embedded frame.",
        "If the page looks blank on a phone, look at why; otherwise nothing to do.",
    ),
    # -- Mobile layout ---------------------------------------------------------------------------
    ("mobile_render", "ok"): Explanation(
        "The page is set up for phones and fits the screen width.",
        "Most ad clicks come from phones.",
        "Nothing to do.",
    ),
    ("mobile_render", "missing_viewport"): Explanation(
        "The page has no mobile viewport tag.",
        "Phones render it as a shrunken desktop page with tiny text, and most visitors leave.",
        'Add <meta name="viewport" content="width=device-width, initial-scale=1"> inside '
        "<head>. Most website builders have a mobile-friendly setting that adds it.",
    ),
    ("mobile_render", "horizontal_overflow"): Explanation(
        "Something on the page is wider than a phone screen, so the page scrolls sideways.",
        "The layout looks broken and buttons can end up off-screen.",
        "Look for fixed-width banners, tables, images or embeds, and give them max-width: 100%.",
    ),
    # -- Page health -----------------------------------------------------------------------------
    ("page_health", "ok"): Explanation(
        "The page loaded over HTTPS, without errors or unusual redirects.",
        "It's the foundation for everything else.",
        "Nothing to do.",
    ),
    ("page_health", "navigation_failed"): Explanation(
        "The page didn't load at all.",
        "Every ad click lands on an error, so you pay for visits that go nowhere.",
        "Open the page yourself. If it's down, contact your host or developer. If only we "
        "can't reach it, check that the domain hasn't expired and that the address in "
        "tag-monitor is correct.",
    ),
    ("page_health", "http_error"): Explanation(
        "The server answered with an error page (HTTP 4xx or 5xx) instead of your page.",
        "Visitors from your ads see an error instead of your offer.",
        "A 404 means the address changed: update your ads or add a redirect. A 5xx is a "
        "server problem: contact your host or developer.",
    ),
    ("page_health", "not_https"): Explanation(
        "The page isn't served over HTTPS.",
        'Browsers label it "Not secure", which scares visitors off, and some tags refuse to run.',
        "Turn on HTTPS (most hosts offer free certificates) and redirect http:// to https://.",
    ),
    ("page_health", "cross_domain_redirect"): Explanation(
        "Visitors are sent to a different website than the one you entered.",
        "It can be intentional, but it's also what an expired domain or a hijacked site "
        "looks like, and your tags may not be on the other site.",
        "Make sure the redirect is intentional, and that your tracking is on the final site.",
    ),
    ("page_health", "too_many_redirects"): Explanation(
        "The page goes through more than two redirects before it loads.",
        "Each hop adds delay on mobile, and tracking parameters from your ads can get lost.",
        "Point your ads straight at the final address.",
    ),
    ("page_health", "js_errors"): Explanation(
        "The page reports JavaScript errors.",
        "Errors can stop tags, forms or checkout buttons from working.",
        "Send the errors listed in the details to your developer.",
    ),
    # -- Message match ---------------------------------------------------------------------------
    ("message_match", "good_match"): Explanation(
        "The page picks up where your ad left off: the same offer, a headline that echoes "
        "the ad, and the action the ad asked for.",
        _MESSAGE_MATCH,
        "Nothing to do. If you change the ad, update the ad copy in tag-monitor too.",
    ),
    ("message_match", "partial_match"): Explanation(
        "The page is related to your ad, but a visitor has to work to connect them: the offer, "
        "the headline or the main button doesn't quite match what the ad said.",
        _MESSAGE_MATCH,
        "Read the issues and suggestions listed with this check. The usual fixes: repeat the "
        "ad's offer in the page's main headline, and make the ad's call to action the most "
        "visible button.",
    ),
    ("message_match", "poor_match"): Explanation(
        "The page doesn't deliver what your ad promised: a different offer, a generic page, "
        "or a promise (like a price or discount) that isn't there.",
        _MESSAGE_MATCH,
        "Point the ad at a page about exactly what it advertises, or change the page so the "
        "ad's offer is the first thing visitors see. The issues listed with this check say "
        "what's missing.",
    ),
    ("message_match", "llm_not_configured"): Explanation(
        "Message match needs an Anthropic API key, and this server doesn't have one.",
        "Nothing is wrong with your page; this check just can't run here.",
        "Whoever runs this server can set ANTHROPIC_API_KEY to turn it on.",
    ),
    ("message_match", "llm_daily_limit"): Explanation(
        "This server has a daily limit on AI checks, and it was reached, so we skipped this one.",
        "It says nothing about your page. The limit keeps AI costs predictable.",
        "Nothing to do; it runs again on the next check after midnight UTC.",
    ),
    ("message_match", "llm_error"): Explanation(
        "The AI service didn't answer (it may have been busy or down).",
        "It says nothing about your page.",
        "Nothing to do; we'll try again on the next check.",
    ),
    ("message_match", "llm_invalid_output"): Explanation(
        "The AI answered, but twice in a row not in the format we asked for, so we ignored it.",
        "It says nothing about your page.",
        "Nothing to do; we'll try again on the next check.",
    ),
}


def explain(check_key: str, code: str) -> Explanation | None:
    return EXPLANATIONS.get((check_key, code)) or EXPLANATIONS.get(("*", code))


def _markdown_entry(code: str, explanation: Explanation) -> list[str]:
    return [
        f"### `{code}`",
        "",
        f"- **What we saw:** {explanation.meaning}",
        f"- **Why it matters:** {explanation.why_it_matters}",
        f"- **How to fix it:** {explanation.how_to_fix}",
        "",
    ]


def render_markdown() -> str:
    lines = [
        "# Check explanations",
        "",
        "Generated from `server/src/tagmonitor/checks/explanations.py`; edit that file and run",
        "`python -m tagmonitor.checks.explanations > docs/check-explanations.md`.",
        "",
    ]
    for section_key, title in [(c.check_key, f"{c.title} (`{c.check_key}`)") for c in ALL_CHECKS]:
        lines += [f"## {title}", ""]
        for (check_key, code), explanation in EXPLANATIONS.items():
            if check_key == section_key:
                lines += _markdown_entry(code, explanation)
    lines += ["## Any check", ""]
    for (check_key, code), explanation in EXPLANATIONS.items():
        if check_key == "*":
            lines += _markdown_entry(code, explanation)
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_markdown(), end="")
