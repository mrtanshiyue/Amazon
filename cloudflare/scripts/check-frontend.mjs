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
  'data-main-view="overview"',
  'data-keyword-term',
  'keyword-status confirmed',
  'keyword-status watching',
  'id="productModalBulkKeywordBtn"',
  'id="bulkKeywordModal"',
  'id="bulkKeywordInput"',
  'id="bulkKeywordImportBtn"',
  'overview-mini-table',
  '确认关键词',
  '待观察关键词',
  '否定关键词',
  'overview-filter-toolbar',
  'data-keyword-filter="confirmed"',
  'data-keyword-filter="watching"',
  'data-negative-filter="phrase"',
  'data-negative-filter="exact"',
  'name="bulkKeywordStatus" value="watching"',
  'name="bulkKeywordStatus" value="confirmed"',
  'data-copy-group="confirmed"',
  'data-copy-group="watching"',
  'data-copy-group="negative"'
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
    this.checked = false;
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
  ["#productModalBulkKeywordBtn", new FakeElement()],
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
  ["#bulkKeywordModal", new FakeElement()],
  ["#bulkKeywordInput", new FakeElement()],
  ["#bulkKeywordCount", new FakeElement()],
  ["#bulkKeywordCancelBtn", new FakeElement()],
  ["#bulkKeywordImportBtn", new FakeElement()],
  ["#toast", new FakeElement()]
]);

const bulkStatusWatching = new FakeElement();
bulkStatusWatching.value = "watching";
bulkStatusWatching.checked = true;
const bulkStatusConfirmed = new FakeElement();
bulkStatusConfirmed.value = "confirmed";
bulkStatusConfirmed.checked = false;

const document = {
  documentElement: { dataset: {} },
  querySelector(selector) {
    if (selector === 'input[name="bulkKeywordStatus"][value="watching"]') return bulkStatusWatching;
    if (selector === 'input[name="bulkKeywordStatus"][value="confirmed"]') return bulkStatusConfirmed;
    if (selector === 'input[name="bulkKeywordStatus"]:checked') {
      return bulkStatusConfirmed.checked ? bulkStatusConfirmed : bulkStatusWatching;
    }
    return elements.get(selector) || new FakeElement();
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return new FakeElement(); }
};

let revision = 0;
let cloudState = { version: 3, stores: [] };
const responseHeaders = () => ({ get: () => null });

