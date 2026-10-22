-- Milestone M0 schema: 7 core durability and provenance tables.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    production_root TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter_key TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    chapter_id TEXT NOT NULL,
    title TEXT NOT NULL,
    audio_duration_seconds REAL,
    audio_total_samples INTEGER,
    total_frames INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(episode_id, chapter_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    chapter_id TEXT,
    role TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes_size INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    chapter_id TEXT,
    scope TEXT NOT NULL,
    target_path TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    evidence_path TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    chapter_id TEXT,
    workflow_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    pid INTEGER,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    step_name TEXT NOT NULL,
    step_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_output_path TEXT,
    output_hash TEXT,
    external_target_id TEXT,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_events_idempotency
    ON run_events(idempotency_key, status);

CREATE INDEX IF NOT EXISTS idx_run_events_step_name
    ON run_events(step_name, status);

CREATE TABLE IF NOT EXISTS file_operations (
    op_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    step_name TEXT NOT NULL,
    op_type TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT,
    dest_path TEXT,
    dest_sha256 TEXT,
    bytes_size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

