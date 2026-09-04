import { readFileSync } from "node:fs";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const html = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) throw new Error("Frontend script not found");

for (const required of [
  'id="editProductBtn"',
  'id="themeGlobalBtn"',
  'data-modal-tab="info"',
  'data-modal-tab="keywords"',
  'data-modal-tab="negatives"',
  'class="product-nav"',
  'id="storeModal"',
  'data-main-view="overview"'
]) {
  if (!html.includes(required)) throw new Error(`Missing UI structure: ${required}`);
}
if (html.includes('data-action="add-keyword"') || html.includes('id="skuInput"')) {
  throw new Error("Right-side editing controls must not return");
}

class FakeElement {
  constructor() {
    this.innerHTML = "";
    this.value = "";
    this.dataset = {};
    this.textContent = "";
    this.disabled = false;
    this.attributes = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener() {}
  setAttribute(name, value) { this.attributes[name] = String(value); }
  click() {}
  focus() {}
  select() {}
}

const elements = new Map([
  ["#storeSelect", new FakeElement()],
  ["#addProductBtn", new FakeElement()],
  ["#editProductBtn", new FakeElement()],
  ["#themeGlobalBtn", new FakeElement()],
  ["#productNavCount", new FakeElement()],
  ["#searchInput", new FakeElement()],
  ["#productList", new FakeElement()],
  ["#main", new FakeElement()],
  ["#exportBtn", new FakeElement()],
  ["#importBtn", new FakeElement()],
  ["#importFile", new FakeElement()],
  ["#imageFile", new FakeElement()],
  ["#productModal", new FakeElement()],
  ["#productModalTabs", new FakeElement()],
  ["#productModalTitle", new FakeElement()],
  ["#modalKeywordCount", new FakeElement()],
  ["#modalNegativeCount", new FakeElement()],
  ["#productModalCloseBtn", new FakeElement()],
  ["#productModalCancelBtn", new FakeElement()],
  ["#productModalSaveBtn", new FakeElement()],
  ["#productModalDeleteBtn", new FakeElement()],
  ["#productModalAddKeywordBtn", new FakeElement()],
  ["#productModalAddNegativeBtn", new FakeElement()],
  ["#productModalKeywordEditor", new FakeElement()],
  ["#productModalNegativeEditor", new FakeElement()],
  ["#productModalChooseImageBtn", new FakeElement()],
  ["#productModalRemoveImageBtn", new FakeElement()],
  ["#productModalImageFile", new FakeElement()],
  ["#productModalImagePreview", new FakeElement()],
  ["#productModalSku", new FakeElement()],
  ["#productModalAsin", new FakeElement()],
  ["#storeModal", new FakeElement()],
  ["#storeModalTitle", new FakeElement()],
  ["#storeModalSub", new FakeElement()],
  ["#storeModalName", new FakeElement()],
  ["#storeModalCancelBtn", new FakeElement()],
  ["#storeModalSaveBtn", new FakeElement()],
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
elements.get("#storeModalName").value = "Store 02";
await vm.runInContext("saveStoreModal()", context);
const storesAfterAdd = vm.runInContext("state.stores.length", context);
if (storesAfterAdd !== 2) throw new Error(`Add store failed: ${storesAfterAdd}`);
const activeStoreName = vm.runInContext("currentStore().name", context);
if (activeStoreName !== "Store 02") throw new Error(`Store modal save failed: ${activeStoreName}`);

vm.runInContext("newProduct()", context);
const productsBeforeSave = vm.runInContext("currentStore().products.length", context);
if (productsBeforeSave !== 0) throw new Error(`Modal should not create product before save: ${productsBeforeSave}`);

vm.runInContext("addModalKeyword()", context);
vm.runInContext("productModalDraft.keywords[0].term = 'Reading Glasses for Women'; productModalDraft.keywords[0].exact = 1.25", context);
vm.runInContext("addModalNegative()", context);
vm.runInContext("productModalDraft.negatives[0].term = 'kids'; productModalDraft.negatives[0].phrase = true", context);

elements.get("#productModalSku").value = "YS005";
elements.get("#productModalAsin").value = "B0TEST1234";
await vm.runInContext("saveProductFromModal()", context);

const productsAfterSave = vm.runInContext("currentStore().products.length", context);
if (productsAfterSave !== 1) throw new Error(`Save product failed: ${productsAfterSave}`);

const selectedSku = vm.runInContext("selectedProduct().sku", context);
if (selectedSku !== "YS005") throw new Error(`Selected product mismatch: ${selectedSku}`);

let sidebarHtml = elements.get("#productList").innerHTML;
if (!sidebarHtml.includes("YS005")) throw new Error("Saved product is missing from left sidebar");

let mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("YS005") || !mainHtml.includes("B0TEST1234") || !mainHtml.includes("overview-layout")) {
  throw new Error("Selected product overview is missing from right workspace");
}
if (mainHtml.includes("<input") || mainHtml.includes("添加关键词") || mainHtml.includes("添加否定词")) {
  throw new Error("Right workspace must be read-only");
}

vm.runInContext("setMainView('keywords')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("Reading Glasses for Women") || !mainHtml.includes("$ 1.25")) {
  throw new Error("Keyword read-only view is missing");
}

vm.runInContext("setMainView('negatives')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("kids") || !mainHtml.includes("Negative Phrase")) {
  throw new Error("Negative keyword read-only view is missing");
}

vm.runInContext("editCurrentProduct()", context);
elements.get("#productModalSku").value = "YS005-EDIT";
vm.runInContext("productModalDraft.keywords[0].phrase = 1.35", context);
await vm.runInContext("saveProductFromModal()", context);

const editedSku = vm.runInContext("selectedProduct().sku", context);
if (editedSku !== "YS005-EDIT") throw new Error(`Edit product failed: ${editedSku}`);

vm.runInContext("setMainView('keywords')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("YS005-EDIT") || !mainHtml.includes("$ 1.35")) {
  throw new Error("Edited product is not reflected in keyword read-only view");
}

vm.runInContext("setMainView('overview')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("overview-layout") || !mainHtml.includes("Cloudflare R2")) {
  throw new Error("Rebuilt read-only workspace is missing");
}

console.log("Frontend UI/runtime smoke test passed: store modal, product editor, workspace tabs, read-only views");
