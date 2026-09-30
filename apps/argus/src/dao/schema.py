"""Explicit, backed-up schema upgrade; never replay historical submissions."""
from datetime import datetime, UTC
from pathlib import Path
import json
import sqlite3
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from .entities import _DecisionBatchRow

VERSION=2


class SchemaUpgradeRequired(RuntimeError):
    pass


def check_schema(path):
    if not path.exists(): return
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        has_table=db.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if has_table and version!=VERSION:
            raise SchemaUpgradeRequired('Stop Argus and run apps/argus/scripts/migrate.sh before starting')


def migrate(path: Path):
    path=Path(path)
    if not path.is_file(): raise ValueError('Database does not exist')
    db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
    try:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version==VERSION: return None
        if version not in (0,1): raise ValueError('Unsupported schema version')
        backup=path.with_name(path.name+'.backup-'+datetime.now(UTC).strftime('%Y%m%d%H%M%S%f'))
        with sqlite3.connect(backup) as target: db.backup(target)
        backup.chmod(0o600)
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('ALTER TABLE observations ADD COLUMN ingest_seq INTEGER')
            db.execute('ALTER TABLE observations ADD COLUMN batch_id VARCHAR(64)')
            db.execute(str(CreateTable(_DecisionBatchRow.__table__).compile(dialect=dialect())))
            for index in _DecisionBatchRow.__table__.indexes:
                db.execute(str(CreateIndex(index).compile(dialect=dialect())))
            rows=db.execute('SELECT * FROM observations ORDER BY received_at,id').fetchall()
            for seq,row in enumerate(rows,1):
                batch_id='legacy-'+row['id'];status=row['processing_status']
                if status in ('pending','processing'):
                    status='submission_unknown' if row['submission_id'] and not row['task_id'] else 'ready'
                elif status=='submitting': status='submission_unknown'
                if row['task_id']: status='submitted'
                db.execute('UPDATE observations SET ingest_seq=?,batch_id=? WHERE id=?',(seq,batch_id,row['id']))
                db.execute('''INSERT INTO decision_batches
                    (id,platform,account_id,subject,opened_at,closed_at,close_reason,status,trace,workspace_path,workspace_key,session_id,operation_id,receipt,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (batch_id,row['platform'],row['account_id'],row['subject'],row['received_at'],row['received_at'],'legacy',status,'[]',
                     row['workspace_path'],row['workspace_key'],row['session_id'],row['submission_id'],
                     json.dumps({'task_id':row['task_id']}) if row['task_id'] else None,row['updated_at']))
            for row in rows:
                historical = {key: row[key] for key in (
                    'processing_status', 'strategy_version', 'judgment', 'reason', 'error_type')}
                route = None
                if row['judgment'] in ('wake', 'silence'):
                    route = dict(action='submit' if row['judgment']=='wake' else 'silence',
                                 reason=row['reason'] or 'legacy_decision', target_task_id=None)
                db.execute('UPDATE decision_batches SET graph_version=?,route=?,error_type=?,trace=? WHERE id=?',
                           (row['strategy_version'], json.dumps(route) if route else None,
                            row['error_type'], json.dumps([historical]), 'legacy-'+row['id']))
            db.execute('DROP INDEX IF EXISTS ix_observation_status')
            for column in ('processing_status','strategy_version','judgment','reason','workspace_path',
                           'workspace_key','session_id','submission_id','task_id','error_type','updated_at'):
                db.execute(f'ALTER TABLE observations DROP COLUMN {column}')
            db.execute('CREATE UNIQUE INDEX uq_ingest_seq ON observations(ingest_seq)')
            db.execute('CREATE INDEX ix_observations_batch_id ON observations(batch_id)')
            db.execute(f'PRAGMA user_version={VERSION}')
            db.commit()
        except BaseException:
            db.rollback();raise
        return backup
    finally:
        db.close()
