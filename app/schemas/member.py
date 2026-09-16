"""会员模块的 Pydantic 模型（阶段 7 交付物）。

⚠️ 与前端 `qianduan/src/types/index.ts:299-360` 的 Member / MemberLevel /
   BalanceFlow / PointFlow 一字对齐。

⚠️ 类型别名（决策 4 + spec 六.4）：
- `Money` (2 位)：balance / gift_balance / total_consume / amount / avg_price / condition_value
- `Rate` (6 位)：discount_rate（DECIMAL(5,4)，⛔不是 Money，否则 0.9500 被截断且语义错）
- `DateStr`：birthday
- `DateTimeStr`：last_consume_at / created_at
- `Flag` (int)：gender / status / level_id

⚠️ 请求模型 extra='ignore'：前端可能多传 level_name/tags/sleeping_days 等展示字段。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, DateStr, DateTimeStr, Flag, Money, Rate

# ---------- 会员 ----------


class Member(BaseSchema):
    """会员（对应前端 types/index.ts:299-319）。"""

    id: int
    member_no: str
    phone: str
    real_name: str | None = None
    gender: Flag = 0
    birthday: DateStr | None = None
    level_id: int
    level_name: str | None = None
    discount_rate: Rate | None = None  # DECIMAL(5,4) 用 Rate（阶段4收银会员折扣用）
    balance: Money
    gift_balance: Money
    points: int
    total_consume: Money
    consume_count: int
    last_consume_at: DateTimeStr | None = None
    source: str
    status: Flag
    tags: list[str] | None = None
    sleeping_days: int | None = Field(default=None, description="距今未消费天数（DATEDIFF）")


class MemberSearchRequest(BaseModel):
    """会员快速检索（收银台，POST /members/search）。"""

    model_config = ConfigDict(extra="ignore")

    phone: str = Field(min_length=1, max_length=20, description="手机号或后4位")


class MemberCreate(BaseModel):
    """会员建档（POST /members）。

    ⚠️ member_no 后端 M 取号；level_id 自动判定（新会员恒最低等级，init_balance 不算消费）。
    """

    model_config = ConfigDict(extra="ignore")

    phone: str = Field(min_length=1, max_length=20)
    real_name: str | None = Field(default=None, max_length=32)
    gender: Flag = 0
    birthday: date | None = None
    source: str = Field(default="STORE", description="STORE/REFER/ONLINE")
    remark: str | None = Field(default=None, max_length=255)
    init_balance: Decimal | None = Field(default=None, ge=0, description="建档即充值（写RECHARGE流水）")


class MemberUpdate(BaseModel):
    """修改会员（PUT /members/{id}）。

    ⛔ 不含 member_no/balance/gift_balance/points/total_consume/consume_count/level_id
       （这些只能由业务变动，前端传了也忽略 —— schema 里就不定义，防篡改）。
    """

    model_config = ConfigDict(extra="ignore")

    real_name: str | None = Field(default=None, max_length=32)
    gender: Flag | None = None
    birthday: date | None = None
    phone: str | None = Field(default=None, max_length=20)
    remark: str | None = Field(default=None, max_length=255)


class MemberStatusUpdate(BaseModel):
    """挂失/冻结（PUT /members/{id}/status）。"""

    model_config = ConfigDict(extra="ignore")

    status: Flag = Field(description="0冻结/1正常/2挂失")


# ---------- 会员等级 ----------


class MemberLevel(BaseSchema):
    """会员等级（对应前端 types/index.ts:321-330）。"""

    id: int
    level_name: str
    level_value: int
    discount_rate: Rate  # DECIMAL(5,4)
    upgrade_condition: str
    condition_value: Money
    benefit_desc: str | None = None
    status: Flag


class MemberLevelUpdate(BaseModel):
    """修改会员等级（PUT /members/levels/{id}）。

    ⚠️ discount_rate ∈ (0,1]；upgrade_condition ∈ {CONSUME, POINTS}。
    ⚠️ 改 discount_rate 会影响阶段4收银金额。
    """

    model_config = ConfigDict(extra="ignore")

    level_name: str | None = Field(default=None, max_length=32)
    discount_rate: Decimal | None = Field(default=None, description="折扣率 (0,1]")
    upgrade_condition: str | None = Field(default=None, description="CONSUME/POINTS")
    condition_value: Decimal | None = Field(default=None, ge=0)
    benefit_desc: str | None = Field(default=None, max_length=255)
    sort: int | None = None
    status: Flag | None = None


# ---------- 充值（CP2） ----------


class RechargeParams(BaseModel):
    """会员充值（POST /members/{id}/recharge，幂等）。

    ⚠️ request_id 由前端拦截器自动注入（可选，curl 测试可不带）；
       为空时不做幂等保护（MySQL UNIQUE 允许多 NULL）。
    """

    model_config = ConfigDict(extra="ignore")

    amount: Decimal = Field(default=Decimal("0"), ge=0, description="本金充值额")
    gift_amount: Decimal = Field(default=Decimal("0"), ge=0, description="赠送额")
    pay_method: str = Field(description="CASH/WECHAT/ALIPAY/CARD/TRANSFER")
    remark: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64, description="幂等键（前端注入）")


class RechargeResult(BaseSchema):
    """充值响应（充值后的余额）。"""

    balance: Money
    gift_balance: Money


class PointAdjustParams(BaseModel):
    """积分手工调整（POST /members/{id}/points/adjust）。"""

    model_config = ConfigDict(extra="ignore")

    points: int = Field(description="正数增加、负数扣减")
    reason: str = Field(min_length=1, max_length=255, description="调整原因（必填）")


class PointAdjustResult(BaseSchema):
    """积分调整响应。"""

    points: int = Field(description="调整后的积分")


# ---------- 流水（CP2） ----------


class BalanceFlow(BaseSchema):
    """储值流水（对应前端 types/index.ts:332-346）。"""

    id: int
    member_id: int
    member_name: str | None = None
    member_no: str | None = None
    flow_type: str
    amount: Money
    gift_amount: Money
    direction: int
    before_balance: Money
    after_balance: Money
    pay_method: str | None = None
    source_no: str | None = None
    operator_name: str | None = None
    remark: str | None = None
    created_at: DateTimeStr


class PointFlow(BaseSchema):
    """积分流水（对应前端 types/index.ts:348-360）。"""

    id: int
    member_id: int
    member_name: str | None = None
    member_no: str | None = None
    flow_type: str
    points: int
    direction: int
    before_points: int
    after_points: int
    source_no: str | None = None
    operator_name: str | None = None
    remark: str | None = None
    created_at: DateTimeStr


# ---------- 消费档案（CP2） ----------


class FavoriteCategory(BaseSchema):
    """偏好品类（profile）。ratio 是 0~1 小数（不是百分数）。"""

    name: str
    ratio: float


class TopProduct(BaseSchema):
    """常购商品（profile）。"""

    name: str
    qty: float


class MemberProfile(BaseSchema):
    """会员消费档案（GET /members/{id}/profile）。

    ⚠️ avg_price = total_consume / consume_count（consume_count=0 → 0，不除零）。
    ⚠️ 当前 sal_trade 无初始数据 → 3 个数组可能为空（正常）。
    """

    member_id: int
    member_no: str | None = None
    real_name: str | None = None
    total_consume: Money
    consume_count: int
    avg_price: Money
    favorite_categories: list[FavoriteCategory] = Field(default_factory=list)
    top_products: list[TopProduct] = Field(default_factory=list)
