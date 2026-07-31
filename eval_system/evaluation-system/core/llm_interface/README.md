# LLM Interface

Interface for LLM-driven research and thinking.

## Capabilities

1. **Deep Research**: research any topic with web search
2. **Strategy Brainstorming**: generate new strategy ideas
3. **Assumption Challenging**: question every assumption
4. **Unknown Factor Discovery**: find what the system doesn't know
5. **Cross-Session Learning**: share knowledge across sessions
6. **Agentic Loop**: research → evaluate → adjust → repeat

## Usage

```python
from evaluation_system.core.llm_interface import LLMInterface

llm = LLMInterface(
    model="inclusionai/ling-3.0-flash:free",
    provider="gitlawb-opengateway",
)

# Research a topic
result = llm.research("Roll Measure predictability in crypto futures 2026")

# Brainstorm strategies
ideas = llm.brainstorm("aggressive scalping strategies for $10 balance")

# Challenge assumptions
challenges = llm.challenge_assumptions("current evaluation system")

# Discover unknown factors
unknowns = llm.discover_unknowns("trading bot evaluation")
```

## Prompt Templates

### Research Prompt
```
Research this topic deeply and comprehensively. 
Find academic papers, practical guides, and real-world data.
Focus on what makes trading bots profitable and what makes them fail.
Think differently about market microstructure.

Topic: {topic}
```

### Brainstorm Prompt
```
Brainstorm creative trading strategies for aggressive scalping.
The bot has $10 starting balance, 7bps RT fee, paper trading mode.
Target: growing balance no matter what. No survival gates.

Current strategy: {current_strategy}
Current weaknesses: {weaknesses}

Generate ideas that are:
- Aggressive but profitable
- Fee-aware
- Fast (no blocking operations)
- Novel (think differently)
- Certain (not speculative)
```

### Challenge Assumptions Prompt
```
Challenge every assumption in this trading system.
Question what is known, what is assumed, and what is unknown.
Think about what millions of bots are doing and how that affects price.

System description: {system_description}
Current metrics: {metrics}

Find:
1. Assumptions that might be wrong
2. Unknowns that should be known
3. Blind spots in the evaluation
4. Edge sources not yet exploited
5. Feedback loops not yet considered
```