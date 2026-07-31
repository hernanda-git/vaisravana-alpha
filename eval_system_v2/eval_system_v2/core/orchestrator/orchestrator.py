"""
Meta-Orchestrator v2
Coordinates all meta-evaluators, dynamic factors, LLM research, and unknown factor discovery.

This is the brain of the evaluation system. It runs the full pipeline:
1. Layer 1-5 evaluation
2. Meta-evaluation of all evaluators
3. Dynamic factor weight adjustment
4. Unknown factor discovery
5. LLM research for unknowns
6. Final decision with confidence scoring
"""

import json
from datetime import datetime, timezone
from typing import Optional

from eval_system_v2.core.engine.eval_engine import (
    EvaluationEngine,
    Layer2Output,
    Layer3Output,
    Layer4Output,
    Layer5Output,
    MetaEvalResult,
    TrustLevel,
)


class MetaOrchestrator:
    """
    Coordinates the full evaluation pipeline with meta-evaluation.

    Pipeline:
    1. Evaluate trade (Layer 1)
    2. Aggregate window (Layer 2)
    3. Baseline comparison (Layer 3)
    4. Decision gate (Layer 4)
    5. Report generation (Layer 5)
    6. Meta-evaluate all layers
    7. Update dynamic factors
    8. Discover unknown factors
    9. Research unknowns via LLM
    10. Make final decision
    """

    def __init__(self, engine: EvaluationEngine):
        self.engine = engine
        self.research_queue = []
        self.factor_weights = {
            "regime": 0.25,
            "flow": 0.20,
            "cross_asset": 0.15,
            "temporal": 0.15,
            "llm_discovered": 0.25,
        }

    def coordinate_trade(self, trade_data: dict) -> dict:
        """
        Full pipeline for a single trade completion.
        """
        # Layer 1: Per-trade evaluation
        l1 = self.engine.evaluate_trade(trade_data)

        # Check if we have enough trades for window evaluation
        if len(self.engine.trades) >= self.engine.window_size:
            return self.coordinate_window()

        return {
            "stage": "layer1_only",
            "trade_score": l1.trade_score,
            "verdict": l1.verdict.value,
            "is_positive_ev": l1.is_positive_ev,
            "trades_so_far": len(self.engine.trades),
            "window_size": self.engine.window_size,
        }

    def coordinate_window(self) -> dict:
        """
        Full pipeline for an evaluation window (20 trades).
        """
        # Layers 2-5
        l2 = self.engine.run_layer2()
        if not l2:
            return {"status": "waiting_for_window"}

        previous_window = self.engine.windows[-2] if len(self.engine.windows) >= 2 else None
        l3 = self.engine.run_layer3(previous_window)
        l4 = self.engine.run_layer4(l3)
        l5 = self.engine.run_layer5(l4, l3)

        # Meta-evaluation
        meta_eval = self.engine.meta_evaluate()

        # Factor adjustment
        factors = self.engine.update_factors(meta_eval)

        # Unknown discovery
        unknowns = self.engine.discover_unknowns()

        # LLM research for unknowns with trust_level == "unknown"
        research_results = self._research_unknowns(unknowns, meta_eval)

        # Combine everything into final decision
        final_decision = self._combine_all(
            l1=None,  # Layer 1 is per-trade, not window-level
            l2=l2,
            l3=l3,
            l4=l4,
            l5=l5,
            meta_eval=meta_eval,
            factors=factors,
            unknowns=unknowns,
            research_results=research_results,
        )

        return final_decision

    def _research_unknowns(self, unknowns: dict, meta_eval: MetaEvalResult) -> dict:
        """
        Research unknown factors using LLM.
        For now, this is a placeholder that returns research topics.
        In production, this would call the LLM research module.
        """
        research_results = {}

        for factor in unknowns.get("known_not_implemented", []):
            research_results[factor] = {
                "status": "research_needed",
                "priority": "high",
                "action": "add_to_factor_engine",
                "reason": f"Factor {factor} exists in research but not implemented",
            }

        for factor in unknowns.get("unknown_discoverable", []):
            research_results[factor] = {
                "status": "discoverable",
                "priority": "medium",
                "action": "research_and_implement",
                "reason": f"Factor {factor} can be discovered through research",
            }

        for factor in unknowns.get("known_misunderstood", []):
            research_results[factor] = {
                "status": "misunderstood",
                "priority": "high",
                "action": "reinterpret_and_fix",
                "reason": f"Factor {factor} exists but is interpreted incorrectly",
            }

        return research_results

    def _combine_all(
        self,
        l1: Optional[dict],
        l2: Layer2Output,
        l3: Layer3Output,
        l4: Layer4Output,
        l5: Layer5Output,
        meta_eval: MetaEvalResult,
        factors: dict,
        unknowns: dict,
        research_results: dict,
    ) -> dict:
        """
        Combine all evaluation outputs into a final decision.
        """
        # Decision confidence is weighted by meta-evaluator trust
        trust_confidence = {
            TrustLevel.HIGH: 1.0,
            TrustLevel.MEDIUM: 0.75,
            TrustLevel.LOW: 0.5,
            TrustLevel.UNKNOWN: 0.25,
        }.get(meta_eval.trust_level, 0.5)

        # Final decision is Layer 4 decision, but adjusted by meta-evaluation
        final_decision = l4.decision.value
        final_confidence = l4.confidence * trust_confidence

        # If meta-evaluator says unknown, add research task
        if meta_eval.trust_level == TrustLevel.UNKNOWN:
            final_decision = "research_more"
            final_confidence = 0.25

        # If meta-evaluator says low trust, investigate
        if meta_eval.trust_level == TrustLevel.LOW:
            final_decision = "investigate_evaluators"
            final_confidence = 0.5

        return {
            "stage": "full_pipeline",
            "layer1": l1,
            "layer2": l2.to_dict(),
            "layer3": l3.to_dict(),
            "layer4": l4.to_dict(),
            "layer5": l5.to_dict(),
            "meta_eval": meta_eval.to_dict(),
            "factors": factors,
            "unknowns": unknowns,
            "research_results": research_results,
            "final_decision": final_decision,
            "final_confidence": round(final_confidence, 4),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_status(self) -> dict:
        """Get current status of the evaluation system."""
        return {
            "bot_name": self.engine.bot_name,
            "total_trades": len(self.engine.trades),
            "total_windows": len(self.engine.windows),
            "total_decisions": len(self.engine.decisions),
            "total_reports": len(self.engine.reports),
            "total_meta_evals": len(self.engine.meta_evals),
            "last_decision": self.engine.decisions[-1].to_dict() if self.engine.decisions else None,
            "last_meta_eval": self.engine.meta_evals[-1].to_dict() if self.engine.meta_evals else None,
            "last_good_state": self.engine._last_good_state,
            "balance": self.engine.balance,
            "starting_balance": self.engine.starting_balance,
        }

    def export_all(self) -> str:
        """Export all evaluation data as JSON."""
        return self.engine.to_json()