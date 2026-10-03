// The theme toggle (itda.work's static/js/theme.js policy), the sidebar on narrow screens, the
// table of contents marking the section being read, and the code blocks' copy buttons.
(function () {
  "use strict";

  // --- the table of contents' decisions, pure so tests/js/docs-site-toc.test.mjs can run them ---

  /**
   * The heading whose section is being read: the last one scrolled past the reading line, the
   * last one when the page is scrolled to its end (short closing sections never reach the line),
   * -1 above the first one (the lead before any heading is no entry of the table).
   *
   * @param {number[]} tops headings' offsets from the top of the document, in order
   * @param {{scrollY: number, viewport: number, height: number, line: number}} at
   *   the scroll offset, the viewport's height, the document's height, and the reading line's
   *   distance from the top of the viewport
   * @returns {number}
   */
  function currentIndex(tops, at) {
    if (!tops.length) return -1;
    if (at.scrollY > 0 && at.scrollY + at.viewport >= at.height - 1) return tops.length - 1;
    var found = -1;
    for (var i = 0; i < tops.length; i++) {
      if (tops[i] - at.scrollY > at.line) break;
      found = i;
    }
    return found;
  }

  /**
   * The scroll offset that shows an item of a scrolling box with ``margin`` around it, moving the
   * box as little as possible; the offset unchanged when the item is already in view.
   *
   * @param {{scrollTop: number, height: number}} box
   * @param {{top: number, height: number}} item the item's offset from the top of the box's content
   * @param {number} margin
   * @returns {number}
   */
  function scrollToShow(box, item, margin) {
    if (item.top - margin < box.scrollTop) return Math.max(0, item.top - margin);
    if (item.top + item.height + margin > box.scrollTop + box.height) {
      return Math.min(item.top - margin, item.top + item.height + margin - box.height);
    }
    return box.scrollTop;
  }

  // --- the copy button's decisions (#173), pure so tests/js/docs-site-copy.test.mjs can run them ---

  /**
   * What a code block copies: its text without the newline that ends the block, so a pasted
   * command waits in the terminal for Enter instead of running.
   *
   * @param {string} text the code element's textContent
   * @returns {string}
   */
  function codeText(text) {
    return text.replace(/\n+$/, "");
  }

  /**
   * Put ``text`` on the clipboard: the Clipboard API where the page has one, and where it has
   * none (an http page other than localhost) or refuses, ``fallback`` (a selection and
   * execCommand("copy")).
   *
   * @param {string} text
   * @param {{writeText: function(string): Promise<void>} | undefined} clipboard
   * @param {function(string): boolean} fallback true when it copied
   * @returns {Promise<boolean>} whether the text is on the clipboard
   */
  function copyText(text, clipboard, fallback) {
    function viaFallback() {
      try {
        return fallback(text);
      } catch (_) {
        return false;
      }
    }
    if (!clipboard || typeof clipboard.writeText !== "function") return Promise.resolve(viaFallback());
    try {
      return Promise.resolve(clipboard.writeText(text)).then(
        function () {
          return true;
        },
        viaFallback
      );
    } catch (_) {
      return Promise.resolve(viaFallback());
    }
  }

  var pure = { currentIndex: currentIndex, scrollToShow: scrollToShow, codeText: codeText, copyText: copyText };
  if (typeof module === "object" && module.exports) {
    module.exports = pure;
    return;
  }

  // --- the page --------------------------------------------------------------------------------

  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var dark = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", dark);
      try {
        localStorage.setItem("theme", dark ? "dark" : "light");
      } catch (_) {}
    });
  }

  var menu = document.getElementById("menu-button");
  var sidebar = document.getElementById("sidebar");
  if (menu && sidebar) {
    menu.addEventListener("click", function () {
      var open = sidebar.classList.toggle("is-open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  var toc = document.querySelector(".toc");
  if (toc) watchToc(toc);

  function anchorOf(href) {
    var id = href.charAt(0) === "#" ? href.slice(1) : "";
    try {
      return decodeURIComponent(id);
    } catch (_) {
      return id;
    }
  }

  function watchToc(toc) {
    var entries = [];
    toc.querySelectorAll("a[href^='#']").forEach(function (link) {
      var heading = document.getElementById(anchorOf(link.getAttribute("href")));
      if (!heading) return;
      var item = link.parentElement;
      var parent = null;
      if (item && item.classList.contains("toc__h3")) {
        for (var at = item.previousElementSibling; at; at = at.previousElementSibling) {
          if (at.classList.contains("toc__h2")) {
            parent = at.querySelector("a");
            break;
          }
        }
      }
      entries.push({ link: link, heading: heading, parent: parent });
    });
    if (!entries.length) return;

    // Where things are, measured when the layout changes rather than on every scroll: a scroll
    // then reads only window.scrollY, so it never makes the browser lay the page out.
    var tops = [];
    var height = 0;
    var line = 0;
    var shown = false;
    var current = -2;
    // A heading the reader jumped to (a click, a #fragment) stays current until they scroll on
    // their own; a short closing section cannot scroll up to the reading line. Their own scroll
    // is told by its input (wheel, touch, keys, the scrollbar), not by the scroll events: the
    // browser's jump to a #fragment on load can land after the page has settled.
    var pinned = -1;
    var frame = 0;

    function measure() {
      shown = toc.getClientRects().length > 0;
      if (!shown) return;
      var y = window.scrollY;
      tops = entries.map(function (entry) {
        return entry.heading.getBoundingClientRect().top + y;
      });
      height = document.documentElement.scrollHeight;
      // The line is where a jump to a heading puts it (html's scroll-padding-top, which clears
      // the sticky header), plus a little; the preview band scrolls away with the page.
      var padding = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0;
      var header = document.querySelector(".site-header");
      line = Math.max(padding, header ? header.getBoundingClientRect().bottom : 0) + 8;
    }

    function mark(index) {
      if (index === current) return;
      current = index;
      entries.forEach(function (entry, at) {
        var on = at === index;
        entry.link.classList.toggle("is-current", on);
        if (on) entry.link.setAttribute("aria-current", "location");
        else entry.link.removeAttribute("aria-current");
        if (entry.parent) entry.parent.classList.remove("is-current-parent");
      });
      if (index < 0) return;
      var entry = entries[index];
      if (entry.parent) entry.parent.classList.add("is-current-parent");
      // Scroll the table alone: scrollIntoView would move the page too.
      var box = toc.getBoundingClientRect();
      var rect = entry.link.getBoundingClientRect();
      var next = scrollToShow(
        { scrollTop: toc.scrollTop, height: toc.clientHeight },
        { top: rect.top - box.top - toc.clientTop + toc.scrollTop, height: rect.height },
        rect.height * 2
      );
      if (next !== toc.scrollTop) toc.scrollTop = next;
    }

    function update() {
      frame = 0;
      if (!shown) return;
      if (pinned >= 0) return mark(pinned);
      mark(currentIndex(tops, { scrollY: window.scrollY, viewport: window.innerHeight, height: height, line: line }));
    }

    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }

    function pin(index) {
      if (index < 0) return;
      pinned = index;
      mark(index);
    }

    function unpin() {
      if (pinned < 0) return;
      pinned = -1;
      schedule();
    }

    function pinHash() {
      var id = anchorOf(location.hash);
      for (var at = 0; at < entries.length; at++) {
        if (entries[at].heading.id === id) return pin(at);
      }
    }

    window.addEventListener("scroll", schedule, { passive: true });
    // A press on a table entry comes before its click, which pins again.
    ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (type) {
      window.addEventListener(type, unpin, { passive: true, capture: true });
    });
    window.addEventListener("resize", function () {
      measure();
      schedule();
    });
    // Images and the web font move headings after the first measure.
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(function () {
        measure();
        schedule();
      }).observe(document.body);
    }
    window.addEventListener("hashchange", pinHash);
    entries.forEach(function (entry, at) {
      // Clicking the entry of the fragment already in the address fires no hashchange.
      entry.link.addEventListener("click", function () {
        pin(at);
      });
    });

    measure();
    if (location.hash) pinHash();
    update();
  }

  // --- the code blocks' copy buttons (#173) -----------------------------------------------------

  // Drawn here rather than by the build: without JavaScript a button could not copy, so it is
  // better not shown. The build gives every <pre> its .code-block box (render.py's _highlight).
  var ICON_COPY =
    '<svg class="copy-button__copy" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">' +
    '<rect x="9" y="9" width="11" height="11" rx="2" stroke-width="2" />' +
    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1" /></svg>';
  var ICON_DONE =
    '<svg class="copy-button__done" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">' +
    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>';
  var ICON_FAILED =
    '<svg class="copy-button__failed" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">' +
    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>';
  var LABEL = "코드 복사";
  var SAID = { copied: "복사했습니다", failed: "복사하지 못했습니다" };
  var SHOWN_FOR = 2000;

  addCopyButtons();

  function addCopyButtons() {
    var blocks = document.querySelectorAll(".code-block");
    if (!blocks.length) return;
    // One live region for the page: a button's own label stays "코드 복사", the outcome is said here.
    var status = document.createElement("span");
    status.className = "visually-hidden";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    document.body.appendChild(status);
    blocks.forEach(function (block) {
      var pre = block.querySelector("pre");
      if (!pre) return;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "copy-button";
      button.setAttribute("aria-label", LABEL);
      button.title = LABEL;
      button.innerHTML = ICON_COPY + ICON_DONE + ICON_FAILED + '<span class="copy-button__note" aria-hidden="true"></span>';
      var timer = 0;
      button.addEventListener("click", function () {
        var code = pre.querySelector("code") || pre;
        copyText(codeText(code.textContent || ""), navigator.clipboard, selectAndCopy).then(function (copied) {
          var state = copied ? "copied" : "failed";
          button.setAttribute("data-state", state);
          button.querySelector(".copy-button__note").textContent = copied ? "복사됨" : "복사 실패";
          // Emptied first, so the same outcome twice in a row is said twice.
          status.textContent = "";
          status.textContent = SAID[state];
          clearTimeout(timer);
          timer = setTimeout(function () {
            button.removeAttribute("data-state");
            button.querySelector(".copy-button__note").textContent = "";
            status.textContent = "";
          }, SHOWN_FOR);
        });
      });
      block.appendChild(button);
    });
  }

  function selectAndCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "copy-scratch";
    var focused = document.activeElement;
    document.body.appendChild(area);
    area.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } finally {
      document.body.removeChild(area);
      if (focused && typeof focused.focus === "function") focused.focus();
    }
    return copied;
  }
})();
