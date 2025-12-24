from pydantic import BaseModel

class GenericResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict = {}
