"""Schema 基类与类型别名 - 解决 Pydantic v2 序列化陷阱。

阶段 1 起，所有响应模型的金额/数量/时间字段必须使用这些别名，
禁止裸用 Decimal / datetime，否则前端会收到字符串而非 number。

关键陷阱说明：
- Pydantic v2 默认把 Decimal 序列化成字符串 "12.30"，前端类型是 number 且直接参与运算会 NaN
- datetime 默认输出 ISO 格式（带 T），接口约定是 YYYY-MM-DD HH:mm:ss
- tinyint 被声明成 bool 输出 true/false，前端类型是 number 会类型不匹配
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

# ---------- 类型别名 ----------

# 金额：内部 Decimal 运算，输出 JSON number（两位小数）
# 为什么用 PlainSerializer：Pydantic v2 默认把 Decimal 序列化为字符串，
# 前端 types/index.ts 定义的金额字段是 number 类型，字符串会导致运算错误
Money = Annotated[
    Decimal,
    PlainSerializer(lambda v: float(round(v, 2)), return_type=float),
]

# 数量：内部 Decimal 运算，输出 JSON number（三位小数，支持称重商品）
Qty = Annotated[
    Decimal,
    PlainSerializer(lambda v: float(round(v, 3)), return_type=float),
]

# 时间：输出 YYYY-MM-DD HH:mm:ss（接口约定格式，不是 ISO 的带 T 格式）
DateTimeStr = Annotated[
    datetime,
    PlainSerializer(lambda v: v.strftime("%Y-%m-%d %H:%M:%S"), return_type=str),
]

# 日期：输出 YYYY-MM-DD
DateStr = Annotated[
    datetime,
    PlainSerializer(lambda v: v.strftime("%Y-%m-%d"), return_type=str),
]

# tinyint 标志位：接口约定是 number（0/1），不是 boolean
# 为什么不用 bool：前端 types/index.ts 中 status/is_xxx 字段类型是 number，
# 若后端输出 true/false，前端做 === 1 判断会失败
Flag = int


class BaseSchema(BaseModel):
    """所有响应模型的基类。

    配置说明：
    - from_attributes=True: 支持从 ORM 对象直接构造（阶段 1 起使用）
    - populate_by_name=True: 允许用字段名或别名填充
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )
