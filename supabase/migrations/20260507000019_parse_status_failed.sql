-- Rename ParseStatus value parse_failed → failed in raw_inbound_events.
-- Aligns with the RegenJobStatus.FAILED pattern; removes the redundant "parse_" prefix.
-- ADR naming-opacity audit 2026-05-07.

-- Update any existing rows that carry the old value before changing the constraint.
UPDATE raw_inbound_events SET parse_status = 'failed' WHERE parse_status = 'parse_failed';

ALTER TABLE raw_inbound_events
    DROP CONSTRAINT raw_inbound_events_parse_status_check;

ALTER TABLE raw_inbound_events
    ADD CONSTRAINT raw_inbound_events_parse_status_check
    CHECK (parse_status = ANY (ARRAY['pending'::text, 'processed'::text, 'failed'::text, 'skipped'::text]));
