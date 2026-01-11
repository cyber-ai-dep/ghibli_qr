from enum import Enum
import json
from typing import List, Optional
from pydantic import Field, BaseModel

from src.ghibli_portrait.config import Settings


ImgURLs = List[str]
s = Settings()

class Quality(str, Enum):
    BASIC = "basic"
    HIGH = "high"


class AspectRatio(str, Enum):
    _1_1 = "1:1"
    _4_3 = "4:3"
    _3_4 = "3:4"
    _16_9 = "16:9"
    _9_16 = "9:16"
    _2_3 = "2:3"
    _3_2 = "3:2"
    _21_9 = "21:9"


class QwenImageSize(str, Enum):
    """Qwen model image size options"""
    SQUARE = "square"
    SQUARE_HD = "square_hd"
    PORTRAIT_4_3 = "portrait_4_3"
    PORTRAIT_16_9 = "portrait_16_9"
    LANDSCAPE_4_3 = "landscape_4_3"
    LANDSCAPE_16_9 = "landscape_16_9"

    @staticmethod
    def from_aspect_ratio(aspect_ratio: 'AspectRatio') -> 'QwenImageSize':
        """Map AspectRatio to QwenImageSize"""
        mapping = {
            AspectRatio._1_1: QwenImageSize.SQUARE,
            AspectRatio._4_3: QwenImageSize.LANDSCAPE_4_3,
            AspectRatio._3_4: QwenImageSize.PORTRAIT_4_3,
            AspectRatio._16_9: QwenImageSize.LANDSCAPE_16_9,
            AspectRatio._9_16: QwenImageSize.PORTRAIT_16_9,
            AspectRatio._2_3: QwenImageSize.PORTRAIT_4_3,
            AspectRatio._3_2: QwenImageSize.LANDSCAPE_4_3,
            AspectRatio._21_9: QwenImageSize.LANDSCAPE_16_9,
        }
        return mapping.get(aspect_ratio, QwenImageSize.SQUARE)


class Image2GhibliRequest(BaseModel):
    img_urls: ImgURLs = Field(
        ...,
        min_length=1,
        examples=[
            ["https://images.pexels.com/photos/1563356/pexels-photo-1563356.jpeg"]
        ],
        description="List of image URLs to transform to Ghibli style art",
    )
    
    prompt: str = s.PROMPT_PIC_TO_GHIBLI

    quality: Quality = Field(
        default=Quality.BASIC,
        description="Img quality: high (4K) or basic (2K) ― recommended",
    )
    
    aspect_ratio: AspectRatio = Field(
        default=AspectRatio._1_1,
        description="Width to height ratio of the image",
    )

class TaskState(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"


class ResultUrls(BaseModel):
    """Parsed resultJson structure"""
    resultUrls: List[str]


class TaskData(BaseModel):
    completeTime: int = Field(..., description="Task completion timestamp in milliseconds")
    consumeCredits: Optional[int] = Field(None, description="Credits consumed for this task")
    costTime: int = Field(..., description="Time taken in seconds")
    createTime: int = Field(..., description="Task creation timestamp in milliseconds")
    model: str = Field(..., description="Model used for generation")
    param: str = Field(..., description="JSON string of original request parameters")
    remainedCredits: Optional[int] = Field(None, description="Remaining credits after task")
    state: TaskState = Field(..., description="Task state: success or fail")
    taskId: str = Field(..., description="Unique task identifier")
    updateTime: int = Field(..., description="Last update timestamp in milliseconds")
    
    resultJson: Optional[str] = Field(None, description="JSON string containing result URLs (success only)")
    
    failCode: Optional[str] = Field(None, description="Error code (failure only)")
    failMsg: Optional[str] = Field(None, description="Error message (failure only)")
    
    def get_result_urls(self) -> Optional[List[str]]:
        if self.resultJson:
            try:
                result = json.loads(self.resultJson)
                return result.get("resultUrls", [])
            except json.JSONDecodeError:
                return None
        return None


class CallbackRequest(BaseModel):
    code: int = Field(..., description="Response code (200=success, 501=failure)")
    data: TaskData = Field(..., description="Task data")
    msg: str = Field(..., description="Response message")
    
    @property
    def is_success(self) -> bool:
        return self.code == 200 and self.data.state == TaskState.SUCCESS
    
    @property
    def is_failure(self) -> bool:
        return self.code != 200 or self.data.state == TaskState.FAIL


class QRLockRequest(BaseModel):
    url: str = Field(
        ...,
        description="URL to encode in the QR code",
        examples=["https://example.com"]
    )
    version: Optional[int] = Field(
        None,
        ge=1,
        le=40,
        description="QR code version (1-40). If None, automatically determined based on data length"
    )
    shorten_url: Optional[bool] = Field(
        False,
        description="Whether to shorten the URL before encoding it in the QR code"
    )


class GhibliQRRequest(BaseModel):

    url: str = Field(
        ...,
        description="URL to encode in the QR code",
        examples=["https://example.com"]
    )

    img_url: str = Field(
        ...,
        min_length=1,
        examples=["https://images.pexels.com/photos/1563356/pexels-photo-1563356.jpeg"],
        description="Image URL to transform to Ghibli style art",
    )