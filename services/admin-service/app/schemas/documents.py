from pydantic import BaseModel
class DocumentCreate(BaseModel):
    title: str
    version: str
    content: str
    acceptance_required: bool = True
