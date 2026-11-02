PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Veliler
CREATE TABLE IF NOT EXISTS parents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Öğrenciler (velinin çocukları) ve öğrenciye özel tarife
CREATE TABLE IF NOT EXISTS students (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id        INTEGER NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    color            TEXT NOT NULL DEFAULT '#4f7cff',
    online_price     REAL NOT NULL DEFAULT 0,
    inperson_price   REAL NOT NULL DEFAULT 0,
    -- NULL ise ayarlardaki varsayılan komisyon kullanılır
    commission_type  TEXT CHECK (commission_type IN ('percent', 'fixed')),
    commission_value REAL,
    notes            TEXT NOT NULL DEFAULT '',
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Ödemeler (velinin ödemesi gereken kalemler)
CREATE TABLE IF NOT EXISTS payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    description       TEXT NOT NULL DEFAULT '',
    -- saat cinsinden, küsuratlı olabilir (ör. 1.5)
    online_count      REAL NOT NULL DEFAULT 0,
    inperson_count    REAL NOT NULL DEFAULT 0,
    -- ödeme oluşturulurken geçerli olan saat ücretleri
    online_unit       REAL NOT NULL DEFAULT 0,
    inperson_unit     REAL NOT NULL DEFAULT 0,
    amount            REAL NOT NULL,
    due_date          TEXT,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'cancelled')),
    paid_at           TEXT,
    paid_amount       REAL,
    method            TEXT NOT NULL DEFAULT '',
    receipt_path      TEXT,
    receipt_name      TEXT,
    commission_type   TEXT,
    commission_value  REAL,
    commission_amount REAL NOT NULL DEFAULT 0,
    reminder_sent_at  TEXT,
    notes             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id);

-- Asistana yapılan ödemeler (komisyon bakiyesinden düşülür)
CREATE TABLE IF NOT EXISTS payouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    amount     REAL NOT NULL,
    paid_on    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Haftalık program
CREATE TABLE IF NOT EXISTS schedule_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    day         INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
    start_min   INTEGER NOT NULL,
    lesson_type TEXT NOT NULL DEFAULT 'inperson' CHECK (lesson_type IN ('online', 'inperson')),
    note        TEXT NOT NULL DEFAULT ''
);

-- Programda dolu olunan saatler (ders dışı meşguliyet)
CREATE TABLE IF NOT EXISTS busy_blocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    day       INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
    start_min INTEGER NOT NULL,
    label     TEXT NOT NULL DEFAULT ''
);

-- SMS taslakları
CREATE TABLE IF NOT EXISTS message_templates (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    body TEXT NOT NULL
);

-- Ortak notlar
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body       TEXT NOT NULL,
    pinned     INTEGER NOT NULL DEFAULT 0,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
