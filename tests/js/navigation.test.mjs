import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  BEFORE_NAVIGATE_EVENT,
  FILE_KEY,
  NAVIGATED_EVENT,
  NAVIGATION_FAILED_EVENT,
  NavigationLog,
  PAGE_KEY,
  TraversalUndo,
  arrivesOnRestore,
  attachmentName,
  beforeNavigateDetail,
  boostsForm,
  boostsLink,
  carriedAcross,
  destination,
  fetchOutcome,
  formMethod,
  formRequest,
  isAttachment,
  isDocument,
  isFragmentLink,
  isPatch,
  isSameUrl,
  newPageId,
  onlyFragmentMoved,
  pageRequest,
  patchesPage,
  returnsToPatch,
  stamped,
} from "../../wireview/static/wireview/navigation.mjs";

const entry = (hook, navigation, connected = true) => ({ hook, navigation, connected });

test("the event has the documented name", () => {
  assert.equal(NAVIGATED_EVENT, "wireview:navigated");
});

test("a hook that was on the page before the navigation hears it", () => {
  assert.deepEqual(carriedAcross([entry("player", 0)], 1), ["player"]);
  assert.deepEqual(carriedAcross([entry("player", 2)], 3), ["player"]);
});

test("a hook the navigation brought hears mounted, not navigated", () => {
  // A Back paints the cached page, mounting its hooks under the navigation's
  // own token, before the fetched page lands
  assert.deepEqual(carriedAcross([entry("old", 0), entry("cached", 1)], 1), ["old"]);
});

test("a hook the morph removed is left to destroyed", () => {
  assert.deepEqual(carriedAcross([entry("gone", 0, false), entry("kept", 0)], 1), ["kept"]);
});

test("the log says where each navigation came from, and what started it (#154)", () => {
  const log = new NavigationLog("http://x/a/");
  assert.deepEqual(log.landed("http://x/b/", "link"), { url: "http://x/b/", previousUrl: "http://x/a/", kind: "link" });
  // A Back has already moved the address bar; the log still knows the page it left
  assert.deepEqual(log.landed("http://x/a/", "popstate"), {
    url: "http://x/a/",
    previousUrl: "http://x/b/",
    kind: "popstate",
  });
});

test("a patch moves the log without an announcement (#169)", () => {
  const log = new NavigationLog("http://x/a/");
  log.patched("http://x/a/?tab=b");
  assert.deepEqual(log.landed("http://x/b/", "push"), {
    url: "http://x/b/",
    previousUrl: "http://x/a/?tab=b",
    kind: "push",
  });
});

test("the event before a move has the documented name (#154)", () => {
  assert.equal(BEFORE_NAVIGATE_EVENT, "wireview:before-navigate");
});

test("the event before a move names where it goes, resolved, and what started it (#154)", () => {
  assert.deepEqual(beforeNavigateDetail("?tab=b", "http://x/a/", "push", { patch: true }), {
    url: "http://x/a/?tab=b",
    kind: "push",
    patch: true,
  });
  assert.deepEqual(beforeNavigateDetail("/b/", "http://x/a/", "link"), { url: "http://x/b/", kind: "link", patch: false });
  // A form's move names the form, so a guard can let the form it guards go
  const form = { tagName: "FORM" };
  assert.equal(beforeNavigateDetail("/post/", "http://x/a/", "form", { form }).form, form);
  assert.equal("form" in beforeNavigateDetail("/b/", "http://x/a/", "link"), false);
});

test("without an undo on its way, a popstate is an ordinary traversal (#154)", () => {
  assert.equal(new TraversalUndo().arrived("k1"), "none");
});

test("an undo is over when it arrives at the entry it returns to (#154)", () => {
  const undo = new TraversalUndo();
  undo.start("c");
  assert.equal(undo.arrived("c"), "returned");
  // The next popstate is the user's again
  assert.equal(undo.arrived("b"), "none");
});

