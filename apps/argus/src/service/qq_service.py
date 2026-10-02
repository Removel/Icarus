"""QQ decisions are composed here; only this service delivers business actions."""
import asyncio
from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4
from ..clients.icarus_client import GatewayBusinessError, GatewayTransportError
from ..entity.session_link import SessionLink
from . import batch_state
from ..strategy.batch_lifecycle import OPEN_STATES
from ..decision_graph.compiler import GraphCompiler
from ..decision_graph.graph import DecisionGraph
from ..decision_graph.node import NodeBinding
from ..decision_graph.edge import Edge, EdgeAction
from ..decision_graph.runner import GraphRunner
from ..entity.decision import RouteDecision
from ..strategy.qq.qq_self_message_policy import QQSelfMessagePolicy
from ..evidence.qq.qq_facts import QQBatchFactsReader, QQDecisionEvidenceReader
from ..entity.policy_inputs import InterventionInput, RelationInput, ActionInput, DeliveryCheckInput
from ..strategy.work_state import DeliveryCheckPolicy
from ..strategy.qq.qq_intervention_policy import QQInterventionPolicy
from ..strategy.qq.qq_jev_intervention_policy import QQJevInterventionPolicy
from ..strategy.qq.qq_task_relation_policy import QQTaskRelationPolicy
from ..strategy.qq.qq_action_policy import QQActionPolicy
from ..evidence.icarus_state import IcarusStateReader, session_signature, fingerprint
from ..evidence.qq.qq_conversation import QQConversationReader
from ..evidence.recent_activity import RecentActivityReader
from ..strategy.decision_trace import safe_trace
from ..evidence.qq.qq_prompt import compose_qq_prompt, reply_hint
from ..evidence.qq.qq_signals import is_strong_signal

logger = logging.getLogger('argus.batch')


async def identity(*, value):
    return value


