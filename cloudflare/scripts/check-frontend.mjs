import { readFileSync } from "node:fs";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const html = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) throw new Error("Frontend script not found");

class FakeElement {
  constructor() {
    this.innerHTML = "";
    this.value = "";
    this.dataset = {};
    this.classList = { add() {}, remove() {} };
  }
  addEventListener() {}
  click() {}
  focus() {}
}

const elements = new Map([
  ["#storeSelect", new FakeElement()],
  ["#addProductBtn", new FakeElement()],
  ["#searchInput", new FakeElement()],
  ["#productList", new FakeElement()],
  ["#main", new FakeElement()],
  ["#exportBtn", new FakeElement()],
  ["#importBtn", new FakeElement()],
  ["#importFile", new FakeElement()],
  ["#imageFile", new FakeElement()],
  ["#toast", new FakeElement()]
]);

const document = {
  documentElement: { dataset: {} },
  querySelector(selector) {
    return elements.get(selector) || new FakeElement();
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return new FakeElement(); }
};

let etag = "";
const fetch = async (url, options = {}) => {
  const method = options.method || "GET";
  if (String(url).endsWith("/api/state") && method === "GET") {
    return {
      ok: true,
      status: 200,
      headers: { get: name => name.toLowerCase() === "etag" ? etag : null },
      json: async () => ({ version: 2, stores: [] })
    };
  }
  if (String(url).endsWith("/api/state") && method === "PUT") {
    etag = '"test-etag"';
    return {
      ok: true,
      status: 200,
      headers: { get: name => name.toLowerCase() === "etag" ? etag : null },
      json: async () => ({ ok: true })
    };
  }
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => ({ ok: true }),
    blob: async () => new Blob()
  };
};

const storage = new Map();
const context = vm.createContext({
  console,
  document,
  window: {},
  localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value))
  },
  matchMedia: () => ({ matches: false }),
  crypto: webcrypto,
  fetch,
  requestAnimationFrame: fn => fn(),
  setTimeout,
  clearTimeout,
  structuredClone,
  Blob,
  FileReader: class {},
  URL,
  Image: class {},
  confirm: () => true,
  prompt: (_message, value) => value
});

vm.runInContext(match[1], context, { filename: "public/index.html" });
await new Promise(resolve => setTimeout(resolve, 20));

const initialStores = vm.runInContext("state.stores.length", context);
if (initialStores !== 1) throw new Error(`Expected 1 initialized store, got ${initialStores}`);

vm.runInContext("addStore()", context);
const storesAfterAdd = vm.runInContext("state.stores.length", context);
if (storesAfterAdd !== 2) throw new Error(`Add store failed: ${storesAfterAdd}`);

vm.runInContext("newProduct()", context);
const productsAfterAdd = vm.runInContext("currentStore().products.length", context);
if (productsAfterAdd !== 1) throw new Error(`Add product failed: ${productsAfterAdd}`);

console.log("Frontend runtime smoke test passed: init, addStore, newProduct");