test("a traversal that runs before the undo is passed over, and the undo still arrives (#154)", () => {
  // Back from c to b, cancelled; another Back queued first lands on a
  const undo = new TraversalUndo();
  undo.start("c");
  assert.equal(undo.arrived("a"), "passing");
  assert.equal(undo.arrived("b"), "passing");
  assert.equal(undo.arrived("c"), "returned");
});

test("an undo the browser dropped stops swallowing popstates (#154)", () => {
  const undo = new TraversalUndo();
  const first = undo.start("c");
  assert.equal(undo.failed(first), true, "the page has to arrive where the address bar is");
  assert.equal(undo.arrived("a"), "none");
  // A failure reported for an undo another one replaced changes nothing
  const second = undo.start("b");
  assert.equal(undo.failed(first), false);
  assert.equal(undo.arrived("b"), "returned");
  assert.equal(undo.failed(second), false, "it arrived already");
});

test("a move on the same path is a patch; another path or origin is not (#169)", () => {
  assert.equal(isPatch("http://x/a/", "?tab=b"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "?tab=c"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "#top"), true);
  assert.equal(isPatch("http://x/a/", "/a/?tab=b"), true);
  assert.equal(isPatch("http://x/a/", "http://x/a/?tab=b"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "/a/"), true, "dropping the query stays on the page");
  assert.equal(isPatch("http://x/a/", "/b/"), false);
  assert.equal(isPatch("http://x/a/", "/a"), false, "another path, though Django may redirect it");
  assert.equal(isPatch("http://x/a/", "http://y/a/"), false);
});

test("stamping keeps what the entry held and survives a null state (#169)", () => {
  assert.deepEqual(stamped(null, "p1"), { [PAGE_KEY]: "p1" });
  assert.deepEqual(stamped({ content: "<body>", scrollY: 3 }, "p2"), { content: "<body>", scrollY: 3, [PAGE_KEY]: "p2" });
  assert.deepEqual(stamped({ [PAGE_KEY]: "old" }, "new"), { [PAGE_KEY]: "new" });
});

test("page ids differ, so a reloaded page owns none of the old entries (#169)", () => {
  const ids = new Set(Array.from({ length: 100 }, () => newPageId()));
  assert.equal(ids.size, 100);
});

test("Back or Forward is a patch only to an entry the page on screen made, on its path (#169)", () => {
  const page = { id: "p1", url: "http://x/a/" };
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/a/?tab=b", page), true);
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1", content: "<body>" }, "http://x/a/", page), true);
  // Another page's entry, or one from before a reload
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p0" }, "http://x/a/?tab=b", page), false);
  // An entry a boosted push left behind, or a page loaded before this code
  assert.equal(returnsToPatch({ content: "<body>" }, "http://x/a/", page), false);
  assert.equal(returnsToPatch(null, "http://x/a/", page), false);
  // Its id, but not its path: never made by a patch, so fetched
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/b/", page), false);
});

test("a relative URL is resolved against the document's base, as pushState and fetch resolve it (#169)", () => {
  // <base href="/b/"> on the page /a/: "?x" goes to /b/?x, another path
  assert.equal(isPatch("http://x/a/", "?x", "http://x/b/"), false);
  assert.equal(isPatch("http://x/a/", "?x", "http://x/a/"), true);
  assert.equal(isPatch("http://x/a/", "/a/?x", "http://x/b/"), true, "an absolute path ignores the base");
  assert.equal(patchesPage({ id: "p1", url: "http://x/a/" }, "?x", "http://x/b/"), false);
});

test("a push is judged against the page on screen, not the address bar (#169)", () => {
  // A push to /b/ moved the address bar before its fetch: "?tab=b" from the page
  // still on screen is not a patch of /b/
  const page = { id: "p1", url: "http://x/a/" };
  assert.equal(patchesPage(page, "/b/?tab=b", "http://x/b/"), false);
  assert.equal(patchesPage(page, "/a/?tab=b", "http://x/b/"), true);
});

