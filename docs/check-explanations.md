# Check explanations

Generated from `server/src/tagmonitor/checks/explanations.py`; edit that file and run
`python -m tagmonitor.checks.explanations > docs/check-explanations.md`.

## Meta Pixel (`meta_pixel`)

### `firing`

- **What we saw:** Your Meta Pixel loaded and sent a PageView when we visited the page.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Nothing to do.

### `duplicate_pageview`

- **What we saw:** Your Meta Pixel sends PageView more than once per visit.
- **Why it matters:** Every visit is counted twice, so audiences and reports are inflated, and cost per result looks better (or worse) than it is.
- **How to fix it:** The pixel is usually installed twice, e.g. once by your theme or a plugin and once by hand or through Tag Manager. Remove one copy. Meta's Pixel Helper browser extension shows every copy it finds.

### `installed_not_firing`

- **What we saw:** The Meta Pixel code loads on the page, but it never sends a PageView.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Look for a pixel snippet that sets up the pixel (fbq('init', ...)) but is missing fbq('track', 'PageView'), a JavaScript error that stops the code before it runs, or a cookie-consent tool that blocks the pixel until visitors click accept.

### `script_blocked`

- **What we saw:** The Meta Pixel script (fbevents.js) failed to download.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Check whether a security setting on your site (a Content-Security-Policy, firewall or security plugin) blocks connect.facebook.net, and allow it.

### `wrong_pixel`

- **What we saw:** The pixel ID you told us to expect isn't on the page. A different pixel, or none, is.
- **Why it matters:** Events go to a different ad account's pixel (often an old one or an agency's), so your campaigns don't see them.
- **How to fix it:** In Meta Events Manager, copy your pixel ID and make sure it's the one in your site's pixel code or Tag Manager. If you changed pixels on purpose, update the expected ID in tag-monitor.

### `not_installed`

- **What we saw:** There's no Meta Pixel on this page.
- **Why it matters:** If you run Meta ads to this page, they can't measure or optimize for results.
- **How to fix it:** Install the pixel from Meta Events Manager (or through Tag Manager), or ignore this if you don't advertise on Meta.

## Google Analytics 4 (`google_ga4`)

### `firing`

- **What we saw:** Google Analytics 4 received a page_view when we visited the page.
- **Why it matters:** GA4 is how you see which ads and pages turn into customers.
- **How to fix it:** Nothing to do.

### `duplicate_pageview`

- **What we saw:** GA4 records page_view more than once per visit.
- **Why it matters:** Pageviews double, bounce rate drops to near zero, and conversion rates look lower than they really are.
- **How to fix it:** GA4 is usually installed twice, e.g. a gtag.js snippet and a Tag Manager tag for the same measurement ID. Keep one of them.

### `installed_not_firing`

- **What we saw:** The GA4 tag loads, but it never sends a page_view.
- **Why it matters:** Visits to this page are missing from Google Analytics and from any Google Ads conversions imported from GA4.
- **How to fix it:** Check that gtag('config', 'G-...') runs after the gtag.js script, that no JavaScript error stops it, and that a consent tool isn't blocking it for every visitor.

### `script_blocked`

- **What we saw:** The GA4 script (gtag.js) failed to download.
- **Why it matters:** No analytics data is collected from this page.
- **How to fix it:** Check that nothing on your site blocks googletagmanager.com (Content-Security-Policy, firewall or security plugin).

### `wrong_id`

- **What we saw:** The GA4 measurement ID you told us to expect isn't on the page.
- **Why it matters:** Data goes to a different GA4 property, often an old one, so your reports look empty.
- **How to fix it:** In GA4 go to Admin > Data streams, copy the measurement ID (G-...) and make sure it's the one on your site. If you switched properties on purpose, update the expected ID.

### `not_installed`

