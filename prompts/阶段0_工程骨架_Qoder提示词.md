# Qoder 提示词 · 阶段 0：后端工程骨架

> **用法**：在 Qoder 里打开工作区 `C:\Users\徐炜城\Desktop\工作空间\houduan`，切到 **Agent / Quest 模式**（允许读整个仓库、允许写文件），把下面 `======` 之间的内容**整段粘贴**发送。
>
> **建议先做一步**：单独给 Qoder 发一句「先读 `docs/06_后端接口文档.md` 第 1 章和 `docs/07_后端技术架构与开发计划.md` 第 2 章，读完只回一句话总结，先不要改任何文件」——确认它真的读进去了，再发下面的正式提示词。这样能避免它凭想象编造响应体。

---

======

## 你的角色与本次任务

你是一名资深 Python 后端工程师，长期使用 FastAPI + Pydantic v2 + SQLAlchemy 2.0 做工程化开发，习惯「类型注解齐全、文档字符串规范、先设计再编码、每步可验证」。

当前工作区是**「中小型社区超市销售管理系统（CSSMS）」的后端工程 `houduan`**。

前置条件（都已存在且已验证，**不要重新生成、不要修改**）：

- 前端工程已完成：Vue 3 + TypeScript，36 个页面，接口调用代码已定稿；
- 数据库结构已完成：MySQL 8，**61 张表 + 86 条外键**，建表脚本 `db/01_schema.sql`，初始化数据 `db/02_init_data.sql`，且已实测「从零重建 + 权限码零差异 + 库存勾稽 0 条不一致」；
- 接口契约已定稿：**128 个接口、35 个 Schema**，见 `docs/06_后端接口文档.md`；
- 技术方案已定稿：见 `docs/07_后端技术架构与开发计划.md`。

**本次只做「阶段 0：工程骨架与基础设施」，不实现任何业务接口（健康检查除外）。**

阶段 0 完工的标志：服务能启动、统一响应体生效、业务错误走统一通道、日志带 request_id、CORS 就绪、有一个能反映数据库连通状态的健康检查接口、有可运行的单元测试、`ruff` 零告警。

---

## 一、开工前必须先读的文件（权威资料，与你的既有知识冲突时以它们为准）

| 顺序 | 文件 | 你要确认的内容 |
| :--: | --- | --- |
| 1 | `docs/06_后端接口文档.md` **第 1 章 通用约定** | 统一响应体 5 个字段、28 个业务错误码、HTTP 状态码语义、分页结构、幂等约定、29 个权限码清单 |
| 2 | `docs/07_后端技术架构与开发计划.md` **第 2 章 技术架构** | 分层架构与各层职责、目录结构、技术决策 3/5/7（响应体、幂等、金额与序列化） |
| 3 | `db/01_schema.sql` **开头 60 行** | 数据库名 `community_supermarket`、字符集 `utf8mb4`、金额 `DECIMAL(12,2)`、数量 `DECIMAL(12,3)` 等设计约定 |
| 4 | `qianduan/src/api/request.ts` | 前端如何消费响应体：`code === 0` 才取 `data`；HTTP 401 清登录态跳登录页；HTTP 403/404/500 分别提示 |

> 读完后，在动手前先用 5 行以内说明你对「统一响应体 + 业务错误通道」的理解，确认无误再开始写代码。

---

## 二、已定技术决策（**不得自行更改，也不得"优化"**）

1. **数据库访问：同步 `SQLAlchemy 2.0` + `PyMySQL`，路由函数一律用 `def`**（FastAPI 会自动丢进线程池）。
   ⛔ 不要用 `asyncmy` / `aiomysql` / `AsyncSession`，**更不要同步异步混用**。
2. **不引入 `passlib`**：它已停止维护，与 `bcrypt >= 4.0` 不兼容（会报 `AttributeError: module 'bcrypt' has no attribute '__about__'`）。密码哈希与校验**直接用 `bcrypt` 库**。
   本阶段 `app/core/security.py` 只做「哈希 + 校验 + 密码规则校验」，**JWT 签发与解析留到阶段 2，本阶段不要写**。
