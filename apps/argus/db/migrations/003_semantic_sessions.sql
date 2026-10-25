DROP INDEX IF EXISTS uq_wake_active;
DROP INDEX IF EXISTS uq_wake_real_scope;
CREATE UNIQUE INDEX uq_wake_active ON wake_requests(boot_id,chat_id)
    WHERE status IN ('waiting','sent','accepted') AND COALESCE(handoff->>'mode','mock')!='gateway';
CREATE UNIQUE INDEX uq_wake_real_scope ON wake_requests(chat_id)
    WHERE handoff->>'mode'='gateway' AND COALESCE(handoff->>'local_terminal','false')!='true'
    AND COALESCE(handoff->>'release_complete','false')!='true'
    AND (COALESCE(handoff->>'protocol_version','1')!='2' OR handoff->>'submission_may_have_run'='true');
CREATE INDEX IF NOT EXISTS ix_wake_waiting ON wake_requests(boot_id,chat_id,created_at,id)
    WHERE handoff->>'mode'='gateway' AND handoff->>'protocol_version'='2'
    AND COALESCE(handoff->>'submission_may_have_run','false')!='true'
    AND COALESCE(handoff->>'local_terminal','false')!='true';
CREATE INDEX IF NOT EXISTS ix_observed_session ON observed_items((detail->>'argus_session_id'),chat_id,observed_at,id);