const fetch = async (url, options = {}) => {
  const method = options.method || "GET";
  if (String(url).endsWith("/api/state") && method === "GET") {
    return {
      ok: true,
      status: 200,
      headers: responseHeaders(),
      json: async () => structuredClone(cloudState)
    };
  }
  if (String(url).endsWith("/api/state") && method === "PUT") {
    cloudState = JSON.parse(options.body);
    revision += 1;
    return {
      ok: true,
      status: 200,
      headers: responseHeaders(),
      json: async () => ({ ok: true, updatedAt: "2026-09-04T00:00:00.000Z" })
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
let clipboardText = "";
const navigator = {
  clipboard: {
    writeText: async value => { clipboardText = String(value); }
  }
};
const context = vm.createContext({
  console,
  document,
  navigator,
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

await vm.runInContext("Promise.all([saveCloudNow(), saveCloudNow()])", context);
if (revision < 2) throw new Error("Serialized cloud save queue did not execute both writes");

const persistedAfterQueue = JSON.stringify(cloudState.stores);
const expectedAfterQueue = vm.runInContext("JSON.stringify(cloudStatePayload().stores)", context);
if (persistedAfterQueue !== expectedAfterQueue) throw new Error("Cloud state verification failed after queued saves");

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
const defaultKeywordStatus = vm.runInContext("productModalDraft.keywords[0].status", context);
if (defaultKeywordStatus !== "watching") throw new Error(`New keyword default status should be watching, got ${defaultKeywordStatus}`);

elements.get("#bulkKeywordInput").value = [
  "  Blue   Light Readers  ",
  "computer readers",
  "",
  "BLUE LIGHT READERS",
  "Reading Glasses for Women"
].join("\n");
vm.runInContext("importBulkKeywords()", context);

const bulkTerms = vm.runInContext("productModalDraft.keywords.map(row => row.term)", context);
if (bulkTerms.length !== 3) throw new Error(`Bulk import expected 3 unique total keywords, got ${bulkTerms.length}`);
if (!bulkTerms.includes("Blue Light Readers") || !bulkTerms.includes("computer readers")) {
  throw new Error("Bulk import normalization failed");
}
const importedStatuses = vm.runInContext(
  "productModalDraft.keywords.filter(row => row.term !== 'Reading Glasses for Women').map(row => row.status)",
  context
);
if (importedStatuses.some(status => status !== "watching")) {
  throw new Error("Bulk imported keywords must default to watching");
}

bulkStatusWatching.checked = false;
bulkStatusConfirmed.checked = true;
elements.get("#bulkKeywordInput").value = [
  "fashion readers",
  "Reading Glasses for Women"
].join("\n");
vm.runInContext("importBulkKeywords()", context);

const confirmedImported = vm.runInContext(
  "productModalDraft.keywords.find(row => row.term === 'fashion readers')?.status",
  context
);
if (confirmedImported !== "confirmed") {
  throw new Error(`Bulk confirmed status failed: ${confirmedImported}`);
}
const existingStatusAfterConfirmedImport = vm.runInContext(
  "productModalDraft.keywords.find(row => row.term === 'Reading Glasses for Women')?.status",
  context
);
if (existingStatusAfterConfirmedImport !== "watching") {
  throw new Error("Bulk import must not modify an existing keyword status");
}
vm.runInContext("addModalNegative()", context);
vm.runInContext("productModalDraft.negatives[0].term = 'kids'; productModalDraft.negatives[0].phrase = true", context);

elements.get("#productModalSku").value = "YS005";
elements.get("#productModalAsin").value = "B0TEST1234";
vm.runInContext("saveProductFromModal()", context);

const draftClosedImmediately = vm.runInContext("productModalDraft === null", context);
if (!draftClosedImmediately) throw new Error("Product editor must close immediately after save click");

const productsAfterSave = vm.runInContext("currentStore().products.length", context);
if (productsAfterSave !== 1) throw new Error(`Save product failed: ${productsAfterSave}`);

const selectedSku = vm.runInContext("selectedProduct().sku", context);
if (selectedSku !== "YS005") throw new Error(`Selected product mismatch: ${selectedSku}`);

await new Promise(resolve => setTimeout(resolve, 40));

const persistedBulkTerms = cloudState.stores
  .flatMap(store => store.products || [])
  .flatMap(product => product.keywords || [])
  .map(row => row.term);
if (!persistedBulkTerms.includes("Blue Light Readers") || !persistedBulkTerms.includes("computer readers") || !persistedBulkTerms.includes("fashion readers")) {
  throw new Error("Bulk imported keywords did not persist to cloud state");
}
const persistedFashionStatus = cloudState.stores
  .flatMap(store => store.products || [])
  .flatMap(product => product.keywords || [])
  .find(row => row.term === "fashion readers")?.status;
if (persistedFashionStatus !== "confirmed") {
  throw new Error(`Confirmed bulk import did not persist: ${persistedFashionStatus}`);
}

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
if (!mainHtml.includes("Reading Glasses for Women") || !mainHtml.includes("$ 1.25") || !mainHtml.includes("待观察")) {
  throw new Error("Keyword read-only view or watching label is missing");
}

vm.runInContext("setMainView('negatives')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("kids") || !mainHtml.includes("Negative Phrase")) {
  throw new Error("Negative keyword read-only view is missing");
}

vm.runInContext("editCurrentProduct()", context);
elements.get("#productModalSku").value = "YS005-EDIT";
vm.runInContext("productModalDraft.keywords[0].phrase = 1.35; productModalDraft.keywords[0].status = 'confirmed'", context);
vm.runInContext("saveProductFromModal()", context);

const editDraftClosedImmediately = vm.runInContext("productModalDraft === null", context);
if (!editDraftClosedImmediately) throw new Error("Product editor must close immediately after edit save");

const editedSku = vm.runInContext("selectedProduct().sku", context);
if (editedSku !== "YS005-EDIT") throw new Error(`Edit product failed: ${editedSku}`);

vm.runInContext("setMainView('keywords')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("YS005-EDIT") || !mainHtml.includes("$ 1.35") || !mainHtml.includes("确认")) {
  throw new Error("Edited product or confirmed keyword label is not reflected in keyword read-only view");
}

await new Promise(resolve => setTimeout(resolve, 40));

const persistedKeywordStatus = cloudState.stores
  .flatMap(store => store.products || [])
  .flatMap(product => product.keywords || [])
  .find(row => row.term === "Reading Glasses for Women")?.status;
if (persistedKeywordStatus !== "confirmed") {
  throw new Error(`Keyword status did not persist to cloud state: ${persistedKeywordStatus}`);
}

clipboardText = "";
await vm.runInContext("copyKeywordGroup('confirmed')", context);
const confirmedCopied = clipboardText.split("\n");
if (
  confirmedCopied.length !== 2 ||
  !confirmedCopied.includes("Reading Glasses for Women") ||
  !confirmedCopied.includes("fashion readers")
) {
  throw new Error(`Confirmed copy format/content failed: ${clipboardText}`);
}

clipboardText = "";
await vm.runInContext("copyKeywordGroup('watching')", context);
const watchingCopied = clipboardText.split("\n");
if (
  watchingCopied.length !== 2 ||
  !watchingCopied.includes("Blue Light Readers") ||
  !watchingCopied.includes("computer readers")
) {
  throw new Error(`Watching copy format/content failed: ${clipboardText}`);
}

clipboardText = "";
vm.runInContext("negativeOverviewFilter = 'phrase'", context);
await vm.runInContext("copyKeywordGroup('negative')", context);
if (clipboardText !== "kids") {
  throw new Error(`Filtered negative copy format/content failed: ${clipboardText}`);
}

clipboardText = "";
vm.runInContext("negativeOverviewFilter = 'exact'", context);
await vm.runInContext("copyKeywordGroup('negative')", context);
if (clipboardText !== "") {
  throw new Error(`Exact filtered negative copy should be empty: ${clipboardText}`);
}

vm.runInContext("negativeOverviewFilter = 'all'", context);

vm.runInContext("setMainView('overview')", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("overview-layout") || !mainHtml.includes("Cloudflare R2")) {
  throw new Error("Rebuilt read-only workspace is missing");
}
if (!mainHtml.includes("确认关键词") || !mainHtml.includes("待观察关键词") || !mainHtml.includes("否定关键词")) {
  throw new Error("Overview must show confirmed, watching, and negative keyword tables");
}
if (!mainHtml.includes("overview-mini-table") || !mainHtml.includes("Exact") || !mainHtml.includes("Phrase") || !mainHtml.includes("Broad")) {
  throw new Error("Overview keyword tables are missing expected columns");
}
if (!mainHtml.includes('data-keyword-term')) {
  throw new Error("Overview keyword term-only selection structure is missing");
}
if (!mainHtml.includes("Reading Glasses for Women") || !mainHtml.includes("fashion readers")) {
  throw new Error("Confirmed/watching keyword rows are missing");
}
if (!mainHtml.includes("kids") || !mainHtml.includes("词组")) {
  throw new Error("Negative keyword table or match mode is missing");
}

vm.runInContext("keywordOverviewFilter = 'confirmed'; renderMain()", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("确认关键词") || mainHtml.includes("待观察关键词")) {
  throw new Error("Confirmed keyword table filter failed");
}

vm.runInContext("keywordOverviewFilter = 'watching'; renderMain()", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("待观察关键词") || mainHtml.includes("确认关键词")) {
  throw new Error("Watching keyword table filter failed");
}

vm.runInContext("keywordOverviewFilter = 'all'; negativeOverviewFilter = 'phrase'; renderMain()", context);
mainHtml = elements.get("#main").innerHTML;
if (!mainHtml.includes("kids") || !mainHtml.includes("词组")) {
  throw new Error("Negative phrase filter failed");
}

vm.runInContext("negativeOverviewFilter = 'exact'; renderMain()", context);
mainHtml = elements.get("#main").innerHTML;
if (mainHtml.includes(">kids<")) {
  throw new Error("Negative exact filter should exclude phrase-only row");
}

console.log("Frontend UI/runtime smoke test passed: restored overview filters, group copy, three tables, background save, bulk import, verified R2 persistence");
