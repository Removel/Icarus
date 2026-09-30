import asyncio
import sqlite3
import pytest
from apps.argus.src.dao.schema import migrate, SchemaUpgradeRequired
from apps.argus.src.dao.observation_store import ObservationStore


def test_legacy_store_requires_explicit_upgrade_and_preserves_receipts(tmp_path):
    path=tmp_path/'argus.db'
    # Legacy fixture is sourced from the committed pre-graph schema, not the new metadata.
    import subprocess
    source=subprocess.check_output(['git','show','959b4e0:apps/argus/src/dao/entities.py'],text=True)
    namespace={};exec(source,namespace)
    from sqlalchemy import create_engine
    engine=create_engine('sqlite:///'+str(path));namespace['_Base'].metadata.create_all(engine);engine.dispose()
    conn=sqlite3.connect(path)
    states=['pending','processing','submitting','submitted','finished','failed','submission_unknown']
    for i,state in enumerate(states):
        conn.execute('''INSERT INTO observations (id,platform,account_id,source_message_id,subject,sender_id,occurred_at,received_at,content,subtype,detail_version,detail,processing_status,submission_id,task_id,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(str(i),'qq','1',str(i),'qq:group:2','3','2026-01-01','2026-01-01','[]','qq_message',1,'{}',state,
                 'original' if state in ('submitting','submitted','submission_unknown') else None,'t' if state=='submitted' else None,'2026-01-01'))
    conn.execute("UPDATE observations SET strategy_version='qq-wake-v3',judgment='silence',reason='judge_fallback',error_type='TimeoutError' WHERE id='5'")
    conn.commit();conn.close()
    async def refuse():
        with pytest.raises(SchemaUpgradeRequired): await ObservationStore(tmp_path).start()
    asyncio.run(refuse())
    backup=migrate(path)
    assert backup.is_file()
    assert migrate(path) is None
    conn=sqlite3.connect(path)
    assert conn.execute('PRAGMA user_version').fetchone()[0]==2
    assert [r[0] for r in conn.execute('SELECT status FROM decision_batches ORDER BY id')]==['ready','ready','submission_unknown','submitted','finished','failed','submission_unknown']
    assert conn.execute("SELECT operation_id FROM decision_batches WHERE id='legacy-3'").fetchone()[0]=='original'
    import json
    historical=conn.execute("SELECT graph_version,route,error_type,trace FROM decision_batches WHERE id='legacy-5'").fetchone()
    assert historical[0]=='qq-wake-v3'
    assert json.loads(historical[1])['reason']=='judge_fallback'
    assert historical[2]=='TimeoutError'
    assert json.loads(historical[3])[0]['judgment']=='silence'
    columns={r[1] for r in conn.execute('PRAGMA table_info(observations)')}
    assert not columns.intersection({'processing_status','submission_id','task_id','judgment','reason'})
    conn.close()
