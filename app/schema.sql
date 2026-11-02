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
    -- velinin önceki bakiyesinden bu ödemeye aktarılan tutar (+ borç, - alacak)
    carry_in          REAL NOT NULL DEFAULT 0,
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
    week_start  TEXT,  -- haftanın pazartesisi (YYYY-MM-DD); her hafta ayrı program
    day         INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
    start_min   INTEGER NOT NULL,
    lesson_type TEXT NOT NULL DEFAULT 'inperson' CHECK (lesson_type IN ('online', 'inperson')),
    note        TEXT NOT NULL DEFAULT '',
    gcal_event_id TEXT,                     -- Google Takvim etkinliği (taslak özellik)
    gcal_synced   INTEGER NOT NULL DEFAULT 0 -- 0: Google'a gönderilmeyi bekliyor
);

-- Programda dolu olunan saatler (ders dışı meşguliyet)
CREATE TABLE IF NOT EXISTS busy_blocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT,
    day       INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
    start_min INTEGER NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',  -- 'gcal': Google Takvim'den gelen
    gcal_event_id TEXT
);

-- Kullanıcının eklediği özel günler (yarıyıl tatili, sınav haftası vb.)
CREATE TABLE IF NOT EXISTS special_days (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
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

-- İşlem geçmişi
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    entity     TEXT,
    entity_id  INTEGER,
    ip         TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Ders kayıtları: derste işlenen konu (programdaki derse bağlı ya da elle girilmiş)
CREATE TABLE IF NOT EXISTS lessons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    slot_id      INTEGER UNIQUE REFERENCES schedule_slots(id) ON DELETE SET NULL,
    lesson_date  TEXT NOT NULL,
    start_min    INTEGER,
    lesson_type  TEXT NOT NULL DEFAULT 'inperson' CHECK (lesson_type IN ('online', 'inperson')),
    topic        TEXT NOT NULL DEFAULT '',   -- veli ve öğrenci görür
    homework     TEXT NOT NULL DEFAULT '',   -- veli ve öğrenci görür
    private_note TEXT NOT NULL DEFAULT '',   -- sadece hoca/asistan görür
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_lessons_student ON lessons(student_id, lesson_date);

-- Ders kaydına eklenen dosyalar (PDF, görsel)
CREATE TABLE IF NOT EXISTS lesson_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id  INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    name       TEXT NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_lesson_files_lesson ON lesson_files(lesson_id);

-- Veli / öğrenci portal hesapları (personel hesaplarından tamamen ayrı)
CREATE TABLE IF NOT EXISTS portal_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('parent', 'student')),
    parent_id     INTEGER REFERENCES parents(id) ON DELETE CASCADE,
    student_id    INTEGER REFERENCES students(id) ON DELETE CASCADE,
    active        INTEGER NOT NULL DEFAULT 1,
    last_login    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Google Takvim'e gönderilemeyen silmeler (senkron sırasında Google'dan da silinir)
CREATE TABLE IF NOT EXISTS gcal_trash (
    event_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
