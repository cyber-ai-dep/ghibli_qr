from enum import Enum
from typing import List
from pydantic import Field, BaseModel


ImgURLs = List[str]


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


class Image2GhibliRequest(BaseModel):
    img_urls: ImgURLs = Field(
        ...,
        min_length=1,
        examples=[
            ["https://images.pexels.com/photos/1563356/pexels-photo-1563356.jpeg"]
        ],
        description="List of image URLs to transform to Ghibli style art",
    )
    
    quality: Quality = Field(
        default=Quality.BASIC,
        description="Img quality: high (4K) or basic (2K) ― recommended",
    )
    
    aspect_ratio: AspectRatio = Field(
        default=AspectRatio._1_1,
        description="Width to height ratio of the image",
    )