class QQService:
    def __init__(self, database, store, batches, judge, gateway, *, workspace_path, bot_names=(),
                 reply_hint=reply_hint, max_steps=32, graph_timeout_seconds=30,
                 clock=lambda:datetime.now(UTC), waiting_check_seconds=5, submission_window_seconds=300):
        self.store,self.judge,self.gateway = store,judge,gateway
        self.database, self.batches = database, batches
        self.workspace_path,self.reply_hint,self.clock = workspace_path,reply_hint,clock
        self.max_steps,self.graph_timeout_seconds = max_steps,graph_timeout_seconds
        self.waiting_check_seconds = waiting_check_seconds
        self.bot_names, self.notify = bot_names, None
        self._locks = {}
        self.runner = GraphRunner()
        self.work_reader = IcarusStateReader(store,batches,gateway,workspace_path,clock=clock)
        self.conversation_reader = QQConversationReader(database,store,batches)
        self.activity_reader = RecentActivityReader(database,store,batches,window_seconds=submission_window_seconds)
        self.facts_policy = QQBatchFactsReader(bot_names)
        self.decision_reader = QQDecisionEvidenceReader(self.conversation_reader,self.work_reader,
            self.activity_reader,self.facts_policy,batches,clock=clock)
        self.graph = GraphCompiler().compile(self._build_graph())

    async def receive(self, observation):
        inserted = await self.store.insert(observation.to_row(
            is_strong_signal=is_strong_signal(observation,self.bot_names)))
        if inserted and self.notify is not None:
            try:
                self.notify((observation.source.platform,observation.source.account_id,observation.subject.key),
                            human=not observation.is_self)
            except Exception as error:
                logger.warning('receive notification failed error=%s',type(error).__name__)
        return inserted

    def _build_graph(self):
        human = QQSelfMessagePolicy()
        code = QQInterventionPolicy()
        model = QQJevInterventionPolicy(self.judge)
        relation = QQTaskRelationPolicy(self.judge)
        action = QQActionPolicy()
        nodes = (
            NodeBinding('human',human.evaluate,lambda batch,_:{'value':batch}),
            NodeBinding('code',code.evaluate,lambda _,r:{'value':InterventionInput(
                r['decision_evidence'].facts,r['decision_evidence'].snapshot,
                r['decision_evidence'].activity,r['decision_evidence'].rechecking)},requires=('decision_evidence',)),
            NodeBinding('model',model.evaluate,lambda _,r:{'value':r['decision_evidence'].facts},requires=('decision_evidence','code')),
            NodeBinding('intervene',identity,lambda _,r:{'value':r['model'] if 'model' in r else r['code']},join='selected'),
            NodeBinding('relation',relation.evaluate,lambda _,r:{'value':RelationInput(
                r['decision_evidence'].facts,r['decision_evidence'].snapshot)},requires=('decision_evidence',)),
            NodeBinding('action',action.evaluate,lambda _,r:{'value':ActionInput(
                r['relation'],r['decision_evidence'].snapshot)},requires=('relation','decision_evidence')),
            NodeBinding('silent',identity,lambda *_:{'value':RouteDecision('silence','not_required')},join='selected'),
            NodeBinding('end',identity,lambda _,r:{'value':r.get('silent',r.get('action'))},join='any'),
        )
        edges = (
            Edge('human','code',bool,collect=EdgeAction(self.decision_reader.evaluate,
                 lambda batch,_:{'batch':batch}),id='decision_evidence'),
            Edge('human','silent',lambda v:not v),
            Edge('code','model',lambda v:v.verdict=='undetermined'),
            Edge('code','intervene',lambda v:v.verdict!='undetermined'),Edge('model','intervene'),
            Edge('intervene','silent',lambda v:v.verdict=='no'),Edge('intervene','relation',lambda v:v.verdict=='yes'),
            Edge('relation','action'),Edge('action','end'),Edge('silent','end'),
        )
        return DecisionGraph(nodes,edges,'human','end')

    async def evaluate_batch(self,batch):
        return await self.runner.run(self.graph,batch,max_steps=self.max_steps,timeout_seconds=self.graph_timeout_seconds)

    async def waiting_changed(self,batch_id,*,now):
        record = await self.batches.get(batch_id)
        if record.status != 'waiting':
            return False
        batch = await batch_state.load_batch(self.store,self.batches,batch_id)
        # No model call here. Work history is bounded, and only its cursor enters the signature.
        snapshot = await self.work_reader.evaluate(batch=batch)
        human_seq = await self.store.message_watermark(batch.platform,batch.account_id,batch.subject,human_only=True)
        changed = fingerprint(snapshot,human_seq) != record.wait_fingerprint
        if not changed:
            await self.batches.transition(batch_id,('waiting',),next_check_at=now+timedelta(seconds=self.waiting_check_seconds))
        return changed

    async def process_batch(self,batch_id):
        batch = await batch_state.load_batch(self.store,self.batches,batch_id)
        scope = (batch.platform,batch.account_id,batch.subject)
        async with self._locks.setdefault(scope,asyncio.Lock()):
            if not await self.batches.is_head(batch_id,open_states=OPEN_STATES):
                return
            record = await self.batches.get(batch_id)
            if record.status in {'ready','waiting'}:
                if not await batch_state.claim_batch(self.database,self.batches,batch_id):
                    return
                run = await self.evaluate_batch(batch)
                valid = run.status=='completed' and isinstance(run.output,RouteDecision)
                decision = run.output if valid else RouteDecision('silence','graph_failed')
                collected = run.executions.get('decision_evidence')
                evidence = collected.output if collected and collected.status=='succeeded' else None
                intervention = run.executions.get('intervene')
                snapshot = evidence.snapshot if evidence else None
                messages = evidence.conversation if evidence else None
                verdict = intervention.output.verdict if intervention and intervention.status=='succeeded' else None
                sig = fingerprint(snapshot,messages.human_through_seq) if snapshot and messages else None
                await batch_state.save_decision(self.database,self.batches,batch_id,decision=decision,run_status='completed' if valid else 'failed',
                    trace=safe_trace(run),judged_at=self.clock() if valid else None,intervention=verdict,
                    judged_through_seq=messages.through_seq if messages else None,
                    decision_message_seq=messages.human_through_seq if messages else None,
                    wait_fingerprint=sig,next_check_at=self.clock()+timedelta(seconds=self.waiting_check_seconds) if decision.action=='wait' else None)
            record = await self.batches.get(batch_id)
            if record.status=='decided':
                try:
                    await self.apply_decision(batch,RouteDecision(**record.route))
                except Exception as error:
                    logger.warning('delivery failed batch=%s error=%s',batch_id,type(error).__name__)
                    await self.batches.transition(batch_id,('submitting',),status='submission_unknown',error_type=type(error).__name__)
                    await self.batches.transition(batch_id,('decided',),status='failed',error_type=type(error).__name__)

    async def _wait(self,batch,reason):
        # Preserve the fingerprint of evidence actually judged: the next scan must notice the change.
        record = await self.batches.get(batch.id)
        await batch_state.defer(self.database,self.batches,batch.id,reason=reason,
            next_check_at=self.clock()+timedelta(seconds=self.waiting_check_seconds),fingerprint=record.wait_fingerprint)

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
            record=await self.batches.get(batch.id)
            sid=record.session_id if record.workspace_path==self.workspace_path and record.session_id else 'argus-'+uuid4().hex
            await self.batches.transition(batch.id,('decided',),session_id=sid,workspace_path=self.workspace_path)
            status=await self.gateway.ensure_session(self.workspace_path,sid)
            link=SessionLink(batch.platform,batch.account_id,batch.subject,self.workspace_path,status['workspace_key'],sid)
            await self.store.save_link(link.to_row(id=uuid4().hex,created_at=self.clock()))
        return link, status

    async def _retry_delivery(self, batch):
        await self.batches.transition(batch.id, ('decided',),
            next_check_at=self.clock()+timedelta(seconds=self.waiting_check_seconds))

    async def apply_decision(self,batch,decision):
        record = await self.batches.get(batch.id)
        if record.status!='decided':
            return
        try:
            snapshot = await self.work_reader.evaluate(batch=batch)
        except (GatewayTransportError, GatewayBusinessError):
            await self._retry_delivery(batch)
            return
        except ValueError:
            await self.batches.transition(batch.id, ('decided',), status='failed', error_type='identity_mismatch')
            return
        human_seq = await self.store.message_watermark(batch.platform,batch.account_id,batch.subject,human_only=True)
        checked = await DeliveryCheckPolicy().evaluate(DeliveryCheckInput(decision,snapshot,human_seq,
            record.wait_fingerprint,fingerprint(snapshot,human_seq)))
        if checked.action == 'wait':
            await self._wait(batch,checked.reason)
            return
        context = await self.conversation_reader.delivery_context(batch)
        try:
            link, final_status = await self._session(batch)
        except (GatewayTransportError,GatewayBusinessError):
            await self._retry_delivery(batch)
            return  # No write attempted; retry only after the saved deadline.
        except ValueError:
            await self.batches.transition(batch.id,('decided',),status='failed',error_type='identity_mismatch')
            return
        # The final awaited lookup must not discard work/new-message changes.
        final_human_seq = await self.store.message_watermark(batch.platform,batch.account_id,batch.subject,human_only=True)
        expected = dict(workspace_key=snapshot.workspace_key,session_id=snapshot.session_id,
            lifecycle=snapshot.lifecycle,active_task_ids=snapshot.active_task_ids,
            queued_task_count=snapshot.queued_task_count,pending_event_count=snapshot.pending_event_count,
            pending_plugin_event_count=snapshot.pending_plugin_event_count,background_work_count=snapshot.background_work_count)
        checked = await DeliveryCheckPolicy().evaluate(DeliveryCheckInput(decision,snapshot,human_seq,
            record.wait_fingerprint,fingerprint(snapshot,human_seq),final_status,final_human_seq,
            session_signature(expected),session_signature(final_status)))
        if checked.action == 'wait':
            await self._wait(batch,checked.reason)
            return
        prompt = compose_qq_prompt(batch, decision, context=context,
            has_history=bool(snapshot.history_cursor or snapshot.history_records), reply_hint=self.reply_hint)
        if len(prompt)>100000:
            await self.batches.transition(batch.id,('decided',),status='failed',error_type='prompt_too_large')
            return
        method = 'session.steer' if decision.action=='steer' else 'session.submit'
        attempts = record.delivery_attempts or []
        suffix = f':attempt:{len(attempts)+1}' if attempts else ''
        operation_id = f'{batch.id}:{decision.action}'+(f':{decision.target_task_id}' if decision.action=='steer' else '')+suffix
        params = dict(workspace_path=link.workspace_path,session_id=link.session_id,prompt=prompt,submission_id=operation_id)
        if decision.action=='steer':
            params['task_id']=decision.target_task_id
        if not await batch_state.begin_submission(self.database,self.batches,batch.id,method,params,operation_id,workspace_key=link.workspace_key):
            return
        result = await (self.gateway.session_steer(**params) if decision.action=='steer' else self.gateway.session_submit(**params))
        if result.kind in {'failed','unknown'}:
            await self.batches.transition(batch.id,('submitting',),
                status='failed' if result.kind=='failed' else 'submission_unknown',error_type=result.error_type)
        elif result.kind == 'rejected':
            await batch_state.mark_rejected(self.database,self.batches,batch.id,result.receipt,
                next_check_at=self.clock()+timedelta(seconds=self.waiting_check_seconds))
        else:
            await batch_state.mark_receipt(self.database,self.batches,batch.id,result.receipt,delivered_at=self.clock())
