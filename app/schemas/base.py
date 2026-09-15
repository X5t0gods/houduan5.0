"""Schema 基类与类型别名 - 解决 Pydantic v2 序列化陷阱。

阶段 1 起，所有响应模型的金额/数量/时间/比率字段必须使用这些别名，
禁止裸用 Decimal / datetime，否则前端会收到字符串而非 number。

必需使用别名的字段清单：
- Money     → 金额（DECIMAL(12,2) / DECIMAL(14,2)）
- Qty       → 数量（DECIMAL(12,3) / DECIMAL(14,3)）
- Rate      → 比率/系数（DECIMAL(5,4) / (8,4) / (8,5) / (10,4) / (10,5) / (12,4)）
- DateTimeStr → DATETIME
- DateStr     → DATE
- Flag        → TINYINT 状态位

关键陷阱说明：
- Pydantic v2 默认把 Decimal 序列化成字符串 "12.30"，前端类型是 number 且直接参与运算会 NaN
- datetime 默认输出 ISO 格式（带 T），接口约定是 YYYY-MM-DD HH:mm:ss
- tinyint 被声明成 bool 输出 true/false，前端类型是 number 会类型不匹配
- Rate 不能用 Money：后者只保留 2 位会把 lift=1.2345 截断成 1.23，直接影响 BI 指标
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

# 比率/系数：折扣率、提升度、置信度、换算系数、毛利率、推荐得分等
# 对应 DECIMAL(5,4) / (8,4) / (8,5) / (10,4) / (10,5) / (12,4) 这些精度到 4–5 位的字段
# 为什么单独定义：若误用 Money（2 位）会把 lift=1.2345 截断成 1.23，
# 直接影响智能分析模块的指标正确性与论文实验数据（阶段 9）。
# 为什么取 6 位：涵盖库内最大精度（5 位）并留一位冗余，避免四舍五入引入误差。
Rate = Annotated[
    Decimal,
    PlainSerializer(lambda v: float(round(v, 6)), return_type=float),
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
