from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

from src.ghibli_portrait.models.schemas import AspectRatio, Quality

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    code: int = 200
    data: T
    message: Optional[str] = None


class HealthData(BaseModel):
    status: str = Field(default="healthy", description="Service health status")
    timestamp: Optional[str] = Field(default=datetime.utcnow())


class ImageGenerationData(BaseModel):
    """Data returned after successful image generation"""

    result_urls: List[str] = Field(..., description="Generated image URLs")
    cost_time: int = Field(..., description="Processing time in seconds")
    model: str = Field(..., description="Model used for generation")

    quality: Optional[Quality] = Quality.BASIC
    aspect_ratio: Optional[AspectRatio] = AspectRatio._1_1


class QRGenerationData(BaseModel):
    qr_url: str = Field(..., description="Generated QR code image URL")
    encoded_url: str = Field(..., description="URL encoded in the QR code")


class DeletionData(BaseModel):
    deleted_id: str = Field(..., description="ID of deleted resource")


ImageGenerationResponse = SuccessResponse[ImageGenerationData]
QRGenerationResponse = SuccessResponse[QRGenerationData]
DeletionResponse = SuccessResponse[DeletionData]
HealthResponse = SuccessResponse[HealthData]