3. **数据库表结构不由 SQLAlchemy 生成**：`db/01_schema.sql` 是结构的**唯一真相源**。
   ⛔ 本阶段**不要写任何 ORM 模型**（61 张表的模型属阶段 1），**不要调用 `Base.metadata.create_all()`**，不要引入 Alembic。
4. **业务失败一律 `HTTP 200` + 非 0 `code`**；HTTP 状态码只表达传输层/认证层：`401` 未登录、`403` 无权限、`404` 接口不存在、`500` 服务端异常。
   ⛔ 尤其注意：**登录失败将来也必须返回 200 + 1002**，绝对不能返回 401（否则前端会清登录态把用户踢回登录页）。
5. **金额与数量**：计算层用 `Decimal`，**响应 JSON 中必须是 number（不是字符串）**。
   ⛔ Pydantic v2 默认会把 `Decimal` 序列化成字符串 `"12.30"`，前端类型是 `number` 且直接参与运算，会算出错误结果。必须用下面的类型别名解决。
6. **时间**：接口约定 `YYYY-MM-DD HH:mm:ss`（不是 ISO 的带 `T` 格式）；时区统一 `Asia/Shanghai`。
7. **`tinyint` 字段**：接口中是 `number`（0/1），**不是 boolean**。响应模型里用 `int`，不要用 `bool`。

---

## 三、交付物清单

在仓库根目录（与 `db/`、`docs/` 平级）创建以下内容。**不要移动、不要修改 `db/`、`docs/`、`.workbuddy/`、`qianduan/` 下的任何文件。**

```
houduan/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 实例、生命周期、中间件、异常处理器、路由挂载
│   ├── deps.py                     # 通用依赖：get_db、分页依赖 PageParams
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               # pydantic-settings 配置（唯一配置入口）
│   │   ├── context.py              # request_id 上下文变量（contextvars）
│   │   ├── logging.py              # 日志配置 + request_id 过滤器
│   │   ├── database.py             # engine / SessionLocal / get_db
│   │   ├── error_codes.py          # 28 个业务错误码枚举
│   │   ├── exceptions.py           # BusinessError + 全部全局异常处理器
│   │   ├── response.py             # ApiResult / PageResult 构造器
│   │   ├── security.py             # bcrypt 哈希与校验（JWT 留到阶段 2）
│   │   └── permissions.py          # 29 个权限码常量（阶段 2 使用，本阶段只定义）
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── request_id.py           # RequestIdMiddleware
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py           # /api/v1 路由汇总（本阶段只挂 health）
│   │       └── health.py           # GET /api/v1/health
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseSchema + Money/Qty/DateTimeStr 类型别名
│   │   └── common.py               # ApiResult / PageResult / PageQuery 的 Pydantic 模型
│   ├── models/__init__.py          # 仅占位：一行 docstring 说明该层职责
│   ├── repositories/__init__.py    # 仅占位
│   ├── services/__init__.py        # 仅占位
│   ├── tasks/__init__.py           # 仅占位（定时任务，阶段 8 起使用）
│   └── utils/
│       ├── __init__.py
│       └── formatting.py           # 金额/数量/时间格式化的小工具
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # TestClient fixture 等
│   ├── test_core/
│   │   ├── test_response.py
│   │   ├── test_exceptions.py
│   │   └── test_security.py
│   └── test_api/
│       └── test_health.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

`models/`、`repositories/`、`services/`、`tasks/` 四个目录只放占位 `__init__.py`（文件内一行 docstring 写清该层职责与「禁止事项」），**不要写任何实现**——后续阶段会往里填。

---

## 四、逐个文件的具体要求

### 4.1 `app/core/config.py`

用 `pydantic-settings` 的 `BaseSettings`，字段与 `.env` 一一对应，**禁止任何硬编码密钥**。至少包含：

- 应用：`APP_NAME`、`APP_VERSION`、`APP_ENV`（dev/test/prod）、`DEBUG`、`API_V1_PREFIX`（默认 `/api/v1`）、`HOST`、`PORT`
- 数据库：`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`、`DB_CHARSET`（默认 `utf8mb4`）、`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_RECYCLE`、`DB_ECHO`
- 安全：`JWT_SECRET_KEY`、`JWT_ALGORITHM`（`HS256`）、`ACCESS_TOKEN_EXPIRE_MINUTES`、`REFRESH_TOKEN_EXPIRE_DAYS`（本阶段只读取不实现）
- 其它：`CORS_ORIGINS`（逗号分隔字符串 → 解析为 `list[str]`）、`LOG_LEVEL`、`LOG_DIR`、`TZ`（默认 `Asia/Shanghai`）
- 提供 `database_url` 属性，拼接 `mysql+pymysql://user:password@host:port/dbname?charset=utf8mb4`
- 用 `@lru_cache` 暴露单例 `settings`，模块级可直接 `from app.core.config import settings`