test("nothing is a patch while a fetching navigation is in flight (#169)", () => {
  const leaving = { id: null, url: "http://x/a/" };
  assert.equal(patchesPage(leaving, "?tab=b", "http://x/a/"), false);
  // A cached paint shows another page's copy: no entry is the page's own, not
  // even one stamped null
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/a/?tab=b", leaving), false);
  assert.equal(returnsToPatch({ [PAGE_KEY]: null }, "http://x/a/?tab=b", leaving), false);
  assert.equal(returnsToPatch({}, "http://x/a/?tab=b", leaving), false);
});

test("a push to the URL on screen is the same URL, however it is written (#170)", () => {
  assert.equal(isSameUrl("http://x/a/?tab=b", "?tab=b"), true);
  assert.equal(isSameUrl("http://x/a/?tab=b", "/a/?tab=b"), true);
  assert.equal(isSameUrl("http://x/a/?tab=b", "http://x/a/?tab=b"), true);
  assert.equal(isSameUrl("http://x/a/", "/a/"), true);
  assert.equal(isSameUrl("http://x/a/?tab=b", "?tab=c"), false);
  assert.equal(isSameUrl("http://x/a/?tab=b", "/a/"), false, "dropping the query is a move");
  assert.equal(isSameUrl("http://x/a/", "#top"), false, "a fragment is a move");
  assert.equal(isSameUrl("http://x/a/#top", "/a/"), false);
  // Against the document's base, as pushState resolves it
  assert.equal(isSameUrl("http://x/a/b", "b", "http://x/a/"), true);
});

test("only the fragment moved between two URLs of one document (#170)", () => {
  assert.equal(onlyFragmentMoved("http://x/a/", "http://x/a/#s"), true);
  assert.equal(onlyFragmentMoved("http://x/a/#s", "http://x/a/"), true, "Back from the fragment");
  assert.equal(onlyFragmentMoved("http://x/a/?q=1#s", "http://x/a/?q=1#t"), true);
  assert.equal(onlyFragmentMoved("http://x/a/", "http://x/a/"), true);
  assert.equal(onlyFragmentMoved("http://x/a/?q=1", "http://x/a/?q=2#s"), false, "the query moved too");
  assert.equal(onlyFragmentMoved("http://x/a/", "http://x/b/#s"), false);
  assert.equal(onlyFragmentMoved("http://x/a/", "http://y/a/#s"), false);
});

test("a link to a fragment of this document is the browser's; one to the page itself is not (#170)", () => {
  assert.equal(isFragmentLink("http://x/a/", "#section"), true);
  assert.equal(isFragmentLink("http://x/a/", "http://x/a/#section"), true);
  assert.equal(isFragmentLink("http://x/a/?q=1", "?q=1#section"), true);
  assert.equal(isFragmentLink("http://x/a/#one", "#two"), true);
  assert.equal(isFragmentLink("http://x/a/", "#"), true, "an empty fragment is a fragment too");
  assert.equal(isFragmentLink("http://x/a/", "/a/"), false, "no fragment: a load of the page");
  assert.equal(isFragmentLink("http://x/a/#one", "/a/"), false);
  assert.equal(isFragmentLink("http://x/a/?q=1", "#section", "http://x/a/"), false, "the base drops the query");
  assert.equal(isFragmentLink("http://x/a/", "/b/#section"), false);
  assert.equal(isFragmentLink("http://x/a/", "?q=2#section"), false);
});

test("the failure event has the documented name (#170)", () => {
  assert.equal(NAVIGATION_FAILED_EVENT, "wireview:navigation-failed");
});

// --- the method a boosted form submits with (#170) ---

