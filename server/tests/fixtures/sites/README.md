# Fixture sites

Local landing pages used by the capture and check tests (PRD §14). They are served by
`tests/fixture_server.py` under the hostname `fixtures.test`, which the test resolver maps to
127.0.0.1 and the test SSRF policy allowlists. Tracking scripts point at the real Meta and
Google URLs; in tests those requests are answered by `tagmonitor.browser.tracking_stubs`, so
nothing leaves the machine.

Scripted behaviors (see the server): `slow_lcp/hero.svg` is delayed ~5 s, `http_500` returns
status 500, `redirect_chain_3` redirects three times, and `redirect_to_private_ip` redirects to
127.0.0.1, which the SSRF guard must refuse.