### 4.2 `app/core/context.py`

用 `contextvars.ContextVar` 保存当前请求的 `request_id`，提供 `set_request_id()` / `get_request_id()`。**中间件写入、响应构造器与日志过滤器读取。** 未初始化时返回 `None` 而不是抛错。

### 4.3 `app/core/logging.py`

- 用 `logging.config.dictConfig` 配置，控制台 + 滚动文件双输出（`logs/app.log`，按天或按大小轮转）；
- 自定义 Filter，把 `get_request_id()` 注入 `LogRecord`，格式里体现 `[%(request_id)s]`，缺失时显示 `-`；
- 日志级别取 `settings.LOG_LEVEL`；
- 统一 uvicorn 的日志（`uvicorn.access` 收敛到自己的格式，避免双份输出）；
- ⛔ **绝对不要打印密码、手机号全文、JWT 密钥**。

### 4.4 `app/core/database.py`

- `create_engine(settings.database_url, pool_size=..., max_overflow=..., pool_recycle=..., pool_pre_ping=True, echo=settings.DB_ECHO, future=True)`
- `SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)`
- `class Base(DeclarativeBase): pass`（阶段 1 的模型继承它，本阶段不定义任何模型）
- `def get_db() -> Generator[Session, None, None]`：`try/yield/finally close`，**不要在依赖里 commit**（事务边界归 service 层）
- `def check_db_connection() -> tuple[bool, int]`：执行 `SELECT 1`，返回 `(是否连通, 耗时毫秒)`，**任何异常都要被捕获**（供健康检查用，不允许抛出）

### 4.5 `app/core/error_codes.py`

用 `IntEnum` 定义**下表全部错误码**（与 `docs/06` 第 1.4 节完全一致，数值与文案一字不差）：

| 码 | 含义 |
| --- | --- |
| `SUCCESS = 0` | 成功 |
| `CAPTCHA_INVALID = 1001` | 验证码错误或已失效，请重新输入 |
| `LOGIN_FAILED = 1002` | 用户名或密码错误 |
| `ACCOUNT_LOCKED = 1003` | 账号已被锁定，请稍后再试 |
| `ACCOUNT_DISABLED = 1004` | 账号已停用，请联系管理员 |
| `NO_PERMISSION = 1040` | 没有该操作的权限 |
| `REQUIRED_MISSING = 2001` | 必填项缺失 |
| `PRODUCT_DUPLICATE = 2002` | 商品编码或条码已存在 |
| `PRICE_BELOW_COST = 2003` | 售价低于进价，请确认后重试 |
| `SUPPLIER_DISABLED = 3001` | 供应商已停用 |
| `ORDER_STATUS_INVALID = 4001` | 当前单据状态不允许收货 |
| `RECEIVE_QTY_EXCEED = 4002` | 收货数量超出订单剩余数量 |
| `CHECK_DIFF_REASON_MISSING = 5001` | 盘点差异未填写原因 |
| `DOC_ALREADY_AUDITED = 5002` | 单据已审核，无法重复操作 |
| `BARCODE_NOT_FOUND = 6001` | 未找到该条码对应的商品 |
| `PRODUCT_DISABLED = 6002` | 该商品已停用 |
| `STOCK_NOT_ENOUGH = 6003` | 库存不足 |
| `PAY_AMOUNT_MISMATCH = 7001` | 支付金额与应收金额不一致 |
| `MEMBER_BALANCE_NOT_ENOUGH = 7002` | 会员储值余额不足 |
| `MEMBER_POINT_NOT_ENOUGH = 7003` | 会员积分不足 |
| `STOCK_NOT_ENOUGH_SALE = 7004` | 库存不足，无法完成销售 |
| `RETURN_EXPIRED = 8001` | 超出退货期限 |
| `RETURN_QTY_EXCEED = 8002` | 退货数量超出可退数量 |
| `TRADE_NOT_FOUND = 8003` | 原销售单不存在 |
| `PHONE_ALREADY_MEMBER = 9001` | 该手机号已是会员 |
| `AMOUNT_INVALID = 9002` | 金额不合法 |
| `GIFT_RATIO_EXCEED = 9003` | 赠送比例超出限制 |
| `BI_DATA_NOT_ENOUGH = 10001` | 交易数据不足，无法进行关联分析 |
| `BI_PARAM_INVALID = 10002` | 挖掘参数不合法 |

