from typing import List, Optional, Any
from agents import Agent

RESEARCH_AGENT_INSTRUCTIONS = """You are an expert Research & Evidence Extraction Agent.
When given a research topic and production rules:
1. Use the deep_research tool to retrieve high-quality web sources.
2. Structure the core factual findings into a rigorous research report.
3. CRITICAL REQUIREMENT: At the end of your report, provide a structured JSON block (delimited by ```json ... ```) 
   containing a list of extracted key claims with their evidence links in the Claim-Evidence format:
```json
[
  {
    "statement": "Detailed factual assertion",
    "claim_type": "product_general_availability / financial_metrics / tech_parameter / timeline",
    "scope": {"product": "...", "release_stage": "Preview/Beta/GA"},
    "valid_time": {"effective_from": "YYYY-MM-DD"},
    "status": "corroborated",
    "evidence": [{"url": "https://...", "title": "...", "snippet": "...", "source_type": "first_party_announcement/media", "stance": "supports"}],
    "confidence": 0.95
  }
]
```
"""

def create_research_agent(model: Any, tools: Optional[List[Any]] = None) -> Agent:
    """Instantiate Research & Extraction Agent."""
    return Agent(
        name="research_agent",
        model=model,
        instructions=RESEARCH_AGENT_INSTRUCTIONS,
        tools=tools or []
    )
