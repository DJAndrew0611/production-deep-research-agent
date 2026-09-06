import os
from typing import Dict, Any, List, Optional, Callable
from firecrawl import FirecrawlApp
from agents.tool import function_tool
from schemas import EvidenceCorpus, EvidenceDocument
from core.context import RunContext

def build_corpus_from_sources(sources: List[Dict[str, Any]], analysis_text: str = "") -> EvidenceCorpus:
    """Construct an indexed EvidenceCorpus from raw web sources."""
    docs = []
    for s in sources:
        url = s.get("url", "")
        if not url:
            continue
        title = s.get("title")
        snippet = s.get("snippet") or s.get("description") or ""
        if not snippet and analysis_text:
            snippet = analysis_text[:300]
        docs.append(EvidenceDocument(
            url=url,
            title=title,
            content=snippet,
            domain=url.split("//")[-1].split("/")[0]
        ))
    return EvidenceCorpus(documents=docs)

def create_deep_research_tool(
    api_key_getter: Callable[[], str],
    on_activity: Optional[Callable[[Any], None]] = None,
    context: Optional[RunContext] = None,
    corpus: Optional[EvidenceCorpus] = None
):
    """
    Factory creating a Firecrawl deep_research function tool with strict_mode=False.
    Integrated with RunContext search call tracking, safe fallback, and live EvidenceCorpus accumulation.
    """
    @function_tool(strict_mode=False)
    async def deep_research(query: str, max_depth: int = 3, time_limit: int = 180, max_urls: int = 10) -> Dict[str, Any]:
        """
        Perform comprehensive web research using Firecrawl's deep research or search endpoint.
        """
        try:
            api_key = api_key_getter()
            if not api_key:
                return {"error": "Firecrawl API key is missing.", "success": False}

            if context:
                context.track_search_call()

            firecrawl_app = FirecrawlApp(api_key=api_key)

            def _activity_cb(activity):
                if on_activity:
                    on_activity(activity)

            # 1. Attempt Firecrawl Deep Research
            research_func = None
            if hasattr(firecrawl_app, "v1") and hasattr(firecrawl_app.v1, "deep_research"):
                research_func = firecrawl_app.v1.deep_research
            elif hasattr(firecrawl_app, "deep_research"):
                research_func = firecrawl_app.deep_research

            if research_func is not None:
                try:
                    res = research_func(
                        query=query,
                        max_depth=max_depth,
                        time_limit=time_limit,
                        max_urls=max_urls,
                        on_activity=_activity_cb
                    )

                    if hasattr(res, "model_dump"):
                        res_dict = res.model_dump()
                    elif isinstance(res, dict):
                        res_dict = res
                    else:
                        res_dict = vars(res)

                    data = res_dict.get("data") or {}
                    final_analysis = data.get("finalAnalysis", "") if isinstance(data, dict) else str(data)
                    sources = res_dict.get("sources") or (data.get("sources") if isinstance(data, dict) else []) or []

                    if final_analysis or sources:
                        if corpus is not None:
                            corpus.retrieval_queries.append(query)
                            for s in sources:
                                s_url = s.get("url") if isinstance(s, dict) else getattr(s, "url", "")
                                s_title = s.get("title", "") if isinstance(s, dict) else getattr(s, "title", "")
                                if s_url and (s_url.startswith("http://") or s_url.startswith("https://")):
                                    domain_part = s_url.split("//")[-1].split("/")[0].lower()
                                    corpus.add_document(EvidenceDocument(
                                        url=s_url,
                                        title=s_title or "Official Source",
                                        content=final_analysis[:2500] if final_analysis else "",
                                        domain=domain_part
                                    ))
                        return {
                            "success": True,
                            "final_analysis": final_analysis,
                            "sources_count": len(sources),
                            "sources": sources
                        }
                except Exception:
                    pass

            # 2. Fallback: Firecrawl Search & Scrape
            search_res = firecrawl_app.search(
                query=query,
                limit=max_urls,
                scrape_options={"formats": ["markdown"]}
            )

            if hasattr(search_res, "model_dump"):
                search_dict = search_res.model_dump()
            elif isinstance(search_res, dict):
                search_dict = search_res
            else:
                search_dict = vars(search_res)

            web_items = search_dict.get("web") or []
            combined_texts = []
            extracted_sources = []

            for idx, item in enumerate(web_items, 1):
                if isinstance(item, dict):
                    meta = item.get("metadata") or {}
                    url = meta.get("source_url") or item.get("url") or ""
                    title = meta.get("title") or item.get("title") or f"Source {idx}"
                    content = item.get("markdown") or item.get("description") or ""
                else:
                    meta = getattr(item, "metadata", None)
                    url = getattr(meta, "source_url", "") if meta else getattr(item, "url", "")
                    title = getattr(meta, "title", "") if meta else getattr(item, "title", f"Source {idx}")
                    content = getattr(item, "markdown", "") or getattr(item, "description", "")

                if url and (url.startswith("http://") or url.startswith("https://")):
                    snippet_text = content[:300].strip() if content else title
                    extracted_sources.append({"url": url, "title": title, "snippet": snippet_text})
                    if corpus is not None:
                        domain_part = url.split("//")[-1].split("/")[0].lower()
                        corpus.add_document(EvidenceDocument(
                            url=url,
                            title=title,
                            content=content[:2500] if content else title,
                            domain=domain_part
                        ))

                if content:
                    combined_texts.append(f"### [来源 {idx}] {title}\nURL: {url}\n\n{content[:2500]}")

            if corpus is not None:
                corpus.retrieval_queries.append(query)

            synthesis = "\n\n---\n\n".join(combined_texts)
            if not synthesis:
                synthesis = f"已检索全网，未检索到针对 '{query}' 的新网页。"

            return {
                "success": True,
                "final_analysis": synthesis,
                "sources_count": len(extracted_sources),
                "sources": extracted_sources
            }

        except Exception as e:
            if context:
                context.record_error("search_tools", e)
            return {"error": str(e), "success": False}

    return deep_research
