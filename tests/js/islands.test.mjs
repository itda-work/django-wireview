import assert from "node:assert/strict";
import { test } from "node:test";

import { Island, Islands, isIslandPair, syncAttributes } from "../../wireview/static/wireview/islands.mjs";

/** An island element as the tag renders it, with attributes a test sets, and the hook on it. */
function element(name, props) {
  const attrs = { "wire-island": name, "wire-island-props": JSON.stringify(props) };
  const root = { inner: true, attributes: [], replaceChildren() {} };
  const el = {
    getAttribute: (key) => attrs[key] ?? null,
    set: (key, value) => (attrs[key] = value),
    querySelector: (selector) => (selector === ':scope > [wire-update="ignore"]' ? root : null),
  };
  const pushed = [];
  const handled = [];
  const hook = {
    el,
    pushEvent: (event, payload) => {
      pushed.push([event, payload]);
      return Promise.resolve({ ok: event });
    },
    handleEvent: (event, callback) => {
      handled.push([event, callback]);
      return () => handled.splice(handled.indexOf(callback), 1);
    },
  };
  return { el, root, hook, pushed, handled };
}

/** A definition that records what it is asked. */
function recorder() {
  const calls = [];
  const definition = {
    mount(el, props, ctx) {
      calls.push(["mount", el, props]);
      definition.ctx = ctx;
      return {
        update: (next) => calls.push(["update", next]),
        unmount: () => calls.push(["unmount"]),
      };
    },
  };
  return { definition, calls };
}

function quietIslands() {
  const reported = [];
  return { islands: new Islands({ report: (message) => reported.push(message) }), reported };
}

test("mounting draws into the inner element with the props the element carries", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  islands.registry.Map = definition;
  const page = element("Map", { zoom: 3 });

  new Island(islands, page.hook).mount();

  assert.deepEqual(calls, [["mount", page.root, { zoom: 3 }]]);
  assert.deepEqual(definition.ctx.props, { zoom: 3 });
});

test("a morph hands the props over once when they changed, and never when they did not", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  islands.registry.Map = definition;
  const page = element("Map", { zoom: 3 });
  const island = new Island(islands, page.hook);
  island.mount();

  island.updated();
  page.el.set("wire-island-props", JSON.stringify({ zoom: 4 }));
  island.updated();
  island.updated();

  assert.deepEqual(calls.slice(1), [["update", { zoom: 4 }]]);
  assert.deepEqual(definition.ctx.props, { zoom: 4 });
});

test("an element mounted before its definition is mounted when the definition comes", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  const page = element("Late", { a: 1 });
  const island = new Island(islands, page.hook);
  island.mount();
  assert.deepEqual(calls, []);

  page.el.set("wire-island-props", JSON.stringify({ a: 2 }));
  islands.registry.Late = definition;

  assert.deepEqual(calls, [["mount", page.root, { a: 2 }]]);
  island.updated();
  assert.equal(calls.length, 1, "nothing new to hand over");
});

test("an element that left before its definition came is not mounted", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  const island = new Island(islands, element("Late", {}).hook);
  island.mount();
  island.destroy();

  islands.registry.Late = definition;

  assert.deepEqual(calls, []);
  assert.equal(islands.waiting.size, 0);
});

test("leaving unmounts it once, and nothing reaches it after", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  islands.registry.Map = definition;
  const page = element("Map", {});
  const island = new Island(islands, page.hook);
  island.mount();
  const heard = [];
  definition.ctx.onStatus((status) => heard.push(status));

  island.destroy();
  island.destroy();
  page.el.set("wire-island-props", JSON.stringify({ x: 1 }));
  island.updated();
  island.hear("disconnected");

  assert.deepEqual(calls.slice(1), [["unmount"]]);
  assert.deepEqual(heard, []);
});

test("another island under the same element unmounts the old one and mounts the new", () => {
  const { islands } = quietIslands();
  const map = recorder();
  const chart = recorder();
  islands.registry.Map = map.definition;
  islands.registry.Chart = chart.definition;
  const page = element("Map", { a: 1 });
  const island = new Island(islands, page.hook);
  island.mount();

  page.el.set("wire-island", "Chart");
  island.updated();

  assert.deepEqual(map.calls.slice(1), [["unmount"]]);
  assert.deepEqual(chart.calls, [["mount", page.root, { a: 1 }]]);
});

test("the context pushes through the hook, listens through it, and hears the connection", async () => {
  const { islands } = quietIslands();
  const { definition } = recorder();
  islands.registry.Map = definition;
  const page = element("Map", {});
  const island = new Island(islands, page.hook);
  island.mount();
  const { ctx } = definition;

  assert.deepEqual(await ctx.push("save", { n: 1 }), { ok: "save" });
  assert.deepEqual(await ctx.push("ping"), { ok: "ping" });
  assert.deepEqual(page.pushed, [
    ["save", { n: 1 }],
    ["ping", {}],
  ]);

  const off = ctx.on("moved", () => {});
  assert.equal(page.handled.length, 1);
  off();

  const heard = [];
  const stop = ctx.onStatus((status) => heard.push([status, ctx.status]));
  island.hear("disconnected");
  island.hear("reconnected");
  stop();
  island.hear("disconnected");
  assert.deepEqual(heard, [
    ["disconnected", "disconnected"],
    ["reconnected", "connected"],
  ]);
});

