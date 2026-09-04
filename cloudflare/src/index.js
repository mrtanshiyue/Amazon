const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store"
};
const STATE_KEY = "app/state.json";
const MAX_STATE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/api/")) {
        if (!isAuthorized(request, env)) {
          return json({ error: "Cloudflare Access authentication required" }, 401);
        }
        return await handleApi(request, env, url);
      }
      return env.ASSETS.fetch(request);
    } catch (error) {
      console.error(error);
      return json({ error: "Internal server error" }, 500);
    }
  }
};

function isAuthorized(request, env) {
  if (env.ACCESS_REQUIRED === "false") return true;
  return Boolean(request.headers.get("Cf-Access-Authenticated-User-Email"));
}

async function handleApi(request, env, url) {
  if (url.pathname === "/api/health" && request.method === "GET") {
    const state = await env.STORAGE.head(STATE_KEY);
    return json({ ok: true, storage: "r2", initialized: Boolean(state) });
  }

  if (url.pathname === "/api/state" && request.method === "GET") {
    return readState(env);
  }

  if (url.pathname === "/api/state" && request.method === "PUT") {
    return saveState(request, env);
  }

  const imageMatch = url.pathname.match(/^\/api\/products\/([^/]+)\/image$/);
  if (imageMatch) {
    const productId = decodeURIComponent(imageMatch[1]);
    if (request.method === "PUT") return putImage(request, env, productId);
    if (request.method === "DELETE") return deleteImage(env, productId);
  }

  const imageReadMatch = url.pathname.match(/^\/api\/images\/([^/]+)$/);
  if (imageReadMatch && request.method === "GET") {
    return getImage(env, decodeURIComponent(imageReadMatch[1]));
  }

  return json({ error: "Not found" }, 404);
}

async function readState(env) {
  const object = await env.STORAGE.get(STATE_KEY);
  if (!object) {
    const response = json({ version: 2, stores: [] });
    response.headers.set("etag", "");
    return response;
  }

  const text = await object.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    return json({ error: "Stored state is invalid" }, 500);
  }

  const response = json(payload);
  response.headers.set("etag", object.httpEtag);
  return response;
}

async function saveState(request, env) {
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_STATE_BYTES) {
    return json({ error: "State payload too large" }, 413);
  }

  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    return json({ error: "Invalid JSON" }, 400);
  }

  const validated = validateState(payload);
  if (!validated.ok) return json({ error: validated.error }, 400);

  const current = await env.STORAGE.head(STATE_KEY);
  const ifMatch = request.headers.get("if-match");
  const ifNoneMatch = request.headers.get("if-none-match");

  if (current && !ifMatch) {
    return json({ error: "State version required" }, 409);
  }
  if (!current && ifNoneMatch !== "*") {
    return json({ error: "Initial state precondition required" }, 409);
  }

  const conditionalHeaders = new Headers();
  if (current) {
    conditionalHeaders.set("if-match", ifMatch);
  } else {
    conditionalHeaders.set("if-none-match", "*");
  }

  const stored = await env.STORAGE.put(
    STATE_KEY,
    JSON.stringify({ version: 2, stores: validated.stores }),
    {
      onlyIf: conditionalHeaders,
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "private, no-store"
      }
    }
  );

  if (!stored) {
    return json({ error: "State changed on another device. Reload before saving." }, 409);
  }

  const response = json({ ok: true });
  response.headers.set("etag", stored.httpEtag);
  return response;
}

