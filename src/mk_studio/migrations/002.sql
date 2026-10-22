-- Milestone M1+ expanded scope schema: persistent conversational studio, AI providers, browser tasks, and Mac control audit log.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    active_episode_id TEXT NOT NULL DEFAULT 'EPISODE_002',
    active_chapter_id TEXT NOT NULL DEFAULT 'CH01',
    active_project_path TEXT NOT NULL DEFAULT '',
    active_browser_session_id TEXT NOT NULL DEFAULT 'default-chrome-cdp',
    active_mac_profile TEXT NOT NULL DEFAULT 'editor_automation',
    active_provider_id TEXT NOT NULL DEFAULT 'codex_cli',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider_id TEXT,
    job_id TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    citations_json TEXT NOT NULL DEFAULT '[]',
    token_usage_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at ASC);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(conversation_id),
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    logs_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_configs (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    keychain_service TEXT NOT NULL DEFAULT '',
    budget_limit_usd REAL NOT NULL DEFAULT 5.0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    calls_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_checked_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS browser_tasks (
    task_id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(conversation_id),
    session_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    action_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_takeover INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mac_actions (
    action_id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(conversation_id),
    profile_name TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    target_app TEXT NOT NULL DEFAULT '',
    command_or_script TEXT NOT NULL DEFAULT '',
    expected_postcondition TEXT NOT NULL DEFAULT '',
    observed_postcondition TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

