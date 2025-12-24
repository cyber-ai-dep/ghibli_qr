from enum import Enum
from pydantic import Field, BaseModel

class Quality(str, Enum):
     BASIC = 'basic'
     HIGH =  'high'

class AspectRatio(str, Enum):
    _1_1 = '1:1'
    _4_3 = '4:3'
    _3_4 = '3:4'
    _16_9 = '16:9'
    _9_16 = '9:16'
    _2_3 = '2:3'
    _3_2 = '3:2'
    _21_9 = '21:9'
