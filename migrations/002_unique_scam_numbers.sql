-- Migration 002: Unique Scam Numbers and Tracking
-- Consolidates duplicate scam number records, updates schema with fake_detection_count and last_flagged_at,
-- and enforces unique constraint on phone_number.

-- 1. Add fake_detection_count if not exists
ALTER TABLE flagged_numbers ADD COLUMN IF NOT EXISTS fake_detection_count INTEGER NOT NULL DEFAULT 1;

-- 2. Rename or add last_flagged_at column
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'flagged_numbers' AND column_name = 'flagged_at'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'flagged_numbers' AND column_name = 'last_flagged_at'
    ) THEN
        ALTER TABLE flagged_numbers RENAME COLUMN flagged_at TO last_flagged_at;
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'flagged_numbers' AND column_name = 'last_flagged_at'
    ) THEN
        ALTER TABLE flagged_numbers ADD COLUMN last_flagged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
    END IF;
END $$;

-- 3. Consolidate duplicate phone numbers before enforcing unique constraint:
-- For any phone number with multiple entries, determine the total detections, update the newest row, and delete older rows.
WITH ranked_scams AS (
    SELECT id, phone_number,
           ROW_NUMBER() OVER (PARTITION BY phone_number ORDER BY last_flagged_at DESC, id DESC) as rn,
           COUNT(*) OVER (PARTITION BY phone_number) as total_count,
           SUM(fake_detection_count) OVER (PARTITION BY phone_number) as total_detections
    FROM flagged_numbers
),
scams_to_update AS (
    SELECT id, GREATEST(total_count, total_detections) as new_count
    FROM ranked_scams
    WHERE rn = 1 AND total_count > 1
)
UPDATE flagged_numbers f
SET fake_detection_count = s.new_count
FROM scams_to_update s
WHERE f.id = s.id;

WITH ranked_scams AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY phone_number ORDER BY last_flagged_at DESC, id DESC) as rn
    FROM flagged_numbers
)
DELETE FROM flagged_numbers
WHERE id IN (
    SELECT id FROM ranked_scams WHERE rn > 1
);

-- 4. Replace non-unique index with unique index
DROP INDEX IF EXISTS ix_flagged_numbers_phone_number;
CREATE UNIQUE INDEX IF NOT EXISTS uq_flagged_numbers_phone_number ON flagged_numbers (phone_number);
