-- PostgreSQL migration for an existing deployment.
-- Existing users must be assigned real phone numbers before enforcing NOT NULL.

ALTER TABLE users ADD COLUMN phone_number VARCHAR(16);
ALTER TABLE users ALTER COLUMN username DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number);

CREATE TABLE IF NOT EXISTS flagged_numbers (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR(16) NOT NULL,
    verdict VARCHAR(5) NOT NULL,
    fake_probability FLOAT NOT NULL,
    bonafide_score FLOAT NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'voice_detection',
    flagged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_flagged_numbers_phone_number
    ON flagged_numbers (phone_number);
