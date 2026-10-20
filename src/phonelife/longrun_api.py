"""Unmodified real transport with bounded usage/hash telemetry, never an injector."""
import hashlib
import json
import time
from .llm import ChatAPI

def hash_text(value):return hashlib.sha256(value.encode('utf8')).hexdigest()

class SoakAPI(ChatAPI):
    def __init__(self,*args,store,budget,source_kind,**kwargs):
        super().__init__(*args,**kwargs)
        self.store,self.budget,self.source_kind=store,budget,source_kind
        self.wire=None;self.usage=None
    def _request(self,payload):
        self.wire=hash_text(json.dumps(payload,ensure_ascii=False,allow_nan=False))
        result=super()._request(payload)
        raw=result.get('usage') or {}
        keys=('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')
        self.usage={k:raw[k] for k in keys if type(raw.get(k))is int and raw[k]>=0}
        return result
    async def generate(self,model,system,user,image=None,temperature=.2):
        budget=self.budget()
        if not budget or not budget.run_id:raise RuntimeError('Soak API requires a metered cycle')
        row=self.store.conn.execute("SELECT request_id,phase FROM life_requests WHERE task_run_id=? AND status='reserved' ORDER BY requested_at DESC LIMIT 1",(budget.run_id,)).fetchone()
        if not row:raise RuntimeError('Missing quota reservation')
        self.wire=None;self.usage=None
        event={'task_run_id':budget.run_id,'request_id':row['request_id'],'phase':row['phase'],
            'source_kind':self.source_kind,'request_user_sha256':hash_text(user),'system_sha256':hash_text(system),
            'started_monotonic_ns':time.monotonic_ns(),'declared_model':model,'context_memory_refs':[]}
        if row['phase']=='brain':
            context=json.loads(user).get('life_context',{})
            event['life_context_sha256']=hash_text(json.dumps(context,sort_keys=True,ensure_ascii=False))
            for episode in context.get('recent_episodes',[])+context.get('recent_failures',[]):
                ref={'memory_id':episode.get('memory_id'),'source_runs':episode.get('source_runs',[])}
                if ref not in event['context_memory_refs']:event['context_memory_refs'].append(ref)
        try:
            response=await super().generate(model,system,user,image,temperature)
            event.update(status='received',response_sha256=hash_text(response.content),
                provider_request_id_sha256=hash_text(response.api_request_id),finish_reason=response.finish_reason)
            return response
        except BaseException as error:
            event.update(status='failed_or_cancelled',error_code=getattr(error,'code',type(error).__name__))
            raise
        finally:
            event.update(request_payload_sha256=self.wire,provider_usage=self.usage,
                token_usage_reported=self.usage is not None and bool(self.usage),
                estimated_cost=None,cost_currency=None,
                cost_assumption='No price configured; requests are not currency, cache/token pricing unknown',
                finished_monotonic_ns=time.monotonic_ns())
            self.store.event(budget.device,'longrun_api_exchange',event)
