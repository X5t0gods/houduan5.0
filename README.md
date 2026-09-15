# 中小型社区超市销售管理系统（CSSMS）- 后端

基于 FastAPI + Pydantic v2 + SQLAlchemy 2.0 的后端服务。

> **当前处于阶段 0：工程骨架与基础设施**
> 仅包含健康检查接口，业务接口将在后续阶段逐步实现。

---

## 技术栈

| 组件 | 选型 | 说明 |
| --- | --- | --- |
| Web 框架 | FastAPI | ASGI 异步框架，路由函数用 `def`（同步） |
| 数据校验 | Pydantic v2 | 请求/响应模型，类型安全 |
| ORM | SQLAlchemy 2.0（同步） | 配合 PyMySQL 驱动 |
| 数据库 | MySQL 8 | 61 张表，86 条外键 |
| 密码哈希 | bcrypt | 不使用 passlib（已停止维护） |
| 代码质量 | ruff | lint + format |
| 测试 | pytest + httpx | 单元测试 + 集成测试 |

---

## 目录结构

```
houduan/
├── app/
│   ├── main.py                 # FastAPI 实例、生命周期、中间件、路由挂载
│   ├── deps.py                 # 通用依赖：get_db、PageParams
│   ├── core/                   # 横切基础设施
│   │   ├── config.py           # pydantic-settings 配置
│   │   ├── context.py          # request_id 上下文变量
│   │   ├── logging.py          # 日志配置 + request_id 过滤器
│   │   ├── database.py         # engine / SessionLocal / get_db
│   │   ├── error_codes.py      # 28 个业务错误码枚举
│   │   ├── exceptions.py       # BusinessError + 全局异常处理器
│   │   ├── response.py         # ApiResult 构造器（success/fail）
│   │   ├── security.py         # bcrypt 哈希与校验
│   │   └── permissions.py      # 29 个权限码常量
│   ├── middleware/
│   │   └── request_id.py       # RequestIdMiddleware
│   ├── api/v1/
│   │   ├── router.py           # /api/v1 路由汇总
│   │   └── health.py           # GET /api/v1/health
│   ├── schemas/
│   │   ├── base.py             # BaseSchema + Money/Qty/DateTimeStr 类型别名
│   │   └── common.py           # ApiResult / PageResult / PageQuery
│   ├── models/                 # ORM 模型（阶段 1）
│   ├── repositories/           # 数据访问层（阶段 1+）
│   ├── services/               # 业务逻辑层（阶段 2+）
│   ├── tasks/                  # 定时任务（阶段 8+）
│   └── utils/
│       └── formatting.py       # 金额/数量/时间格式化工具
├── tests/                      # 测试用例
├── db/                         # 数据库脚本（已存在，勿修改）
├── docs/                       # 项目文档（已存在，勿修改）
├── .env.example                # 环境变量模板
├── pyproject.toml              # 项目配置与依赖
└── README.md
```

---

## 快速开始

### 前置要求

- Python >= 3.11
- MySQL 8（可选，健康检查接口在数据库不可用时仍能正常返回）

### 方式一：使用 venv + pip（推荐，本机无 uv 时使用）

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows CMD:
.venv\Scripts\activate.bat
# Linux/Mac:
source .venv/bin/activate

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 4. 复制环境变量配置
copy .env.example .env    # Windows
# cp .env.example .env    # Linux/Mac

# 5. 编辑 .env 填写数据库连接信息

# 6. 启动服务
uvicorn app.main:app --reload
```

### 方式二：使用 uv（如果已安装）

```bash
# 1. 安装依赖
uv sync --dev

# 2. 复制环境变量配置
cp .env.example .env

# 3. 启动服务
uv run uvicorn app.main:app --reload
```

### 验证服务

服务启动后访问：
- Swagger 文档：http://127.0.0.1:8000/docs
- ReDoc 文档：http://127.0.0.1:8000/redoc
- 健康检查：http://127.0.0.1:8000/api/v1/health

---

## 常用命令

```bash
# 启动开发服务（热重载）
uvicorn app.main:app --reload

# 直接运行（等效于上面）
python -m app.main

# 运行测试
pytest -q

# 运行测试（带覆盖率）
pytest --cov=app --cov-report=term-missing

# 代码检查
ruff check .

# 代码格式化
ruff format .

# 检查并自动修复
ruff check . --fix
```

---

## 核心约定

### 统一响应体

所有接口（除文件流导出）返回固定 5 个字段：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "request_id": "uuid",
  "timestamp": "2026-09-15 18:30:00"
}
```

### 错误处理

- **业务失败**：HTTP 200 + 非 0 `code`（前端根据 code 显示提示）
- **HTTP 状态码**：只表达传输层/认证层（401 未登录、403 无权限、404 不存在、500 服务端异常）
- **登录失败**：必须返回 200 + 1002（不能返回 401，否则前端会清登录态）

### 序列化规范

| 类型 | 数据库 | Python | JSON 输出 |
| --- | --- | --- | --- |
| 金额 | DECIMAL(12,2) | Decimal | number（如 12.3） |
| 数量 | DECIMAL(12,3) | Decimal | number（如 1.5） |
| 时间 | DATETIME | datetime | "YYYY-MM-DD HH:mm:ss" |
| 标志位 | TINYINT | int | number（0/1） |

> ⛔ 禁止裸用 `Decimal` / `datetime`，必须使用 `schemas/base.py` 中的类型别名。

---

## 开发阶段

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 工程骨架与基础设施 | ✅ 当前 |
| 1 | 数据层（61 表 ORM 模型） | 待开始 |
| 2 | 认证与权限（6 接口） | 待开始 |
| 3 | 基础数据与商品（13 接口） | 待开始 |
| 4 | 收银主链路（14 接口） | 待开始 |
| 5 | 库存（14 接口） | 待开始 |
| 6-11 | 采购/会员/报表/BI/系统/测试 | 待开始 |

---

## 相关文档

- `docs/06_后端接口文档.md` - 128 个接口契约
- `docs/07_后端技术架构与开发计划.md` - 技术决策与开发计划
- `db/01_schema.sql` - 数据库结构（唯一真相源）
- `db/02_init_data.sql` - 初始化数据

---

## License

MIT