同时提供「错误码 → 中文文案」的映射（用于兜底），文案与上表一致。

### 4.6 `app/core/exceptions.py`

- `class BusinessError(Exception)`：持有 `code: ErrorCode`、`message: str | None`、可选 `data`（默认 `None`）。message 为空时自动取错误码对应文案。
- 注册**全局异常处理器**（在 `main.py` 中挂载，或提供 `register_exception_handlers(app)`）：
  1. `BusinessError` → **HTTP 200** + `{code: 非0, message, data: null}`
  2. `RequestValidationError`（Pydantic 校验失败）→ **HTTP 200** + `code = 2001`（必填项缺失/参数不合法），`message` 里带上首个出错字段与原因，便于前端定位
  3. `StarletteHTTPException` → 保留原状态码（401/403/404/405 等），响应体仍尽量用统一结构（含 code/message/request_id）
  4. 兜底 `Exception` → **HTTP 500** + `code = 500`、`message = "服务器异常，请稍后重试"`，同时用 `logger.exception` 记完整堆栈。**⛔ 绝不要把原始异常堆栈返回给前端。**

### 4.7 `app/core/response.py` + `app/schemas/common.py`

统一响应体字段**固定为这 5 个，一个不多一个不少**：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "request_id": "9f2c1e4a-3b5d-4a7e-8c1f-2d6b0a9e7f31",
  "timestamp": "2026-09-15 18:30:00"
}
```

- `ApiResult` 用 Pydantic 泛型模型（`Generic[T]`）；`request_id` 从 `context` 取，`timestamp` 按 `YYYY-MM-DD HH:mm:ss` 格式（`Asia/Shanghai`）；
- 提供便捷构造：`success(data)`、`fail(code, message=None, data=None)`；
- `PageResult[T]`：`list` / `total` / `page` / `page_size`，其中 **`total` 必须是满足条件的总记录数，不是当前页条数**；
- `PageQuery`：`page`（默认 1，`ge=1`）、`page_size`（默认 20，`ge=1, le=100`）、`keyword`、`start_date`、`end_date`、`store_id`，并提供 `offset` 属性。

### 4.8 `app/schemas/base.py`（**本阶段最容易被写错的地方，请严格照做**）

```python
from typing import Annotated
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, PlainSerializer

class BaseSchema(BaseModel):
    """所有响应模型的基类。"""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# 金额：内部 Decimal，输出 JSON number（两位小数）
Money = Annotated[Decimal, PlainSerializer(lambda v: float(round(v, 2)), return_type=float)]

# 数量：内部 Decimal，输出 JSON number（三位小数，支持称重商品）
Qty = Annotated[Decimal, PlainSerializer(lambda v: float(round(v, 3)), return_type=float)]

# 时间：输出 YYYY-MM-DD HH:mm:ss
DateTimeStr = Annotated[datetime, PlainSerializer(lambda v: v.strftime("%Y-%m-%d %H:%M:%S"), return_type=str)]
DateStr = Annotated[datetime, PlainSerializer(lambda v: v.strftime("%Y-%m-%d"), return_type=str)]

