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


class ShortURLData(BaseModel):
    url: str = Field(..., description="The complete shortened URL")
    code: str = Field(..., description="The generated short code/slug for the URL")

class QRGenerationData(BaseModel):
    qr_url: str = Field(..., description="Generated QR code image URL")
    encoded_url: str = Field(..., description="Original URL encoded in the QR code")

    short_url: Optional[ShortURLData] = Field(None, description="Shortened URL details")

class DeletionData(BaseModel):
    deleted_id: str = Field(..., description="ID of deleted resource")


ImageGenerationResponse = SuccessResponse[ImageGenerationData]
QRGenerationResponse = SuccessResponse[QRGenerationData]
DeletionResponse = SuccessResponse[DeletionData]
HealthResponse = SuccessResponse[HealthData]
ShortURLResponse = SuccessResponse[ShortURLData]


# ============================================================================
# V1 API RESPONSE LAYER (Unified Envelope, camelCase)
# ============================================================================

import uuid
from typing import Dict, Any
from src.ghibli_portrait.models.schemas import ApiSuccessResponse, ApiErrorResponse, ApiError


def success_response(
    message: str,
    data: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None
) -> ApiSuccessResponse:
    """
    Creates a standardized V1 API success response.

    Args:
        message: Human-readable success message
        data: Optional response payload (must be camelCase dictionary)
        request_id: Optional request ID (auto-generated if not provided)

    Returns:
        ApiSuccessResponse with unified envelope structure
    """
    return ApiSuccessResponse(
        success=True,
        message=message,
        requestId=request_id or str(uuid.uuid4()),
        data=data
    )


def error_response(
    message: str,
    errors: List[ApiError],
    request_id: Optional[str] = None
) -> ApiErrorResponse:
    """
    Creates a standardized V1 API error response.

    Args:
        message: High-level error summary
        errors: List of detailed error objects
        request_id: Optional request ID (auto-generated if not provided)

    Returns:
        ApiErrorResponse with unified envelope structure
    """
    return ApiErrorResponse(
        success=False,
        message=message,
        requestId=request_id or str(uuid.uuid4()),
        errors=errors
    )


def validation_error_response(
    field: str,
    message: str,
    code: str = "VALIDATION_ERROR",
    request_id: Optional[str] = None
) -> ApiErrorResponse:
    """
    Helper for single validation errors.

    Args:
        field: Field name that failed validation
        message: Validation error message
        code: Error code (default: VALIDATION_ERROR)
        request_id: Optional request ID

    Returns:
        ApiErrorResponse with single validation error
    """
    return error_response(
        message="Request validation failed",
        errors=[ApiError(code=code, field=field, message=message)],
        request_id=request_id
    )


def internal_error_response(
    message: str = "An internal error occurred",
    request_id: Optional[str] = None
) -> ApiErrorResponse:
    """
    Helper for internal server errors.

    Args:
        message: Error message
        request_id: Optional request ID

    Returns:
        ApiErrorResponse with INTERNAL_ERROR code
    """
    return error_response(
        message=message,
        errors=[ApiError(
            code="INTERNAL_ERROR",
            field=None,
            message="An unexpected error occurred. Please contact support with the requestId."
        )],
        request_id=request_id
    )