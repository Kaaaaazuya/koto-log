-- 育児記録エージェント スキーマ（Design Doc §6）
-- 時刻はすべて JST 絶対時刻の ISO8601 文字列で保存する。

CREATE TABLE IF NOT EXISTS children (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name_alias TEXT NOT NULL,          -- 実名は保持しない（NFR-4）
    birthday   TEXT                    -- DATE (ISO8601)
);

CREATE TABLE IF NOT EXISTS records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id   INTEGER NOT NULL REFERENCES children(id),
    type       TEXT NOT NULL,          -- feeding / sleep / diaper / temp ...
    sub_type   TEXT,                   -- 母乳 / ミルク / 左 / 右 ...
    amount     REAL,                   -- 120
    unit       TEXT,                   -- ml
    started_at TEXT NOT NULL,          -- JST 絶対時刻 (ISO8601)
    ended_at   TEXT,                   -- 睡眠など区間がある場合
    note       TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_child_type_started
    ON records (child_id, type, started_at);

CREATE TABLE IF NOT EXISTS sessions (
    line_user_id   TEXT PRIMARY KEY,
    last_record_id INTEGER REFERENCES records(id),
    recent_context TEXT,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