test("a form's method is read as the browser reads it: get, post or dialog, whatever the case", () => {
  assert.equal(formMethod("POST"), "post");
  assert.equal(formMethod("post"), "post");
  assert.equal(formMethod("Get"), "get");
  assert.equal(formMethod("DiaLog"), "dialog");
  assert.equal(formMethod(null), "get");
});

test("a method HTML has no state for goes as a GET, as the browser sends it -- never as itself", () => {
  // no-cors would refuse to send a PUT at all, and the browser never does
  for (const method of ["put", "PUT", "delete", "patch", "", "head", " post"]) {
    assert.equal(formMethod(method), "get", method);
  }
});

test("the submitter's formmethod wins over the form's, an empty or unknown one being a GET", () => {
  assert.equal(formMethod("get", "post"), "post");
  assert.equal(formMethod("post", "get"), "get");
  assert.equal(formMethod("post", "put"), "get");
  assert.equal(formMethod("post", ""), "get");
  assert.equal(formMethod("post", null), "post");
  assert.equal(formMethod("post", undefined), "post");
  assert.equal(formMethod("get", "dialog"), "dialog");
});

// --- what a boosted fetch came to (#170) ---

test("a form is sent in no-cors mode, so a redirect off the site is an answer and not a network error", () => {
  const body = new FormData();
  assert.deepEqual(formRequest("POST", body), { method: "POST", body, mode: "no-cors" });
});

test("an answer is a page, an error page included", () => {
  assert.equal(fetchOutcome({ response: { type: "basic" } }), "page");
});

test("another origin's answer is never a page, whatever its CORS headers allow (#262)", () => {
  // A cors-mode fetch that another origin answered, or one this origin
  // redirected there: the other origin let it be read
  assert.equal(fetchOutcome({ response: { type: "cors" } }), "elsewhere");
  const here = "http://x.test";
  assert.equal(fetchOutcome({ response: { type: "basic", url: "http://x.test/a/" } }, here), "page");
  assert.equal(fetchOutcome({ response: { type: "basic", url: "http://y.test/a/" } }, here), "elsewhere");
  assert.equal(fetchOutcome({ response: { type: "basic", url: "https://x.test/a/" } }, here), "elsewhere");
  assert.equal(fetchOutcome({ response: { type: "basic", url: "http://x.test:81/a/" } }, here), "elsewhere");
  // A response the page made itself has no URL
  assert.equal(fetchOutcome({ response: { type: "basic", url: "" } }, here), "page");
});

test("an opaque answer is a redirect to another origin, whose address the page cannot see", () => {
  assert.equal(fetchOutcome({ response: { type: "opaque" } }), "elsewhere");
  assert.equal(fetchOutcome({ response: { type: "opaqueredirect" } }), "elsewhere");
});

test("a stopped request is not a failed one", () => {
  const aborted = Object.assign(new Error("The user aborted a request."), { name: "AbortError" });
  assert.equal(fetchOutcome({ error: aborted }), "aborted");
});

test("no answer at all is the network's failure", () => {
  assert.equal(fetchOutcome({ error: new TypeError("Failed to fetch") }), "unanswered");
  // The body failed on the way: an answer that never arrived
  assert.equal(fetchOutcome({ response: { type: "basic" }, error: new TypeError("network error") }), "unanswered");
  assert.equal(fetchOutcome({ error: null }), "unanswered");
});

// --- answers and links that are not a page to boost (#227) ---

const answer = (headers, type = "basic") => ({ type, headers: new Headers(headers) });

test("an HTML document is a page, whatever the case and parameters of its type", () => {
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "text/html; charset=utf-8" }) }), "page");
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "TEXT/HTML" }) }), "page");
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "application/xhtml+xml" }) }), "page");
  // An error page is a page too
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "text/html", "Content-Disposition": "inline" }) }), "page");
});

