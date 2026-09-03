const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
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
    const db = await env.DB.prepare("SELECT 1 AS ok").first();
    return json({ ok: db?.ok === 1, storage: { d1: true, r2: true } });
  }

  if (url.pathname === "/api/state" && request.method === "GET") {
    return json(await readState(env));
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
  const [storesResult, productsResult, keywordsResult, negativesResult] = await Promise.all([
    env.DB.prepare("SELECT id, name FROM stores ORDER BY sort_order, created_at").all(),
    env.DB.prepare("SELECT id, store_id, sku, asin, has_image FROM products ORDER BY sort_order, created_at").all(),
    env.DB.prepare("SELECT id, product_id, term, exact_bid, phrase_bid, broad_bid FROM keywords ORDER BY sort_order, created_at").all(),
    env.DB.prepare("SELECT id, product_id, term, negative_exact, negative_phrase FROM negative_keywords ORDER BY sort_order, created_at").all()
  ]);

  const stores = storesResult.results.map(row => ({ id: row.id, name: row.name, products: [], selectedId: null }));
  const storeMap = new Map(stores.map(store => [store.id, store]));
  const productMap = new Map();

  for (const row of productsResult.results) {
    const product = {
      id: row.id,
      sku: row.sku || "",
      asin: row.asin || "",
      image: row.has_image ? `/api/images/${encodeURIComponent(row.id)}` : "",
      keywords: [],
      negatives: []
    };
    productMap.set(product.id, product);
    const store = storeMap.get(row.store_id);
    if (store) {
      store.products.push(product);
      if (!store.selectedId) store.selectedId = product.id;
    }
  }

  for (const row of keywordsResult.results) {
    const product = productMap.get(row.product_id);
    if (!product) continue;
    product.keywords.push({
      id: row.id,
      term: row.term || "",
      exact: row.exact_bid ?? "",
      phrase: row.phrase_bid ?? "",
      broad: row.broad_bid ?? ""
    });
  }

  for (const row of negativesResult.results) {
    const product = productMap.get(row.product_id);
    if (!product) continue;
    product.negatives.push({
      id: row.id,
      term: row.term || "",
      exact: Boolean(row.negative_exact),
      phrase: Boolean(row.negative_phrase)
    });
  }

  return { version: 2, stores };
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

  const state = validateState(payload);
  if (!state.ok) return json({ error: state.error }, 400);

  const statements = [
    env.DB.prepare("DELETE FROM negative_keywords"),
    env.DB.prepare("DELETE FROM keywords"),
    env.DB.prepare("DELETE FROM products"),
    env.DB.prepare("DELETE FROM stores")
  ];

  state.stores.forEach((store, storeIndex) => {
    statements.push(
      env.DB.prepare("INSERT INTO stores (id, name, sort_order) VALUES (?, ?, ?)")
        .bind(store.id, store.name, storeIndex)
    );

    store.products.forEach((product, productIndex) => {
      statements.push(
        env.DB.prepare("INSERT INTO products (id, store_id, sku, asin, has_image, sort_order) VALUES (?, ?, ?, ?, ?, ?)")
          .bind(product.id, store.id, product.sku, product.asin, product.hasImage ? 1 : 0, productIndex)
      );

      product.keywords.forEach((row, rowIndex) => {
        statements.push(
          env.DB.prepare("INSERT INTO keywords (id, product_id, term, exact_bid, phrase_bid, broad_bid, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .bind(row.id, product.id, row.term, row.exact, row.phrase, row.broad, rowIndex)
        );
      });

      product.negatives.forEach((row, rowIndex) => {
        statements.push(
          env.DB.prepare("INSERT INTO negative_keywords (id, product_id, term, negative_exact, negative_phrase, sort_order) VALUES (?, ?, ?, ?, ?, ?)")
            .bind(row.id, product.id, row.term, row.exact ? 1 : 0, row.phrase ? 1 : 0, rowIndex)
        );
      });
    });
  });

  await env.DB.batch(statements);
  return json({ ok: true });
}

function validateState(payload) {
  if (!payload || !Array.isArray(payload.stores)) return { ok: false, error: "stores must be an array" };
  if (payload.stores.length > 50) return { ok: false, error: "Too many stores" };

  let productCount = 0;
  let keywordCount = 0;
  const stores = [];

  for (const rawStore of payload.stores) {
    const id = cleanId(rawStore?.id);
    const name = cleanText(rawStore?.name, 120);
    if (!id || !name) return { ok: false, error: "Invalid store" };
    const rawProducts = Array.isArray(rawStore.products) ? rawStore.products : [];
    productCount += rawProducts.length;
    if (productCount > 5000) return { ok: false, error: "Too many products" };

    const store = { id, name, products: [] };
    for (const rawProduct of rawProducts) {
      const productId = cleanId(rawProduct?.id);
      if (!productId) return { ok: false, error: "Invalid product id" };
      const rawKeywords = Array.isArray(rawProduct.keywords) ? rawProduct.keywords : [];
      const rawNegatives = Array.isArray(rawProduct.negatives) ? rawProduct.negatives : [];
      keywordCount += rawKeywords.length + rawNegatives.length;
      if (keywordCount > 100000) return { ok: false, error: "Too many keyword rows" };

      const product = {
        id: productId,
        sku: cleanText(rawProduct?.sku, 240),
        asin: cleanText(rawProduct?.asin, 32).toUpperCase(),
        hasImage: typeof rawProduct?.image === "string" && rawProduct.image.startsWith("/api/images/"),
        keywords: [],
        negatives: []
      };

      for (const rawRow of rawKeywords) {
        const rowId = cleanId(rawRow?.id);
        if (!rowId) return { ok: false, error: "Invalid keyword id" };
        product.keywords.push({
          id: rowId,
          term: cleanText(rawRow?.term, 500),
          exact: cleanBid(rawRow?.exact),
          phrase: cleanBid(rawRow?.phrase),
          broad: cleanBid(rawRow?.broad)
        });
      }

      for (const rawRow of rawNegatives) {
        const rowId = cleanId(rawRow?.id);
        if (!rowId) return { ok: false, error: "Invalid negative keyword id" };
        product.negatives.push({
          id: rowId,
          term: cleanText(rawRow?.term, 500),
          exact: Boolean(rawRow?.exact),
          phrase: Boolean(rawRow?.phrase)
        });
      }

      store.products.push(product);
    }
    stores.push(store);
  }

  return { ok: true, stores };
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
  if (value === "" || value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 10000 ? Math.round(number * 100) / 100 : null;
}

async function putImage(request, env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  const product = await env.DB.prepare("SELECT id FROM products WHERE id = ?").bind(productId).first();
  if (!product) return json({ error: "Product not found. Save the product first." }, 404);

  const contentType = request.headers.get("content-type") || "";
  if (!contentType.startsWith("image/")) return json({ error: "Image content type required" }, 415);

  const bytes = await request.arrayBuffer();
  if (!bytes.byteLength || bytes.byteLength > MAX_IMAGE_BYTES) {
    return json({ error: "Image must be between 1 byte and 5 MB" }, 413);
  }

  const key = imageKey(productId);
  await env.IMAGES.put(key, bytes, {
    httpMetadata: { contentType: "image/jpeg", cacheControl: "private, no-store" }
  });
  await env.DB.prepare("UPDATE products SET has_image = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
    .bind(productId).run();

  return json({ ok: true, url: `/api/images/${encodeURIComponent(productId)}?v=${Date.now()}` });
}

async function deleteImage(env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  await Promise.all([
    env.IMAGES.delete(imageKey(productId)),
    env.DB.prepare("UPDATE products SET has_image = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
      .bind(productId).run()
  ]);
  return json({ ok: true });
}

async function getImage(env, productId) {
  if (!cleanId(productId)) return json({ error: "Invalid product id" }, 400);
  const object = await env.IMAGES.get(imageKey(productId));
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

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}
