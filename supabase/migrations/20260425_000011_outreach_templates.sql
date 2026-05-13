-- Extend outreach_drafts.intent CHECK to add 'renewal'
ALTER TABLE outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_intent_check;

ALTER TABLE outreach_drafts
    ADD CONSTRAINT outreach_drafts_intent_check
    CHECK (intent IN ('check_in', 'expansion', 'renewal', 'custom'));


-- Extend outreach_drafts.generated_by CHECK to add 'template'
ALTER TABLE outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_generated_by_check;

ALTER TABLE outreach_drafts
    ADD CONSTRAINT outreach_drafts_generated_by_check
    CHECK (generated_by IN ('llm', 'human', 'template'));


-- Record which template initialized the draft (enables "Reset to template" UX)
ALTER TABLE outreach_drafts
    ADD COLUMN template_id text NULL;