test("an answer that is not an HTML document is a file", () => {
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "text/csv" }) }), "file");
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "application/pdf" }) }), "file");
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "text/plain; charset=utf-8" }) }), "file");
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "application/json" }) }), "file");
  // A type that only starts like one
  assert.equal(fetchOutcome({ response: answer({ "Content-Type": "text/html-fragment" }) }), "file");
  // No type: the browser sniffs it, and boost would parse anything as HTML
  assert.equal(fetchOutcome({ response: answer({}) }), "file");
});

test("an HTML answer the server marks to be saved is a file", () => {
  const headers = { "Content-Type": "text/html", "Content-Disposition": 'Attachment; filename="page.html"' };
  assert.equal(fetchOutcome({ response: answer(headers) }), "file");
});

test("an opaque answer stays a redirect elsewhere: its headers are hidden", () => {
  assert.equal(fetchOutcome({ response: answer({}, "opaque") }), "elsewhere");
  assert.equal(fetchOutcome({ response: answer({}, "opaqueredirect") }), "elsewhere");
});

test("a document is text/html or application/xhtml+xml, not saved", () => {
  assert.equal(isDocument("text/html"), true);
  assert.equal(isDocument(" text/html ;charset=utf-8"), true);
  assert.equal(isDocument("application/xhtml+xml", "inline"), true);
  assert.equal(isDocument("text/html", "attachment"), false);
  assert.equal(isDocument("image/svg+xml"), false);
  assert.equal(isDocument(null), false);
});

test("an attachment is the disposition type, not a word in a parameter", () => {
  assert.equal(isAttachment("attachment"), true);
  assert.equal(isAttachment(' ATTACHMENT ; filename="a.csv"'), true);
  assert.equal(isAttachment('inline; filename="attachment"'), false);
  assert.equal(isAttachment(null), false);
});

test("an attachment's name is filename* over filename, without a path", () => {
  assert.equal(attachmentName('attachment; filename="report.csv"'), "report.csv");
  assert.equal(attachmentName("attachment; filename=report.csv"), "report.csv");
  assert.equal(attachmentName("attachment; filename*=UTF-8''%EB%B3%B4%EA%B3%A0%EC%84%9C.csv; filename=\"r.csv\""), "보고서.csv");
  assert.equal(attachmentName('attachment; filename="a \\"b\\".txt"'), 'a "b".txt');
  assert.equal(attachmentName('attachment; filename="../../etc/passwd"'), "passwd");
  assert.equal(attachmentName('attachment; filename="C:\\\\x\\\\y.txt"'), "y.txt");
  // A name that cannot be read is the browser's to pick, as is none
  assert.equal(attachmentName("attachment; filename*=UTF-8''%E0%A4%A"), "");
  assert.equal(attachmentName("attachment; filename*=ISO-8859-1''r%E9sum%E9.txt; filename=resume.txt"), "resume.txt");
  assert.equal(attachmentName("attachment"), "");
  assert.equal(attachmentName(null), "");
});

/**
 * A stand-in for an element: its attributes, and the elements around it.
 * @param {Record<string, string>} attributes
 * @param {any} [parent]
 */
const element = (attributes, parent = null) => ({
  hasAttribute: (name) => name in attributes,
  getAttribute: (name) => attributes[name] ?? null,
  closest(selector) {
    const name = /^\[([\w-]+)\]$/.exec(selector)[1];
    for (let at = this; at !== null; at = at.parent) if (at.hasAttribute(name)) return at;
    return null;
  },
  parent,
});

test("a link boosts unless it is a download or turned off around it", () => {
  assert.equal(boostsLink(element({ href: "/a/" })), true);
  assert.equal(boostsLink(element({ href: "/a.csv", download: "" })), false);
  assert.equal(boostsLink(element({ href: "/a.csv", download: "a.csv" })), false);
  assert.equal(boostsLink(element({ href: "/a/", "wire-boost": "false" })), false);
  assert.equal(boostsLink(element({ href: "/a/", "wire-boost": " False " })), false);
  assert.equal(boostsLink(element({ href: "/a/" }, element({ "wire-boost": "false" }))), false);
});

