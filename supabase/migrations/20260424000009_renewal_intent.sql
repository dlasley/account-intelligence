-- Add 'renewal' to outreach_drafts.intent CHECK constraint.
-- Postgres doesn't support ALTER CONSTRAINT in-place; drop and re-add.

ALTER TABLE outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_intent_check;

ALTER TABLE outreach_drafts
    ADD CONSTRAINT outreach_drafts_intent_check
    CHECK (intent IN ('check_in', 'expansion', 'renewal', 'custom'));
