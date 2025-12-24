from pydantic import BaseModel

class GenericResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict = {}


class SuccessResp(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict = {}          # or Data if you want a nested model