# tinyint：接口约定是 number，不是 bool
Flag = int
```

要求：
- 上面的**语义与行为必须实现**（写法可微调，但 `Money/Qty` 输出必须是 JSON number，`DateTimeStr` 必须是 `YYYY-MM-DD HH:mm:ss`）；
- 在文件顶部注明：「阶段 1 起，所有响应模型的金额/数量/时间字段**必须**使用这些别名，禁止裸用 `Decimal` / `datetime`」；
- 在 `tests/test_core/test_response.py` 里用一个最小模型断言：`Decimal("12.30")` 序列化后是 `12.3`（number，不是 `"12.30"` 字符串），`datetime` 序列化后形如 `2026-09-15 18:30:00`。

### 4.9 `app/middleware/request_id.py`

- 每个请求生成一个 `uuid4` 作为 request_id（若请求头带 `X-Request-Id` 则沿用，便于前后端联调追踪）；
- 写入 `context`（供响应体与日志使用）；
- 在响应头回写 `X-Request-Id`；
- 用 `try/finally` 保证上下文清理，避免请求间串号。

### 4.10 `app/api/v1/health.py` — `GET /api/v1/health`

**必须**满足：**数据库连不上时，服务仍要能正常启动，此接口也仍要返回 200**，只是 `data.db_status` 为 `disconnected`。

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "python_version": "3.13.12",
    "db_status": "connected",
    "db_latency_ms": 3,
    "uptime_seconds": 128,
    "start_time": "2026-09-15 19:40:07",
    "server_time": "2026-09-15 19:42:15"
  },
  "request_id": "...",
  "timestamp": "2026-09-15 19:42:15"
}
```

- `uptime_seconds` / `start_time` 由 `app.main` 的 `lifespan` 启动时记录（阶段 10 的 `GET /system/info` 要复用同一套计算，请把逻辑抽成可复用函数，不要写死在路由里）；
- 该接口**不加权限校验**（健康检查要能被监控直接调）。

另外，在 `DEBUG=true` 时额外注册一个**仅供调试**的路由 `GET /api/v1/_dev/raise-business-error`，直接 `raise BusinessError(ErrorCode.REQUIRED_MISSING)`；`DEBUG=false` 时该路由**必须不存在**（返回 404）。用途：手工验证「业务错误走 HTTP 200 + 非 0 code」这条通道。

### 4.11 `app/core/security.py`

- `hash_password(plain: str) -> str`：`bcrypt.hashpw` + `bcrypt.gensalt(rounds=10)`（**cost=10 必须与 `db/02_init_data.sql` 里已有的密码哈希一致**，那批哈希可直接被 `checkpw` 校验）；
- `verify_password(plain: str, hashed: str) -> bool`：`bcrypt.checkpw`，用 `try/except` 包裹非法哈希（返回 `False`，不要抛异常）；
- `validate_password_strength(password: str) -> None`：长度 ≥ 6、不含空白字符，不满足抛 `BusinessError(ErrorCode.AMOUNT_INVALID)` 或自定义参数错误码（在代码注释里说明选择理由）；
- ⛔ 不要写 JWT 相关函数（阶段 2）。

### 4.12 `app/core/permissions.py`

定义 29 个权限码常量（与 `docs/06` 第 1.8 节、与数据库 `sys_permission.perm_code` **一字不差**）：

`dashboard:view`、`pos:use`、`pos:session`、`member:list`、`member:detail`、`member:level`、`promo:list`、`promo:edit`、`promo:coupon`、`goods:product:list`、`goods:product:edit`、`goods:category`、`pur:order:list`、`pur:order:create`、`pur:receipt`、`pur:supplier`、`inv:stock:list`、`inv:flow`、`inv:check`、`inv:loss`、`inv:transfer`、`report:sales`、`report:goods`、`report:inv`、`bi:analysis`、`bi:compare`、`sys:user`、`sys:role`、`sys:config`

组织成带中文注释的常量类（如 `class Perm:`）即可。**本阶段只定义常量，不要写 `require_perm` 依赖**（它依赖 JWT，属阶段 2）。

### 4.13 `app/deps.py`

- `get_db` 依赖（转发 `app.core.database.get_db`）；
- `class PageParams`：从 Query 解析分页参数（默认值同 `PageQuery`），提供 `offset`。
  阶段 1/2 会在此基础上加 `current_user` 等依赖，本阶段不要写。

### 4.14 `app/main.py`

