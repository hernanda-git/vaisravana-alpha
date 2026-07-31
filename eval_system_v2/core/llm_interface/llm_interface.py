"""
LLM Interface v2
Interface for LLM-driven research, brainstorming, and assumption challenging.

Uses the configured LLM provider to:
1. Research unknown factors deeply
2. Brainstorm new strategies
3. Challenge assumptions in the evaluation system
4. Discover unknown factors
5. Run agentic brainstorming loops
"""

import json
from datetime import datetime, timezone
from typing import Optional


class LLMInterface:
    """
    LLM interface for research and thinking.

    This interface connects to the configured LLM provider
    and provides methods for deep research and brainstorming.
    """

    def __init__(
        self,
        model: str = "inclusionai/ling-3.0-flash:free",
        provider: str = "gitlawb-opengateway",
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.research_history = []

    def research(self, topic: str, depth: str = "deep") -> dict:
        """
        Research a topic deeply using the LLM.

        Args:
            topic: The research topic
            depth: "quick", "deep", or "exhaustive"

        Returns:
            Research result with findings, sources, and confidence
        """
        research_result = {
            "topic": topic,
            "depth": depth,
            "model": self.model,
            "provider": self.provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }

        # In production, this would call the LLM API
        # For now, return the research request structure
        research_result["status"] = "ready_for_execution"
        research_result["prompt"] = self._build_research_prompt(topic, depth)

        self.research_history.append(research_result)
        return research_result

    def brainstorm(self, topic: str, constraints: Optional[dict] = None) -> dict:
        """
        Brainstorm creative ideas for a topic.

        Args:
            topic: What to brainstorm about
            constraints: Optional constraints (e.g., max trades, budget, risk)

        Returns:
            Brainstorming results with ideas ranked by feasibility
        """
        result = {
            "topic": topic,
            "constraints": constraints or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ready_for_execution",
            "prompt": self._build_brainstorm_prompt(topic, constraints or {}),
        }
        return result

    def challenge_assumptions(self, system_description: str, current_metrics: Optional[dict] = None) -> dict:
        """
        Challenge every assumption in the system.

        Args:
            system_description: Description of the system
            current_metrics: Current performance metrics

        Returns:
            Assumption challenges with severity and suggested fixes
        """
        result = {
            "system_description": system_description,
            "current_metrics": current_metrics or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ready_for_execution",
            "prompt": self._build_challenge_prompt(system_description, current_metrics or {}),
        }
        return result

    def discover_unknowns(self, domain: str, known_factors: Optional[list] = None) -> dict:
        """
        Discover what is unknown in a domain.

        Args:
            domain: The domain to explore
            known_factors: List of known factors

        Returns:
            Unknown factors categorized by discoverability
        """
        result = {
            "domain": domain,
            "known_factors": known_factors or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ready_for_execution",
            "prompt": self._build_discover_prompt(domain, known_factors or []),
        }
        return result

    def agentic_loop(self, initial_topic: str, max_iterations: int = 5) -> dict:
        """
        Run an agentic brainstorming loop.

        Args:
            initial_topic: Starting topic for research
            max_iterations: Maximum number of iterations

        Returns:
            Loop results with all iterations documented
        """
        loop_result = {
            "initial_topic": initial_topic,
            "max_iterations": max_iterations,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "iterations": [],
            "final_recommendation": None,
        }

        current_topic = initial_topic
        for i in range(max_iterations):
            iteration = {
                "iteration": i + 1,
                "topic": current_topic,
                "status": "pending",
            }

            # Research
            research = self.research(current_topic, depth="deep")
            iteration["research"] = research

            # Brainstorm based on research
            brainstorm = self.brainstorm(f"Ideas for {current_topic}")
            iteration["brainstorm"] = brainstorm

            # Challenge assumptions
            challenge = self.challenge_assumptions(
                f"System for {current_topic}",
                {"iteration": i + 1},
            )
            iteration["challenge"] = challenge

            loop_result["iterations"].append(iteration)

            # Next topic would be determined by research findings
            # For now, use a simple heuristic
            if i < max_iterations - 1:
                current_topic = f"{current_topic} — deeper analysis"

        loop_result["status"] = "complete"
        return loop_result

    def _build_research_prompt(self, topic: str, depth: str) -> str:
        prompts = {
            "quick": f"Research {topic} briefly. Give 3 key findings.",
            "deep": f"Research {topic} deeply and comprehensively. Find academic papers, practical guides, and real-world data. Focus on what makes trading bots profitable and what makes them fail. Think differently about market microstructure.",
            "exhaustive": f"Exhaustively research {topic}. Cover all aspects: theory, practice, edge cases, failures, and unknowns. Provide academic references and practical implications.",
        }
        return prompts.get(depth, prompts["deep"])

    def _build_brainstorm_prompt(self, topic: str, constraints: dict) -> str:
        return f"""Brainstorm creative ideas for: {topic}

Constraints:
{json.dumps(constraints, indent=2)}

Generate ideas that are:
- Aggressive but profitable
- Fee-aware
- Fast (no blocking operations)
- Novel (think differently)
- Certain (not speculative)

Rank ideas by expected impact on balance growth.""".strip()

    def _build_challenge_prompt(self, system_description: str, current_metrics: dict) -> str:
        return f"""Challenge every assumption in this trading system.

System description:
{system_description}

Current metrics:
{json.dumps(current_metrics, indent=2)}

Find:
1. Assumptions that might be wrong
2. Unknowns that should be known
3. Blind spots in the evaluation
4. Edge sources not yet exploited
5. Feedback loops not yet considered

Be thorough and honest. Question everything.""".strip()

    def _build_discover_prompt(self, domain: str, known_factors: list) -> str:
        return f"""Discover what is unknown in the domain: {domain}

Known factors:
{json.dumps(known_factors, indent=2)}

Find:
1. Factors that are known but not implemented
2. Factors that are unknown but discoverable
3. Factors that are unknown and unknowable (for now)
4. Factors that are known but misunderstood

Categorize each finding by priority and actionable status.""".strip()