function validateState(payload) {
  if (!payload || !Array.isArray(payload.stores)) {
    return { ok: false, error: "stores must be an array" };
  }
  if (payload.stores.length > 50) return { ok: false, error: "Too many stores" };

  let productCount = 0;
  let rowCount = 0;
  const stores = [];

  for (const rawStore of payload.stores) {
    const id = cleanId(rawStore?.id);
    const name = cleanText(rawStore?.name, 120);
    if (!id || !name) return { ok: false, error: "Invalid store" };

    const rawProducts = Array.isArray(rawStore.products) ? rawStore.products : [];
    productCount += rawProducts.length;
    if (productCount > 5000) return { ok: false, error: "Too many products" };

    const products = [];
    for (const rawProduct of rawProducts) {
      const productId = cleanId(rawProduct?.id);
      if (!productId) return { ok: false, error: "Invalid product id" };

      const rawKeywords = Array.isArray(rawProduct.keywords) ? rawProduct.keywords : [];
      const rawNegatives = Array.isArray(rawProduct.negatives) ? rawProduct.negatives : [];
      rowCount += rawKeywords.length + rawNegatives.length;
      if (rowCount > 100000) return { ok: false, error: "Too many keyword rows" };

      products.push({
        id: productId,
        sku: cleanText(rawProduct?.sku, 240),
        asin: cleanText(rawProduct?.asin, 32).toUpperCase(),
        image: cleanImagePath(rawProduct?.image, productId),
        keywords: rawKeywords.map(rawRow => ({
          id: cleanId(rawRow?.id),
          term: cleanText(rawRow?.term, 500),
          exact: cleanBid(rawRow?.exact),
          phrase: cleanBid(rawRow?.phrase),
          broad: cleanBid(rawRow?.broad)
        })),
        negatives: rawNegatives.map(rawRow => ({
          id: cleanId(rawRow?.id),
          term: cleanText(rawRow?.term, 500),
          exact: Boolean(rawRow?.exact),
          phrase: Boolean(rawRow?.phrase)
        }))
      });

      if (products.at(-1).keywords.some(row => !row.id) ||
          products.at(-1).negatives.some(row => !row.id)) {
        return { ok: false, error: "Invalid keyword id" };
      }
    }

    stores.push({ id, name, products });
  }

  return { ok: true, stores };
}

async function stateHasProduct(env, productId) {
  const object = await env.STORAGE.get(STATE_KEY);
  if (!object) return false;
  try {
    const payload = JSON.parse(await object.text());
    return Array.isArray(payload.stores) && payload.stores.some(store =>
      Array.isArray(store.products) && store.products.some(product => product.id === productId)
    );
  } catch {
    return false;
  }
}

async function putImage(request, env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  if (!(await stateHasProduct(env, productId))) {
    return json({ error: "Product not found. Save the product first." }, 404);
  }

  const contentType = request.headers.get("content-type") || "";
  if (!contentType.startsWith("image/")) {
    return json({ error: "Image content type required" }, 415);
  }

  const bytes = await request.arrayBuffer();
  if (!bytes.byteLength || bytes.byteLength > MAX_IMAGE_BYTES) {
    return json({ error: "Image must be between 1 byte and 5 MB" }, 413);
  }

  await env.STORAGE.put(imageKey(productId), bytes, {
    httpMetadata: {
      contentType: "image/jpeg",
      cacheControl: "private, no-store"
    }
  });

  return json({ ok: true, url: `/api/images/${encodeURIComponent(productId)}` });
}

async function deleteImage(env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  await env.STORAGE.delete(imageKey(productId));
  return json({ ok: true });
}

async function getImage(env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  const object = await env.STORAGE.get(imageKey(productId));
  if (!object) return new Response("Not found", { status: 404 });

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("cache-control", "private, no-store");
  return new Response(object.body, { headers });
}

function imageKey(productId) {
  return `product-images/${productId}.jpg`;
}

function cleanImagePath(value, productId) {
  return typeof value === "string" && value.startsWith("/api/images/")
    ? `/api/images/${encodeURIComponent(productId)}`
    : "";
}

function cleanId(value) {
  if (typeof value !== "string") return "";
  const id = value.trim();
  return id.length > 0 && id.length <= 100 ? id : "";
}

function cleanText(value, max) {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

function cleanBid(value) {
  if (value === "" || value === null || value === undefined) return "";
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 10000
    ? Math.round(number * 100) / 100
    : "";
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: JSON_HEADERS
  });
}
