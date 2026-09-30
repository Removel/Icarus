"""QQ batch orchestration; policies decide, this service executes."""
import asyncio
import logging
from uuid import uuid4

from ..clients.icarus_client import GatewayBusinessError, GatewayTransportError
from ..dao.observation_store import SessionLink
from ..dao.batch_store import BatchStore
from ..decision_graph.graph import DecisionGraph, NodeBinding, Edge
from ..decision_graph.runner import GraphRunner
from ..entity.decision import RouteDecision
from ..handler.qq_handler import QQHandler
from ..strategy.qq.qq_self_message_policy import QQSelfMessagePolicy
from ..strategy.qq.qq_batch_facts_policy import QQBatchFactsPolicy, transcript
from ..strategy.qq.qq_intervention_policy import QQInterventionPolicy
from ..strategy.qq.qq_jev_intervention_policy import QQJevInterventionPolicy
from ..strategy.qq.qq_task_relation_policy import QQTaskRelationPolicy
from ..strategy.qq.qq_action_policy import QQActionPolicy
from .qq_materials import TaskStatusReader, ContextReader


async def _identity(value):
    return value


class QQService:
    def __init__(self, store, judge, gateway, *, workspace_path, bot_names=(),
                 reply_hint=QQHandler.reply_hint, max_steps=32, graph_timeout_seconds=30):
        self.store=store;self.batches=BatchStore(store);self.judge=judge;self.gateway=gateway
        self.workspace_path=workspace_path;self.reply_hint=reply_hint
        self.max_steps=max_steps;self.graph_timeout_seconds=graph_timeout_seconds
        self._locks={};self.runner=GraphRunner();self.graph=self._build_graph(bot_names)

    def _build_graph(self,names):
        human=QQSelfMessagePolicy();facts=QQBatchFactsPolicy(names);code=QQInterventionPolicy()
        model=QQJevInterventionPolicy(self.judge)
        tasks=TaskStatusReader(self.store,self.gateway,self.workspace_path)
        relation=QQTaskRelationPolicy(self.judge,self.gateway);action=QQActionPolicy()
        return DecisionGraph((
            NodeBinding('human',human.evaluate,lambda batch,_:batch),
            NodeBinding('facts',facts.evaluate,lambda batch,_:batch,requires=('human',)),
            NodeBinding('session',tasks.evaluate,lambda batch,_:batch,requires=('facts',)),
            NodeBinding('code',code.evaluate,lambda _,r:r['facts'],requires=('session',)),
            NodeBinding('context',ContextReader(self.store).evaluate,
                        lambda batch,r:(batch,r['facts'],r['session']),requires=('code','session')),
            NodeBinding('model',model.evaluate,lambda _,r:r['context'],requires=('context',)),
            NodeBinding('intervene',_identity,lambda _,r:r['model'] if 'model' in r else r['code'],join='selected'),
            NodeBinding('silent',_identity,lambda *_:RouteDecision('silence','not_required'),join='selected'),
            NodeBinding('relation',relation.evaluate,lambda _,r:(r['facts'],r['session']),requires=('facts','session')),
            NodeBinding('action',action.evaluate,lambda _,r:(r['relation'],r['session']),requires=('relation','session')),
            NodeBinding('end',_identity,lambda _,r:r.get('silent',r.get('action')),join='any'),
        ),(
            Edge('human','silent',lambda v:not v),Edge('human','facts',bool),Edge('facts','session'),
            Edge('session','code'),
            Edge('code','context',lambda v:v.verdict=='undetermined'),Edge('context','model'),
            Edge('code','intervene',lambda v:v.verdict!='undetermined'),
            Edge('model','intervene'),Edge('intervene','silent',lambda v:v.verdict=='no'),
            Edge('intervene','relation',lambda v:v.verdict=='yes'),
            Edge('relation','action'),Edge('silent','end'),Edge('action','end'),
        ),'human','end')

    async def evaluate_batch(self,batch):
        return await self.runner.run(self.graph,batch,max_steps=self.max_steps,timeout_seconds=self.graph_timeout_seconds)

    async def process_batch(self,batch_id):
        batch=await self.batches.get(batch_id)
        scope=(batch.platform,batch.account_id,batch.subject)
        async with self._locks.setdefault(scope,asyncio.Lock()):
            record=await self.batches.record(batch_id)
            if record['status']=='ready':
                if not await self.batches.claim_batch(batch_id):return
                run=await self.evaluate_batch(batch)
                await self.batches.save_decision(batch_id,run)
            record=await self.batches.record(batch_id)
            if record['status']=='decided':
                try:
                    await self.apply_decision(batch,RouteDecision(**record['route']))
                except Exception as error:
                    logging.getLogger('argus.batch').warning('delivery failed batch=%s error=%s', batch_id, type(error).__name__)
                    await self.batches.transition(batch_id,('submitting',),status='submission_unknown',error_type=type(error).__name__)
                    await self.batches.transition(batch_id,('decided',),status='failed',error_type=type(error).__name__)

    async def _session(self,batch):
        link=await self.store.current_link(batch.platform,batch.account_id,batch.subject)
        if link and link.workspace_path!=self.workspace_path:link=None
        if link:
            try:status=await self.gateway.session_get(link.workspace_path,link.session_id)
            except GatewayBusinessError as error:
                if error.code!='session_not_found':raise
                link=None
            else:
                if status['workspace_key']!=link.workspace_key:raise ValueError('Workspace mismatch')
        if not link:
            record=await self.batches.record(batch.id)
            sid=record['session_id'] if record['workspace_path']==self.workspace_path and record['session_id'] else 'argus-'+uuid4().hex
            await self.batches.transition(batch.id,('decided',),session_id=sid,workspace_path=self.workspace_path)
            try:status=await self.gateway.session_get(self.workspace_path,sid)
            except GatewayBusinessError as error:
                if error.code!='session_not_found':raise
                try:status=await self.gateway.session_create(self.workspace_path,sid)
                except GatewayBusinessError as error:
                    if error.code!='session_exists':raise
                    status=await self.gateway.session_get(self.workspace_path,sid)
                except GatewayTransportError:
                    status=await self.gateway.session_get(self.workspace_path,sid)
            if status['session_id']!=sid:raise ValueError('Session identity mismatch')
            link=SessionLink(batch.platform,batch.account_id,batch.subject,self.workspace_path,status['workspace_key'],sid)
            await self.store.save_link(link)
        return link

    async def apply_decision(self,batch,decision):
        if (await self.batches.record(batch.id))['status']!='decided':return
        try:
            link=await self._session(batch)
        except (GatewayTransportError,GatewayBusinessError):
            return # No write operation has been sent; retry the saved decision later.
        except ValueError:
            await self.batches.transition(batch.id,('decided',),status='failed',error_type='identity_mismatch');return
        prompt=f'平台: {batch.platform}\n会话: {batch.subject}\n'+transcript(batch.messages)+self.reply_hint(batch.subject)
        if len(prompt)>100000:
            await self.batches.transition(batch.id,('decided',),status='failed',error_type='prompt_too_large');return
        action=decision.action
        target=decision.target_task_id
        if action=='steer':
            try:
                status=await self.gateway.task_get_status(link.workspace_path,link.session_id,target)
            except GatewayBusinessError as error:
                if error.code not in {'task_not_found','task_status_unavailable'}:return
                try:
                    snapshot=await self.gateway.session_get(link.workspace_path,link.session_id)
                except (GatewayTransportError,GatewayBusinessError):return
                if snapshot.get('workspace_key') != link.workspace_key:
                    await self.batches.transition(batch.id,('decided',),status='failed',error_type='identity_mismatch')
                    return
                if (snapshot.get('lifecycle') in {'ready','unloaded'} and
                    snapshot.get('active_task_ids') == [] and snapshot.get('queued_task_count',0)==0):
                    status={'lifecycle':'completed'}
                else:
                    await self.batches.transition(batch.id,('decided',),status='failed',error_type='task_state_unresolved')
                    return
            except GatewayTransportError:return
            if status['lifecycle'] in ('completed','failed','cancelled','interrupted'):
                action='submit'
                await self.batches.transition(batch.id,('decided',),route=dict(action='submit',reason='target_finished',target_task_id=None))
            elif status['lifecycle'] not in ('running','accepted'):return
        for _ in range(2):
            method='session.steer' if action=='steer' else 'session.submit'
            op_id=f'{batch.id}:{action}'+(f':{target}' if action=='steer' else '')
            params=dict(workspace_path=link.workspace_path,session_id=link.session_id,prompt=prompt,submission_id=op_id)
            if action=='steer':params['task_id']=target
            if not await self.batches.begin_submission(batch.id,method,params,op_id,workspace_key=link.workspace_key):return
            try:
                if action=='steer':result=await self.gateway.session_steer(**params)
                else:result=await self.gateway.session_submit(**params)
            except GatewayBusinessError as error:
                state='failed' if error.code in {'session_not_found','submission_conflict','runtime_stopping','-32602'} else 'submission_unknown'
                await self.batches.transition(batch.id,('submitting',),status=state,error_type=error.code);return
            except (GatewayTransportError,TimeoutError):
                await self.batches.transition(batch.id,('submitting',),status='submission_unknown',error_type='transport');return
            if not isinstance(result, dict):
                await self.batches.transition(batch.id,('submitting',),status='submission_unknown',error_type='invalid_receipt')
                return
            if action=='steer' and result.get('status') in ('not_running','already_finished'):
                await self.batches.transition(batch.id,('submitting',),status='decided',receipt=result,
                                             route=dict(action='submit',reason='steer_rejected',target_task_id=None))
                action='submit';continue
            valid=(result.get('status')=='accepted' if action=='steer' else bool(result.get('task_id')))
            if not valid:
                await self.batches.transition(batch.id,('submitting',),status='submission_unknown',error_type='invalid_receipt');return
            await self.batches.mark_receipt(batch.id,result);return