test("what the island's code throws is reported and the island goes on", () => {
  const { islands, reported } = quietIslands();
  let updates = 0;
  islands.registry.Shaky = {
    mount() {
      return {
        update() {
          updates++;
          throw new Error("update");
        },
        unmount() {
          throw new Error("unmount");
        },
      };
    },
  };
  const page = element("Shaky", { a: 1 });
  const island = new Island(islands, page.hook);
  island.mount();
  page.el.set("wire-island-props", JSON.stringify({ a: 2 }));
  island.updated();
  page.el.set("wire-island-props", JSON.stringify({ a: 3 }));
  island.updated();
  island.destroy();

  assert.equal(updates, 2);
  assert.deepEqual(reported, [
    'Island "Shaky" threw in update()',
    'Island "Shaky" threw in update()',
    'Island "Shaky" threw in unmount()',
  ]);
});

test("props that are not JSON are reported and nothing is mounted until they are", () => {
  const { islands, reported } = quietIslands();
  const { definition, calls } = recorder();
  islands.registry.Map = definition;
  const page = element("Map", {});
  page.el.set("wire-island-props", "{nope");
  const island = new Island(islands, page.hook);
  island.mount();
  assert.deepEqual(calls, []);
  assert.equal(reported.length, 1);

  page.el.set("wire-island-props", JSON.stringify({ ok: true }));
  island.updated();
  assert.deepEqual(calls, [["mount", page.root, { ok: true }]]);
});

test("an instance without update or unmount is fine", () => {
  const { islands, reported } = quietIslands();
  islands.registry.Plain = { mount() {} };
  const page = element("Plain", {});
  const island = new Island(islands, page.hook);
  island.mount();
  page.el.set("wire-island-props", JSON.stringify({ a: 1 }));
  island.updated();
  island.destroy();
  assert.deepEqual(reported, []);
});

test("a name Object's prototype has is not a definition until it is assigned", () => {
  const { islands } = quietIslands();
  const { definition, calls } = recorder();
  const page = element("constructor", { a: 1 });
  const island = new Island(islands, page.hook);
  island.mount();
  assert.deepEqual(calls, []);

  islands.registry.constructor = definition;
  assert.deepEqual(calls, [["mount", page.root, { a: 1 }]]);
});

test("the island that replaces another under the element does not hear the old one's subscriptions", () => {
  const { islands } = quietIslands();
  const heard = [];
  islands.registry.A = {
    mount(el, props, ctx) {
      ctx.on("saved", () => heard.push("A:event"));
      ctx.onStatus((status) => heard.push(`A:${status}`));
    },
  };
  islands.registry.B = {
    mount(el, props, ctx) {
      ctx.onStatus((status) => heard.push(`B:${status}`));
    },
  };
  const page = element("A", {});
  const island = new Island(islands, page.hook);
  island.mount();
  assert.equal(page.handled.length, 1);

  page.el.set("wire-island", "B");
  island.updated();
  island.hear("disconnected");

  assert.equal(page.handled.length, 0, "A's handler was removed");
  assert.deepEqual(heard, ["B:disconnected"]);
});

/** An element with attributes, enough for syncAttributes. */
function attributed(attrs) {
  const own = new Map(Object.entries(attrs));
  return {
    nodeType: 1,
    get attributes() {
      return [...own].map(([name, value]) => ({ name, value }));
    },
    hasAttribute: (name) => own.has(name),
    getAttribute: (name) => own.get(name) ?? null,
    setAttribute: (name, value) => own.set(name, value),
    removeAttribute: (name) => own.delete(name),
    own,
  };
}

test("a morph takes the render's attributes for an island's element, and nothing else", () => {
  const live = attributed({ "wire-island": "Map", "wire-island-props": '{"a":1}', class: "old", "data-gone": "x" });
  const next = attributed({ "wire-island": "Map", "wire-island-props": '{"a":2}', class: "new" });

  assert.equal(isIslandPair(live, next), true);
  syncAttributes(live, next);

  assert.deepEqual(Object.fromEntries(live.own), { "wire-island": "Map", "wire-island-props": '{"a":2}', class: "new" });
});

test("only two island elements are a pair", () => {
  const island = attributed({ "wire-island": "Map" });
  assert.equal(isIslandPair(island, attributed({})), false);
  assert.equal(isIslandPair(attributed({}), island), false);
  assert.equal(isIslandPair(island, { nodeType: 3 }), false);
});
