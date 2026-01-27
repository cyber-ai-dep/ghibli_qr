from datetime import datetime, timezone
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
    timestamp: Optional[str] = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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

from typing import Dict, Any
from src.ghibli_portrait.models.schemas import (
    ApiSuccessResponse,
    ApiErrorResponse,
    ApiError,
    ErrorType,
    ErrorStage,
)


def _utc_timestamp() -> str:
    """Generate ISO 8601 UTC timestamp."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def success_response(
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> ApiSuccessResponse:
    """
    Creates a standardized V1 API success response.

    Response field order: success, data, message, errors, timestamp

    Args:
        message: Human-readable success message
        data: Optional response payload (must be camelCase dictionary)

    Returns:
        ApiSuccessResponse with unified envelope structure
    """
    return ApiSuccessResponse(
        success=True,
        data=data,
        message=message,
        errors=None,
        timestamp=_utc_timestamp(),
    )


def error_response(
    message: str,
    errors: List[ApiError],
) -> ApiErrorResponse:
    """
    Creates a standardized V1 API error response.

    Response field order: success, data, message, errors, timestamp

    Args:
        message: High-level error summary
        errors: List of detailed error objects (must include type and stage)

    Returns:
        ApiErrorResponse with unified envelope structure
    """
    return ApiErrorResponse(
        success=False,
        data=None,
        message=message,
        errors=errors,
        timestamp=_utc_timestamp(),
    )


def validation_error_response(
    field: Optional[str],
    message: str,
    code: str,
    stage: ErrorStage = ErrorStage.INPUT,
    error_type: ErrorType = ErrorType.VALIDATION_ERROR,
) -> ApiErrorResponse:
    """
    Helper for single validation errors.

    Args:
        field: Field name that failed validation (camelCase)
        message: Validation error message
        code: SCREAMING_SNAKE_CASE error code
        stage: Pipeline stage where error occurred
        error_type: Error classification type

    Returns:
        ApiErrorResponse with single validation error
    """
    return error_response(
        message="Request validation failed",
        errors=[ApiError(
            code=code,
            type=error_type,
            stage=stage,
            field=field,
            message=message
        )],
    )


def external_error_response(
    message: str,
    code: str,
    stage: ErrorStage,
    detail: str,
) -> ApiErrorResponse:
    """
    Helper for external API errors (KIE API failures, timeouts).

    Args:
        message: High-level error summary
        code: SCREAMING_SNAKE_CASE error code
        stage: Pipeline stage where error occurred
        detail: Detailed error message

    Returns:
        ApiErrorResponse with EXTERNAL_ERROR type
    """
    return error_response(
        message=message,
        errors=[ApiError(
            code=code,
            type=ErrorType.EXTERNAL_ERROR,
            stage=stage,
            field=None,
            message=detail
        )],
    )


def internal_error_response(
    message: str = "An internal error occurred",
    stage: ErrorStage = ErrorStage.ORCHESTRATION,
) -> ApiErrorResponse:
    """
    Helper for internal server errors.

    Args:
        message: Error message
        stage: Pipeline stage where error occurred

    Returns:
        ApiErrorResponse with SYSTEM_ERROR type
    """
    return error_response(
        message=message,
        errors=[ApiError(
            code="INTERNAL_ERROR",
            type=ErrorType.SYSTEM_ERROR,
            stage=stage,
            field=None,
            message="An unexpected error occurred. Please try again."
        )],
    )