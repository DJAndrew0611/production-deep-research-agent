from typing import List, Optional, Any
from agents import Agent

ELABORATION_AGENT_INSTRUCTIONS = """You are an expert Content Enhancer specializing in research elaboration.
When given verified evidence and an initial research report:
1. Deepen the analysis with detailed explanations of core mechanisms and architectures.
2. Provide concrete real-world case studies, commercial deployment examples, and comparative benchmarks.
3. Structure sections logically with executive summaries, technical breakdowns, and strategic projections.
4. Maintain strict factual fidelity to the verified evidence while significantly expanding depth.
"""

def create_elaboration_agent(model: Any, tools: Optional[List[Any]] = None) -> Agent:
    """Instantiate Elaboration Agent."""
    return Agent(
        name="elaboration_agent",
        model=model,
        instructions=ELABORATION_AGENT_INSTRUCTIONS,
        tools=tools or []
    )
