PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO metadata(key, value) VALUES ('revision', '0');

CREATE TABLE IF NOT EXISTS demo_runs (
    id TEXT PRIMARY KEY,
    run_number INTEGER NOT NULL UNIQUE,
    engine TEXT NOT NULL CHECK(engine IN ('naive', 'temporal')),
    status TEXT NOT NULL CHECK(status IN ('active', 'closing', 'closed')),
    seed INTEGER NOT NULL,
    join_code TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    closed_at REAL,
    summary_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON demo_runs(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES demo_runs(id),
    sequence_number INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('audience', 'synthetic')),
    supporter_alias TEXT NOT NULL,
    section TEXT NOT NULL,
    price_pence INTEGER NOT NULL,
    engine TEXT NOT NULL CHECK(engine IN ('naive', 'temporal')),
    workflow_id TEXT,
    orchestration_status TEXT NOT NULL DEFAULT 'processing',
    last_message TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(run_id, sequence_number)
);
CREATE INDEX IF NOT EXISTS orders_run_id ON orders(run_id, sequence_number);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    run_id TEXT NOT NULL REFERENCES demo_runs(id),
    event_type TEXT NOT NULL,
    step TEXT NOT NULL,
    message TEXT NOT NULL,
    attempt INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_order_id ON order_events(order_id, id);

CREATE TABLE IF NOT EXISTS reservations (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL UNIQUE,
    seat TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    payment_id TEXT NOT NULL UNIQUE,
    amount_pence INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    ticket_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS loyalty_entries (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    loyalty_id TEXT NOT NULL UNIQUE,
    points INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmations (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    confirmation_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS service_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    run_id TEXT NOT NULL REFERENCES demo_runs(id),
    service TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fault_settings (
    run_id TEXT PRIMARY KEY REFERENCES demo_runs(id),
    reservation_failure_pct INTEGER NOT NULL DEFAULT 0,
    payment_failure_pct INTEGER NOT NULL DEFAULT 0,
    ticket_failure_pct INTEGER NOT NULL DEFAULT 0,
    card_decline_pct INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 180,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS crash_tokens (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES demo_runs(id),
    target_source TEXT NOT NULL,
    target_order_id TEXT,
    consumed_order_id TEXT,
    created_at REAL NOT NULL,
    consumed_at REAL
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_type TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
