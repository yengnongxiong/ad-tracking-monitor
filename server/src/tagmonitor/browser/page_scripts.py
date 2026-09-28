"""JavaScript that runs inside the captured page."""

# Installed with add_init_script, so it runs before any page script. `buffered: true` also
# delivers entries that happened before the observer existed.
PERFORMANCE_OBSERVER = """
(() => {
  window.__tagmonitor = { lcp: null, layoutShift: 0 };
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__tagmonitor.lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) window.__tagmonitor.layoutShift += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  } catch (error) {
    // Very old engines lack these entry types; the metrics then stay null.
  }
})();
"""

READ_PERFORMANCE = """
() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const metrics = window.__tagmonitor || {};
  return {
    lcp_ms: metrics.lcp ?? null,
    layout_shift: metrics.layoutShift ?? null,
    load_ms: nav && nav.loadEventEnd > 0 ? nav.loadEventEnd : null,
  };
}
"""

# What a visitor sees and reads: used by the mobile rendering check and the LLM message match.
DOM_FACTS = """
() => {
  const clean = (text) => (text || "").replace(/\\s+/g, " ").trim();
  const meta = (name) => {
    const el = document.querySelector(`meta[name="${name}" i]`);
    return el ? el.getAttribute("content") : null;
  };
  const isVisible = (el) => {
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const inFirstViewport = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  };

  // Visible text in the first viewport, in document order, capped at 1,500 characters.
  const skip = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const chunks = [];
  let length = 0;
  let visited = 0;
  if (document.body) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && length < 1500 && visited < 5000) {
      visited += 1;
      const text = clean(walker.currentNode.textContent);
      const el = walker.currentNode.parentElement;
      if (!text || !el || skip.has(el.tagName) || !isVisible(el) || !inFirstViewport(el)) continue;
      chunks.push(text);
      length += text.length + 1;
    }
  }

  const buttons = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(
    "button, a[href], input[type=submit], input[type=button], [role=button]"
  )) {
    const text = clean(el.innerText || el.value || el.getAttribute("aria-label"));
    if (!text || text.length > 100 || seen.has(text) || !isVisible(el)) continue;
    seen.add(text);
    buttons.push(text);
    if (buttons.length === 30) break;
  }

  const headings = (selector, limit) =>
    [...document.querySelectorAll(selector)].map((el) => clean(el.innerText)).filter(Boolean)
      .slice(0, limit);

  return {
    title: clean(document.title) || null,
    meta_description: meta("description"),
    viewport_meta: meta("viewport"),
    h1: headings("h1", 10),
    h2: headings("h2", 20),
    above_fold_text: chunks.join(" ").slice(0, 1500),
    button_texts: buttons,
    scroll_width: document.documentElement.scrollWidth,
    inner_width: window.innerWidth,
  };
}
"""
