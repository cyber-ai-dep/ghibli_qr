from pydantic import BaseModel, Field


class GenericResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict = Field(
        default_factory=dict,
        example={
            "taskId": "af11b48786cb5bb6c09e3aecf148599f",
            "recordId": "af11b48786cb5b4236403aecf148599f",
        },
    )


class TaskCreatedData(BaseModel):
    taskId: str = Field("af11b48786cb5bb6c6403aecf148599f")
    recordId: str = Field("af11b48786cb5bb6c6403aecf148599f")


class CreatedTaskResponse(GenericResponse):
    data: TaskCreatedData = TaskCreatedData()