- **What we saw:** We didn't see Google Analytics 4 on this page.
- **Why it matters:** Without analytics you can't tell which ads bring customers.
- **How to fix it:** Add GA4 with gtag.js or Tag Manager, or ignore this if you use other analytics. (GA4 sent through a server-side container on your own domain isn't visible to us.)

## Google Ads (`google_ads`)

### `firing`

- **What we saw:** Your Google Ads tag sent a hit when we visited the page.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Nothing to do.

### `installed_not_firing`

- **What we saw:** The Google Ads tag (AW-...) loads, but it never sends a hit.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Make sure gtag('config', 'AW-...') runs on the page, and check for JavaScript errors or a consent tool that blocks it for every visitor.

### `script_blocked`

- **What we saw:** The Google Ads tag script failed to download.
- **Why it matters:** Ad platforms learn who converts from these signals. Without them your conversions go unreported and the platform's automatic bidding optimizes for the wrong people, while you keep paying for clicks.
- **How to fix it:** Check that nothing on your site blocks googletagmanager.com.

### `wrong_id`

- **What we saw:** The Google Ads conversion ID you told us to expect isn't on the page.
- **Why it matters:** Conversions are reported to a different Google Ads account, or nowhere.
- **How to fix it:** In Google Ads go to Goals > Conversions > Tag setup and compare the AW- ID with the one on your site.

### `not_installed`

- **What we saw:** We didn't see a Google Ads tag on this page.
- **Why it matters:** If you run Google Ads to this page, conversions and remarketing can't work.
- **How to fix it:** Add the Google Ads tag with gtag.js or Tag Manager, or ignore this if you don't run Google Ads.

## Google Tag Manager (`google_gtm`)

### `loaded`

- **What we saw:** Your Google Tag Manager container loaded.
- **Why it matters:** Tag Manager is often what loads all your other tags.
- **How to fix it:** Nothing to do.

### `script_blocked`

- **What we saw:** Your Google Tag Manager container (gtm.js) failed to load.
- **Why it matters:** Every tag inside the container (analytics, ads, pixels) stops working at once.
- **How to fix it:** Check that the container ID in your site's code is correct and published, and that nothing blocks googletagmanager.com.

### `not_installed`

- **What we saw:** There's no Google Tag Manager container on this page.
- **Why it matters:** That's fine if you install tags directly.
- **How to fix it:** Nothing to do unless you expected Tag Manager here.

## Mobile speed (`page_speed`)

### `fast`

- **What we saw:** The main content appeared within 2.5 seconds on a phone (Google's "good" threshold for Largest Contentful Paint).
- **Why it matters:** Fast pages keep the visitors you paid for.
- **How to fix it:** Nothing to do.

### `needs_improvement`

- **What we saw:** The main content took between 2.5 and 4 seconds to appear on a phone.
- **Why it matters:** Every extra second on mobile loses visitors before they see your offer.
- **How to fix it:** Compress and resize the large image at the top of the page, serve it in a modern format (WebP/AVIF), and remove scripts the page doesn't need. Google PageSpeed Insights lists the biggest wins for your page.

### `slow`

- **What we saw:** The main content took more than 4 seconds to appear on a phone (Google's "poor" threshold).
- **Why it matters:** Many visitors leave before the page appears, so you pay for clicks that never see your offer.
- **How to fix it:** Start with the hero image and any sliders or videos above the fold, then heavy third-party scripts (chat widgets, extra trackers). Google PageSpeed Insights shows what delays your largest element.

### `no_lcp`

- **What we saw:** The browser didn't report when the main content appeared, so we couldn't time it.
- **Why it matters:** It happens on pages that show almost nothing, or only an embedded frame.
- **How to fix it:** If the page looks blank on a phone, look at why; otherwise nothing to do.

## Mobile layout (`mobile_render`)

### `ok`

- **What we saw:** The page is set up for phones and fits the screen width.
- **Why it matters:** Most ad clicks come from phones.
- **How to fix it:** Nothing to do.

### `missing_viewport`

- **What we saw:** The page has no mobile viewport tag.
- **Why it matters:** Phones render it as a shrunken desktop page with tiny text, and most visitors leave.
- **How to fix it:** Add <meta name="viewport" content="width=device-width, initial-scale=1"> inside <head>. Most website builders have a mobile-friendly setting that adds it.

### `horizontal_overflow`

- **What we saw:** Something on the page is wider than a phone screen, so the page scrolls sideways.
- **Why it matters:** The layout looks broken and buttons can end up off-screen.
- **How to fix it:** Look for fixed-width banners, tables, images or embeds, and give them max-width: 100%.

## Page health (`page_health`)

### `ok`

- **What we saw:** The page loaded over HTTPS, without errors or unusual redirects.
- **Why it matters:** It's the foundation for everything else.
- **How to fix it:** Nothing to do.

### `navigation_failed`

- **What we saw:** The page didn't load at all.
- **Why it matters:** Every ad click lands on an error, so you pay for visits that go nowhere.
- **How to fix it:** Open the page yourself. If it's down, contact your host or developer. If only we can't reach it, check that the domain hasn't expired and that the address in tag-monitor is correct.

### `http_error`

- **What we saw:** The server answered with an error page (HTTP 4xx or 5xx) instead of your page.
- **Why it matters:** Visitors from your ads see an error instead of your offer.
- **How to fix it:** A 404 means the address changed: update your ads or add a redirect. A 5xx is a server problem: contact your host or developer.

### `not_https`

- **What we saw:** The page isn't served over HTTPS.
- **Why it matters:** Browsers label it "Not secure", which scares visitors off, and some tags refuse to run.
- **How to fix it:** Turn on HTTPS (most hosts offer free certificates) and redirect http:// to https://.

### `cross_domain_redirect`

- **What we saw:** Visitors are sent to a different website than the one you entered.
- **Why it matters:** It can be intentional, but it's also what an expired domain or a hijacked site looks like, and your tags may not be on the other site.
- **How to fix it:** Make sure the redirect is intentional, and that your tracking is on the final site.

### `too_many_redirects`

- **What we saw:** The page goes through more than two redirects before it loads.
- **Why it matters:** Each hop adds delay on mobile, and tracking parameters from your ads can get lost.
- **How to fix it:** Point your ads straight at the final address.

### `js_errors`

- **What we saw:** The page reports JavaScript errors.
- **Why it matters:** Errors can stop tags, forms or checkout buttons from working.
- **How to fix it:** Send the errors listed in the details to your developer.

## Any check

### `not_evaluated`

- **What we saw:** We couldn't run this check because the page itself didn't load.
- **Why it matters:** Everything on the page is unavailable while it's down, so we report the outage once (under Page health) instead of flagging every check.
- **How to fix it:** Fix the Page health problem first; this check will run again on the next visit.

### `check_crashed`

- **What we saw:** This check hit an internal error on our side.
- **Why it matters:** It says nothing about your page. We've logged it and will look into it.
- **How to fix it:** Nothing to do on your side.