test("the nearest wire-boost decides", () => {
  const off = element({ "wire-boost": "false" });
  assert.equal(boostsLink(element({ href: "/a/", "wire-boost": "" }, off)), true);
  assert.equal(boostsLink(element({ href: "/a/" }, element({ "wire-boost": "true" }, off))), true);
  assert.equal(boostsLink(element({ href: "/a/" }, element({}, element({ "wire-boost": "" })))), true);
  // A download is the browser's even where boost is turned on
  assert.equal(boostsLink(element({ href: "/a/", download: "", "wire-boost": "" })), false);
});

test("a form boosts only by its own wire-boost, and not when it is false", () => {
  assert.equal(boostsForm(element({ "wire-boost": "" })), true);
  assert.equal(boostsForm(element({ "wire-boost": "true" })), true);
  assert.equal(boostsForm(element({ "wire-boost": "false" })), false);
  assert.equal(boostsForm(element({ "wire-boost": " FALSE " })), false);
  assert.equal(boostsForm(element({})), false);
  // An element around it does not turn it on (#103)
  assert.equal(boostsForm(element({}, element({ "wire-boost": "" }))), false);
});

test("the mark on an entry a file left has its own name", () => {
  assert.equal(FILE_KEY, "wireviewFile");
  assert.notEqual(FILE_KEY, PAGE_KEY);
});

// --- a document the back/forward cache restored (#170) ---

test("a page restored mid-navigation arrives at the address bar again, even under the same URL", () => {
  const frozen = { id: null, url: "http://x/a/" };
  assert.equal(arrivesOnRestore(true, frozen, "http://x/b/", "http://x/a/"), true);
  assert.equal(arrivesOnRestore(true, frozen, "http://x/a/", "http://x/a/"), true);
});

test("a page restored at another entry than it showed arrives there", () => {
  const own = { id: "p1", url: "http://x/a/" };
  assert.equal(arrivesOnRestore(true, own, "http://x/a/?tab=x", "http://x/a/?tab=y"), true);
});

test("a page restored as it was, at its own entry, has nothing to do", () => {
  const own = { id: "p1", url: "http://x/a/" };
  assert.equal(arrivesOnRestore(true, own, "http://x/a/", "http://x/a/"), false);
  assert.equal(arrivesOnRestore(true, own, "http://x/a/", "http://x/a/#section"), false);
});

test("a page that was loaded, not restored, has nothing to do", () => {
  assert.equal(arrivesOnRestore(false, { id: null, url: "http://x/a/" }, "http://x/b/", "http://x/a/"), false);
});

// --- where a navigation goes (#262) ---

/**
 * The vectors tests/test_navigation_urls.py judges on the server, with what
 * the browser's URL parser makes of each: the two halves of the policy are
 * read side by side from one table.
 */
const VECTORS = JSON.parse(readFileSync(new URL("./navigation-urls.json", import.meta.url), "utf-8"));

test("every destination vector is read by the URL parser as the table says", () => {
  const base = VECTORS.base;
  const origin = new URL(base).origin;
  const wrong = VECTORS.vectors
    .map((vector) => ({ ...vector, got: destination(vector.url, base, origin) }))
    .filter(({ client, href, got }) => got.kind !== client || (href !== undefined && got.href !== href))
    .map(({ url, client, got }) => ({ url, client, got }));
  assert.deepEqual(wrong, []);
});

test("a destination that does not parse is refused", () => {
  assert.deepEqual(destination("http://[::1", "http://x.test/", "http://x.test"), { kind: "refused", href: null });
});

test("a page's fetch does not follow a redirect to another origin; a form's still tells one apart", () => {
  assert.deepEqual(pageRequest(), { mode: "same-origin" });
  assert.equal(formRequest("POST", new FormData()).mode, "no-cors");
});
