from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from app.api.schemas import EvalModeMetricsOut, EvalRunOut, EvalRunRequest
from app.core import audit
from app.core.deps import CurrentPrincipal, DbSession, require_permissions
from app.evaluation import harness
from app.tenancy.permissions import EVAL_RUN

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.post("/run", response_model=EvalRunOut,
             dependencies=[Depends(require_permissions(EVAL_RUN))])
def run_evaluation(payload: EvalRunRequest, db: DbSession,
                   principal: CurrentPrincipal) -> EvalRunOut:
    report = harness.run_suite(payload.suite, payload.modes, payload.limit,
                               persist_tenant_id=principal.tenant_id)
    paths = harness.write_report(report)
    audit.record(db, audit.EVAL_RUN, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="eval",
                 resource_id=payload.suite,
                 detail={"modes": payload.modes, "report": paths["markdown"]})
    return EvalRunOut(
        suite=report.suite,
        generated_at=dt.datetime.fromisoformat(report.generated_at),
        llm_provider=report.llm_provider, llm_model=report.llm_model,
        modes=[
            EvalModeMetricsOut(
                mode=m.mode, cases=m.cases, factual_accuracy=m.factual_accuracy,
                groundedness=m.groundedness, citation_accuracy=m.citation_accuracy,
                unsupported_claim_rate=m.unsupported_claim_rate,
                hallucination_rate=m.hallucination_rate,
                contradiction_rate=m.contradiction_detection_rate,
                symbolic_accuracy=m.symbolic_accuracy,
                abstention_accuracy=m.abstention_accuracy,
                retrieval_precision=m.retrieval_precision,
                retrieval_recall=m.retrieval_recall,
                mean_latency_ms=m.mean_latency_ms, p95_latency_ms=m.p95_latency_ms,
            )
            for m in report.metrics.values()
        ],
        report_path=paths["markdown"],
    )
