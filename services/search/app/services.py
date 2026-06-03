"""Search service business logic."""
from uuid import UUID
from typing import List, Dict, Optional
from elasticsearch import AsyncElasticsearch
from app.repositories import SearchQueryRepository, SearchIndexRepository

class SearchService:
    def __init__(self, es_client: AsyncElasticsearch, query_repo: SearchQueryRepository, index_repo: SearchIndexRepository):
        self.es = es_client
        self.query_repo = query_repo
        self.index_repo = index_repo
    
    async def search(self, user_id: UUID, query: str, content_type: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Full-text search via Elasticsearch."""
        must_clauses = [{"multi_match": {"query": query, "fields": ["title^2", "description", "actors", "director"]}}]
        if content_type:
            must_clauses.append({"term": {"content_type": content_type}})
        
        body = {"query": {"bool": {"must": must_clauses}}, "size": limit}
        results = await self.es.search(index="content", body=body)
        
        # Log search query
        await self.query_repo.create(user_id, query, len(results["hits"]["hits"]))
        
        return [hit["_source"] for hit in results["hits"]["hits"]]
    
    async def index_content(self, content_id: UUID, title: str, description: str, content_type: str, **metadata):
        """Index content in Elasticsearch."""
        doc = {"title": title, "description": description, "content_type": content_type, **metadata}
        await self.es.index(index="content", id=str(content_id), document=doc)
        await self.index_repo.create(content_id, title, content_type, description)