- `FastAPI(title="中小型社区超市销售管理系统 · 后端接口", version=settings.APP_VERSION, description=...)`，description 里写清「阶段 0：基础设施」与 `/docs` 用途；
- 配置 `tags_metadata`，按 10 个模块分组（认证与权限 / 商品管理 / 库存管理 / 采购与供应商 / 销售与收银 / 会员管理 / 促销管理 / 统计报表 / 智能数据分析 / 系统管理），保证 `/docs` 后续不会乱成一团；
- `lifespan` 上下文：启动时记录 `start_time`、初始化日志、输出「服务已启动，监听 {host}:{port}」；关闭时 `engine.dispose()` 释放连接池；
- 挂载 `RequestIdMiddleware` + `CORSMiddleware`（origin 取 `settings.CORS_ORIGINS`，允许凭证、全部方法与头）；
- 注册全部异常处理器；
- 挂载 `/api/v1` 路由；
- 一个 `if __name__ == "__main__"` 的 `uvicorn.run` 入口（便于 `python -m app.main` 直接跑）。

### 4.15 工程配置类文件

- `pyproject.toml`：项目元信息、依赖、`requires-python = ">=3.11"`；
  - `[tool.ruff]`：`line-length = 100`、`target-version = "py311"`，启用 `E/F/I/UP/B/SIM` 等常用规则；
  - `[tool.pytest.ini_options]`：`testpaths = ["tests"]`；
- `.env.example`：**必须与 `config.py` 字段一一对应**，含中文注释与示例值（DB 部分用 `community_supermarket` / `utf8mb4`；`TZ=Asia/Shanghai`；`JWT_SECRET_KEY` 留占位符并注明「生产必须替换」）；
- `.gitignore`：`.venv/`、`__pycache__/`、`*.pyc`、`.env`、`logs/`、`.ruff_cache/`、`.pytest_cache/`、`.idea/`、`.vscode/`；
- `README.md`：项目简介、目录结构说明、**依赖安装与启动命令**（两种方式都写：`uv` 与 `python -m venv` + `pip`）、常用命令（起服务 / 跑测试 / lint）、以及「当前处于阶段 0，仅基础设施」的说明。

---

## 五、依赖

只装**骨架运行与测试必需**的，分析与 Excel 类依赖留到对应阶段再加（避免过早锁定版本）：

```
fastapi        uvicorn[standard]     pydantic        pydantic-settings
sqlalchemy     pymysql               bcrypt
ruff           pytest               httpx
```

- 版本以 `docs/07` 提及的下限为基线，装最新补丁版即可（FastAPI ≥ 0.115 / Pydantic ≥ 2.9 / SQLAlchemy ≥ 2.0 / pydantic-settings ≥ 2.5）；
- 包管理**优先 `uv`**；如果本机没装 `uv`，就回退到 `python -m venv .venv` + `pip install`，并在 `README.md` 里把两种方式都写清楚（**本机很可能没有 uv，请实际执行验证后再决定 README 怎么写**，不要凭空假设）。

---

## 六、编码规范（硬性要求）

1. **所有函数/方法必须有完整的类型注解**；
2. **所有公开函数必须有 docstring**，含 `Args` / `Returns` / `Raises` 三段（无参无返回也要说明用途）；
3. 注释解释**「为什么这样做」**，而不是复述代码在做什么；
4. 关键约束（例如「业务错误必须 HTTP 200」「Decimal 必须以 number 输出」）要在代码里留注释说明原因，避免后续阶段被"顺手改掉"；
5. 不允许出现裸 `print`（用 logger）；
6. 不允许吞异常（`except Exception: pass` 是禁止的）；
7. 中文注释、英文标识符。

---

## 七、明确不做的事（**越界即返工**）

1. ⛔ 不实现 128 个业务接口中的任何一个（**只有 health 与 DEBUG 调试路由**）；
2. ⛔ 不写 61 张表的 ORM 模型、不写任何 `repositories/` `services/` 实现；
3. ⛔ 不修改 `db/*.sql`、`docs/*`、`qianduan/*`、`.workbuddy/*`；
4. ⛔ 不写 JWT 签发/校验与 `require_perm`（阶段 2）；
5. ⛔ 不引入 Redis、Celery、Alembic、Docker（分别在方案里被排除或属阶段 1/11）；
6. ⛔ 不装 pandas / mlxtend / openpyxl / apscheduler；
7. ⛔ 不改动数据库（不建表、不灌数据、不执行任何 DDL/DML）；
8. ⛔ 不做多余抽象（不要为了"通用"造基类工厂、插件机制），阶段 0 只要求清晰可读、能被后续阶段直接扩展。

