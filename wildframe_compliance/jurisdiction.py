import enum

class Jurisdiction(str, enum.Enum):
    GLOBAL = "global"
    EU = "EU"
    US = "US"
    IN = "IN"
    SG = "SG"
