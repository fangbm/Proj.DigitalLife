"""Typed decision refusals are execution evidence, never selected goals or facts."""
from dataclasses import asdict, dataclass
from .llm import ModelOutputError


class DecisionContractError(ValueError):
    def __init__(self, code, field, expected, received=None):
        self.code,self.field,self.expected,self.received=code,field,expected,received
        super().__init__(code+': '+expected)


@dataclass(frozen=True)
class LifeDecisionRejection:
    reason_code: str
    field: str
    expected: str
    received_type: str
    eligible_goal_ids: list[str]
    generation_attempts: int
    repairs_used: int
    source_task_run_id: str
    executed: bool
    confirmed_actions: int
    candidate_sha256: str
    candidate_summary: dict


class LifeDecisionRejected(ModelOutputError):
    def __init__(self, rejections):
        self.rejections=rejections
        self.code=rejections[-1]['reason_code']
        super().__init__(self.code+': candidate rejected, no corresponding goal executed')


def rejection(error,context,audit,attempt,decision=None,candidate=None):
    code=getattr(error,'code','INVALID_LIFE_DECISION_SCHEMA')
    if code not in {'MISSING_CONTINUATION_REFERENCE','INVALID_CONTINUATION_REFERENCE',
            'CONTINUATION_BUDGET_EXCEEDED','CONTINUATION_CONTRACT_CHANGED','MODEL_OUTPUT_INVALID'}:
        code='INVALID_LIFE_DECISION_SCHEMA'
    summary={'kind':decision.kind if decision else 'unparsed','candidate_count':len(decision.candidates) if decision else 0,
             'selected_continuation_type':type(decision.selected.continuation_of).__name__ if decision and decision.selected else 'unavailable'}
    return asdict(LifeDecisionRejection(code,getattr(error,'field','life_decision'),
        getattr(error,'expected','One strict LifeDecision JSON matching the supplied contract'),
        type(getattr(error,'received',candidate)).__name__,
        [g['goal_id'] for g in context.unfinished_goals if g.get('can_continue',True)],attempt,attempt-1,
        context.device.get('task_run_id',''),False,0,audit['response_sha256'],summary))