---

## 八、实施顺序与自检

建议按这个顺序推进，**每完成一组就跑一次 `ruff check .` 与 `pytest -q`**：

1. `config.py` → `context.py` → `logging.py`
2. `database.py`
3. `error_codes.py` → `exceptions.py` → `schemas/base.py` → `schemas/common.py` → `response.py`
4. `middleware/request_id.py`
5. `main.py` → `api/v1/router.py` → `api/v1/health.py` → `deps.py`
6. `security.py` → `permissions.py` → `utils/formatting.py`
7. `tests/` + 工程配置文件

---

## 九、验收标准（逐条可验证，请自己先跑一遍）

| # | 验收项 | 判定方式 |
| :--: | --- | --- |
| 1 | 依赖安装后服务能启动 | `uvicorn app.main:app --reload` 无 ERROR 日志 |
| 2 | 健康检查正常 | `GET /api/v1/health` 返回 200，`code=0`，`data.db_status` ∈ {connected, disconnected} |
| 3 | **数据库不可用时服务仍可启动** | 把 `.env` 的 `DB_HOST` 改成错误地址，服务仍能起、health 仍返回 200 且 `db_status=disconnected` |
| 4 | 接口文档是中文 | 浏览器打开 `/docs`：中文标题、接口有中文说明、health 有 example response、tags 按 10 个模块分组 |
| 5 | 404 语义正确 | `GET /api/v1/not-exist` 返回 **HTTP 404**，响应体含 `code`/`message`/`request_id` |
| 6 | **业务错误通道正确** | `DEBUG=true` 时 `GET /api/v1/_dev/raise-business-error` 返回 **HTTP 200** + `code=2001`；`DEBUG=false` 时该路径 404 |
| 7 | 请求追踪可用 | 任意响应体 `request_id` 与响应头 `X-Request-Id` 一致；日志里能按同一 `request_id` 检索到该请求 |
| 8 | 序列化正确 | `pytest` 中金额断言为 number（如 `12.3` 而非 `"12.30"`），时间断言为 `YYYY-MM-DD HH:mm:ss` |
| 9 | 测试全绿 | `pytest -q` 通过，用例数 ≥ 8（覆盖：成功响应体、业务错误、分页结构、request_id 一致性、未捕获异常 500 兜底、ValidationError→2001、bcrypt 哈希与校验、Decimal/时间序列化） |
| 10 | 代码质量 | `ruff check .` 零告警 |
| 11 | 配置与安全 | `.env.example` 与 `config.py` 字段一一对应；仓库内无任何硬编码密钥/密码；`.env` 已被 `.gitignore` 忽略 |

---

## 十、交付时请附上

1. **自检报告**：用表格列出「验收项 → 实际结果 → 验证命令」，要贴真实执行输出，不要只写"已完成"；
2. **目录树**：最终生成的完整目录树（含每个 `.py` 文件的行数）；
3. **启动命令**：实际可用的安装与启动命令（含你验证过的包管理方式）；
4. **遗留说明**：为后续阶段留的扩展点（例如 `deps.py` 待加 `current_user`、`permissions.py` 待接 `require_perm`）列成清单；
5. **踩坑记录**：过程中遇到的实际问题与解决办法。

======

---

## 附：使用这份提示词时的几个提醒

1. **分两次发效果更好**：先让它读文档并复述理解（见文件开头建议），确认无误后再发正式提示词。Qoder 一次读太多权威文档容易"读取即幻觉"。
2. **验收项 3 与 6 是这份提示词的防幻觉设计**：故意设了「数据库挂了也要能启动」和「业务错误必须 HTTP 200」两个容易被写错的点，用可执行的命令逼它验证，而不是嘴上说符合。
3. **阶段 0 只有 1 个健康检查接口，不要扩大范围**。如果 Qoder 顺手把商品或认证接口也写了，让它回退——阶段 1（61 表模型）与阶段 2（认证）有各自的前置依赖，提前写会返工。
4. **跑完阶段 0 后**，把 `deps.py`、`permissions.py`、`schemas/base.py` 里留下的扩展点记下来，作为阶段 1 提示词的输入。
