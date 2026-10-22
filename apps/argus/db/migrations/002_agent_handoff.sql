ALTER TABLE wake_requests ADD COLUMN handoff jsonb NOT NULL DEFAULT '{"mode":"mock"}'::jsonb;
ALTER TABLE observed_items ADD COLUMN exposed_wake_id uuid;
CREATE UNIQUE INDEX uq_wake_real_scope ON wake_requests(chat_id)
    WHERE handoff->>'mode'='gateway' AND COALESCE(handoff->>'local_terminal','false')!='true'
    AND COALESCE(handoff->>'release_complete','false')!='true';
CREATE INDEX ix_observed_scope_watermark ON observed_items(source,chat_id,observed_at,id);
