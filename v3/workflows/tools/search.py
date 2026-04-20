import os
from duckduckgo_search import DDGS

def _search_tavily(keywords, **kwargs):
    from tavily import TavilyClient
    client = TavilyClient()
    response = client.search(
        query=keywords,
        max_results=kwargs.get("max_results", 8),
        topic="news",
    )
    results = []
    for index, result in enumerate(response.get("results", [])):
        results.append({
            "id": index,
            "title": result.get("title", ""),
            "brief": result.get("content", ""),
            "url": result.get("url", ""),
            "source": result.get("source", result.get("url", "")),
            "date": result.get("published_date", ""),
        })
    return results

def _search_ddgs(keywords, **kwargs):
    results = []
    with DDGS(proxy=kwargs.get("proxy", None)) as ddgs:
        for index, result in enumerate(
            ddgs.news(
                keywords,
                max_results=kwargs.get("max_results", 8),
                timelimit=kwargs.get("timelimit", "d"),
            )
        ):
            results.append({
                "id": index,
                "title": result["title"],
                "brief": result["body"],
                "url": result["url"],
                "source": result["source"],
                "date": result["date"],
            })
    return results

def search(keywords, **kwargs):
    provider = kwargs.get("provider", None)
    use_tavily = provider == "tavily" or (provider is None and os.environ.get("TAVILY_API_KEY"))
    try:
        if use_tavily:
            return _search_tavily(keywords, **kwargs)
        else:
            return _search_ddgs(keywords, **kwargs)
    except Exception as e:
        if "logger" in kwargs:
            kwargs["logger"].error(f"[Search]: Can not search '{ keywords }'.\tError: { str(e) }")
        return []
