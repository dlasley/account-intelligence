-- Migration 000028: expand accounts.vertical taxonomy from 7 Elicit-shaped values to 13
-- standard B2B industry buckets.
--
-- Rationale: the original allow-list (pharma, academia, policy, tech, medtech, cpg, other)
-- reflected Elicit's specific customer mix. The platform now serves customers in any
-- industry, and the Elicit-flavoured values forced demo accounts to either misclassify
-- (everyone into "tech") or fall back to the "other" escape hatch. New taxonomy is the
-- standard B2B industry shape (recognisable to anyone reading Salesforce/HubSpot).
--
-- Operations:
--   1. Drop the existing CHECK constraint.
--   2. Remap existing rows to the new taxonomy.
--   3. Re-add CHECK with the new 13-value allow-list.
--
-- Mapping:
--   pharma   -> life_sciences   (kept distinct from healthcare; pharma R&D ≠ provider IT)
--   academia -> education
--   policy   -> public_sector
--   tech     -> software
--   medtech  -> healthcare
--   cpg      -> retail_consumer
--   other    -> other            (no change)


-- ─── Step 1: drop old CHECK ─────────────────────────────────────────────────
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_vertical_check;


-- ─── Step 2: remap existing rows ────────────────────────────────────────────
UPDATE accounts SET vertical = 'life_sciences'   WHERE vertical = 'pharma';
UPDATE accounts SET vertical = 'education'       WHERE vertical = 'academia';
UPDATE accounts SET vertical = 'public_sector'   WHERE vertical = 'policy';
UPDATE accounts SET vertical = 'software'        WHERE vertical = 'tech';
UPDATE accounts SET vertical = 'healthcare'      WHERE vertical = 'medtech';
UPDATE accounts SET vertical = 'retail_consumer' WHERE vertical = 'cpg';


-- ─── Step 3: add new CHECK ──────────────────────────────────────────────────
ALTER TABLE accounts ADD CONSTRAINT accounts_vertical_check
    CHECK (vertical IN (
        'software',
        'financial_services',
        'healthcare',
        'life_sciences',
        'education',
        'public_sector',
        'retail_consumer',
        'media_entertainment',
        'manufacturing',
        'energy_utilities',
        'professional_services',
        'nonprofit',
        'other'
    ));
