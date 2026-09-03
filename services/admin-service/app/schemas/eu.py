from pydantic import BaseModel
class EUCreate(BaseModel):
    avms_enabled: bool = True
    dsa_enabled: bool = True
    dma_enabled: bool = False
