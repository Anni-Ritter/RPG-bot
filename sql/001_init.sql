-- SQLite reference schema for Selin & Tori RPG Bot V5 Full.
-- Normal startup does NOT require running this file: SQLAlchemy creates the tables automatically.
-- The real Bothost database lives at /app/data/selin_tori.db.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id BIGINT NOT NULL UNIQUE,
    display_name VARCHAR(120),
    character_name VARCHAR(80) NOT NULL DEFAULT 'Селин',
    familiar_name VARCHAR(80) NOT NULL DEFAULT 'Тори',
    xp INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    strength INTEGER NOT NULL DEFAULT 0,
    willpower INTEGER NOT NULL DEFAULT 0,
    tori_bond INTEGER NOT NULL DEFAULT 0,
    atelier_dust INTEGER NOT NULL DEFAULT 0,
    trial_chests INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    stat_date DATE NOT NULL,
    meals INTEGER NOT NULL DEFAULT 0,
    snacks INTEGER NOT NULL DEFAULT 0,
    drinks INTEGER NOT NULL DEFAULT 0,
    plain_drinks INTEGER NOT NULL DEFAULT 0,
    caloric_drinks INTEGER NOT NULL DEFAULT 0,
    energy_drinks INTEGER NOT NULL DEFAULT 0,
    water INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    step_tier VARCHAR(40),
    step_reward_xp INTEGER NOT NULL DEFAULT 0,
    step_reward_coins INTEGER NOT NULL DEFAULT 0,
    temptation_count INTEGER NOT NULL DEFAULT 0,
    rewarded_temptations INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, stat_date)
);

CREATE TABLE IF NOT EXISTS game_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(80) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'user',
    xp_delta INTEGER NOT NULL DEFAULT 0,
    coins_delta INTEGER NOT NULL DEFAULT 0,
    atelier_dust_delta INTEGER NOT NULL DEFAULT 0,
    payload JSON NOT NULL DEFAULT '{}',
    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS custom_quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    title VARCHAR(240) NOT NULL,
    difficulty VARCHAR(20) NOT NULL DEFAULT 'normal',
    reward_xp INTEGER NOT NULL,
    reward_coins INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS workout_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    workout_date DATE NOT NULL,
    kind VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'available',
    started_at DATETIME,
    completed_at DATETIME,
    UNIQUE(user_id, workout_date, kind)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    kind VARCHAR(60) NOT NULL,
    scheduled_at DATETIME NOT NULL,
    sent_at DATETIME,
    dedupe_key VARCHAR(160) NOT NULL UNIQUE,
    payload JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scroll_definitions (
    id VARCHAR(80) PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    rarity VARCHAR(20) NOT NULL,
    item_type VARCHAR(30) NOT NULL,
    collection VARCHAR(120) NOT NULL,
    chapter INTEGER NOT NULL,
    tags JSON NOT NULL DEFAULT '[]',
    prompt TEXT,
    source VARCHAR(30) NOT NULL,
    is_secret BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scroll_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    scroll_id VARCHAR(80) NOT NULL REFERENCES scroll_definitions(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'sealed',
    is_favorite BOOLEAN NOT NULL DEFAULT 0,
    obtained_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revealed_at DATETIME,
    generated_at DATETIME,
    UNIQUE(user_id, scroll_id)
);

CREATE TABLE IF NOT EXISTS scroll_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    scroll_id VARCHAR(80) NOT NULL REFERENCES scroll_definitions(id) ON DELETE CASCADE,
    telegram_file_id VARCHAR(512) NOT NULL,
    telegram_file_unique_id VARCHAR(255),
    is_primary BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chest_opens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    chest_type VARCHAR(40) NOT NULL DEFAULT 'trial',
    rarity VARCHAR(20) NOT NULL,
    scroll_id VARCHAR(80),
    duplicate BOOLEAN NOT NULL DEFAULT 0,
    atelier_dust_awarded INTEGER NOT NULL DEFAULT 0,
    opened_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gacha_state (
    user_id INTEGER PRIMARY KEY REFERENCES user_profiles(id) ON DELETE CASCADE,
    season_number INTEGER NOT NULL DEFAULT 1,
    common_streak INTEGER NOT NULL DEFAULT 0,
    season_chests_opened INTEGER NOT NULL DEFAULT 0,
    season_epic_plus_hit BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS story_progress (
    user_id INTEGER PRIMARY KEY REFERENCES user_profiles(id) ON DELETE CASCADE,
    season_number INTEGER NOT NULL DEFAULT 1,
    season_started_on DATE NOT NULL,
    last_viewed_day INTEGER NOT NULL DEFAULT 0,
    flags JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS pending_temptations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'waiting',
    reward_eligible BOOLEAN NOT NULL DEFAULT 0,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    due_at DATETIME NOT NULL,
    resolved_at DATETIME
);

CREATE TABLE IF NOT EXISTS user_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    code VARCHAR(80) NOT NULL,
    title VARCHAR(160) NOT NULL,
    reward_text VARCHAR(240) NOT NULL DEFAULT '',
    unlocked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code)
);
