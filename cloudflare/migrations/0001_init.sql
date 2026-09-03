PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  store_id TEXT NOT NULL,
  sku TEXT NOT NULL DEFAULT '',
  asin TEXT NOT NULL DEFAULT '',
  has_image INTEGER NOT NULL DEFAULT 0 CHECK (has_image IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_asin ON products(asin);

CREATE TABLE IF NOT EXISTS keywords (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  term TEXT NOT NULL DEFAULT '',
  exact_bid REAL,
  phrase_bid REAL,
  broad_bid REAL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_keywords_product ON keywords(product_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_keywords_term ON keywords(term);

CREATE TABLE IF NOT EXISTS negative_keywords (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  term TEXT NOT NULL DEFAULT '',
  negative_exact INTEGER NOT NULL DEFAULT 0 CHECK (negative_exact IN (0,1)),
  negative_phrase INTEGER NOT NULL DEFAULT 0 CHECK (negative_phrase IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_negative_keywords_product ON negative_keywords(product_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_negative_keywords_term ON negative_keywords(term);
