# Qoder 提示词 · 阶段 1：数据层（62 表 ORM 模型 + 单号生成 + 迁移基线）

> **用法**：在 Qoder 里打开 `C:\Users\徐炜城\Desktop\工作空间\houduan`，切到 **Agent / Quest 模式**，把下面 `======` 之间的内容整段粘贴发送。
>
> **前置条件**：阶段 0 已完成并通过验收（`app/core/`、`app/schemas/`、`tests/` 已存在，`GET /api/v1/health` 可用）。
> **本机需已安装 MySQL 8** 并能连上（阶段 1 的模型比对脚本需要读 `information_schema`）。
>
> **强烈建议分两次发**：先发「只读 `db/01_schema.sql` 全文 + `docs/03_功能需求文档.md` 第 1522–1542 行，读完回答：一共有多少张表、多少条外键、哪些字段类型、单号有哪几种格式？先不要改任何文件」。等它答对（62 张表见下方说明、86 条外键、9 种字段类型、4 种编号格式）再发正式提示词。这一步能过滤掉绝大多数"没读文档就开写"的情况。

---

======

## 你的角色与本次任务

你是一名资深 Python 后端工程师，长期使用 FastAPI + SQLAlchemy 2.0 做工程化开发。你的准则是：**结构以既有 DDL 为唯一真相源，绝不让 ORM 反过来定义结构；每一层都要能被脚本验证，而不是靠肉眼确认。**

工作区 `houduan` 是「中小型社区超市销售管理系统」的后端工程。**阶段 0（工程骨架）已完成**：配置、日志、统一响应体、异常处理器、`Base`/`engine`/`SessionLocal`、健康检查都就绪。

**本次做「阶段 1：数据层」**，共 4 件事：

1. **62 张表的 SQLAlchemy 2.0 声明式模型**（按 8 个数据域拆分）；
2. **并发安全的单号生成器**（16 个编号规则），配套一次建表脚本变更；
3. **结构一致性校验脚本**（模型 ↔ `information_schema` 双向比对，零差异）；
4. **Alembic 迁移基线** 与 **造数脚本骨架**。

⚠️ 最重要的一条原则：**`db/01_schema.sql` 是结构的唯一真相源。**
⛔ 不要用 `Base.metadata.create_all()`、不要用 `alembic revision --autogenerate` 生成建表脚本、不要"顺手修正"建表脚本里你认为不合理的字段。

---

## 一、开工前必读

| 顺序 | 文件 | 你要确认的内容 |
| :--: | --- | --- |
| 1 | `db/01_schema.sql` **全文**（约 1300 行） | 62 张表（含新增 `sys_no_seq`）的**全部字段名、类型、可空、默认值**；文件开头 26 行的「设计约定」；**文件末尾 86 条 `ALTER TABLE ... FOREIGN KEY`**（这是外键的唯一依据） |
| 2 | `db/02_init_data.sql` | 演示数据规模与 ID 规则（**只读，不要改**） |
| 3 | `docs/03_功能需求文档.md` 第 **1522–1542** 行 | 「8.2 单据编号规则汇总」表 + 编号生成约束（禁止 `SELECT MAX(...)+1`） |
| 4 | `docs/07_后端技术架构与开发计划.md` 第 2.3 节决策 6、决策 12 | 单号生成方案、Alembic 基线的正确做法 |
| 5 | 阶段 0 产出的 `app/core/database.py`、`app/models/__init__.py` | 复用 `Base`，不要另建 declarative base |

### 1.1 建表脚本的实测事实（已核对，作为你的比对基准）

| 项 | 事实 |
| --- | --- |
| 现有表 | **61 张**（8 个数据域），本次新增 1 张 → **62 张** |
| 外键 | **86 条**，全部 `ON DELETE RESTRICT`（在脚本末尾用 `ALTER TABLE` 集中创建） |
| 唯一键 | **41 个** `UNIQUE KEY` |
| 字段类型（**全库只有这 9 种**） | `BIGINT`(181)、`VARCHAR`(171)、`DECIMAL`(125)、`DATETIME`(116)、`TINYINT`(41)、`INT`(33)、`DATE`(13)、`JSON`(6)、`TIME`(2) |
| DECIMAL 精度 | `(12,2)`×73、`(12,3)`×24、`(12,4)`×10、`(14,2)`×6、`(5,4)`×3、`(10,4)`×3、`(8,5)`×2、`(8,4)`×2 |
| 主键 | 除本次新增的 `sys_no_seq` 外，一律 `BIGINT UNSIGNED AUTO_INCREMENT`，字段名 `id` |
| 表前缀 | `base_` `sys_` `prd_` `pur_` `inv_` `mem_` `pro_` `sal_` `bi_` |

> ⚠️ **`DECIMAL` 不止是钱**：`(5,4)` / `(8,4)` / `(8,5)` / `(10,4)` / `(12,4)` 这些是**比率/系数**（折扣率、提升度 lift、置信度、换算系数），精度必须保留到 4–5 位小数。**不要把它们当金额处理。**

---

## 二、已定技术决策（不得更改）

1. 同步 SQLAlchemy 2.0 + PyMySQL（阶段 0 已定），**不要引入 async**。
2. ⛔ **不写 `relationship()`**。理由：本项目 61 张表关系密集，隐式懒加载会产生不可控的 N+1，且容易在 service 层悄悄触发查询。跨表取数一律在 **repository 层用显式 `select().join()`** 完成。
   - 若你判断某处确实需要关系对象，必须写成 `relationship(..., lazy="raise")` 并加注释说明，否则不要写。
3. 模型只做**映射**：不写业务方法、不写校验逻辑、不写 `@property` 计算字段。
4. `created_at` / `updated_at` 由**数据库**负责默认值与自动更新（脚本里是 `DEFAULT CURRENT_TIMESTAMP` / `ON UPDATE CURRENT_TIMESTAMP`）。模型里用 `server_default=text("CURRENT_TIMESTAMP")` 声明，**不要用 Python 侧 `default=datetime.now`**（否则会覆盖数据库行为、且时区口径混乱）。
5. 时区统一 `Asia/Shanghai`（阶段 0 已定）。

---

## 三、交付物清单

```
houduan/
├── app/
│   ├── models/                       # ← 本次主体
│   │   ├── __init__.py               # 汇总导入全部模型（保证 Base.metadata 完整）
│   │   ├── base_models.py            # base_ 域 4 张
│   │   ├── sys_models.py             # sys_ 域 12 张（含新增 sys_no_seq）
│   │   ├── prd_models.py             # prd_ 域 7 张
│   │   ├── pur_models.py             # pur_ 域 9 张
│   │   ├── inv_models.py             # inv_ 域 9 张
│   │   ├── mem_models.py             # mem_ 域 6 张
│   │   ├── pro_models.py             # pro_ 域 4 张
│   │   ├── sal_models.py             # sal_ 域 7 张
│   │   ├── bi_models.py              # bi_ 域 4 张
│   │   └── enums.py                  # 字符串状态常量（status / flow_type / data_scope 等）
│   ├── core/
│   │   └── sequence.py               # ← 单号生成器（新增）
│   └── schemas/
│       └── base.py                   # ↑ 追加 Rate 类型别名（见 4.3）
├── scripts/
│   ├── check_models.py               # ← 模型 ↔ 数据库 双向比对（新增）
│   └── seed.py                       # ← 造数脚本骨架（新增）
├── alembic/
│   ├── env.py                        # 改造：读取 settings + target_metadata
│   └── versions/0001_baseline.py     # 空基线版本（不执行任何 DDL）
├── alembic.ini
├── tests/
│   ├── test_models/test_metadata.py  # 模型自身的一致性断言
│   └── test_core/test_sequence.py    # 单号生成器（含并发与跨日）
└── db/
    ├── 01_schema.sql                 # ← 本次修改（新增 sys_no_seq + mem_balance_flow 补列）
    └── README.md                     # ← 本次同步更新（表数量、缺口清单）
```

---

## 四、逐个交付物的要求

### 4.1 `db/01_schema.sql` 的两处变更（**精确照抄，不要自由发挥**）

**(1) 新增 `sys_no_seq` 表** —— 放在 `sys_` 域（`sys_backup` 之后），并**同步加入第 3.2 节的表清单**（脚本内如有注释清单）：

```sql
-- ----------------------------------------------------------------------------
--  单号流水序列
--  用途：为 16 类单据/编码提供并发安全的流水号（见需求文档 8.2 节）
--  为什么单独建表：全库原无处存放流水计数，而 8.2 节明确禁止 SELECT MAX(...)+1
--  为什么用复合主键而不加 AUTO_INCREMENT：
--      MySQL 的 LAST_INSERT_ID(expr) 惯用法在「首次插入」路径上会返回自增 ID，
--      导致新键第一次取号得到错误的巨大值；去掉自增列才能让两条路径返回同一语义。
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sys_no_seq`;
CREATE TABLE `sys_no_seq` (
  `seq_key`     VARCHAR(32)  NOT NULL                COMMENT '业务键 XS/CG/SH/CT/FK/PD/BS/DB/JB/CZ/GD/P/S/M/BATCH',
  `seq_date`    DATE         NOT NULL                COMMENT '日期分区；不按日重置的键（P/S）固定 1970-01-01',
  `current_val` INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '当前已分配的最大流水号',
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`seq_key`, `seq_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='单号流水序列（并发安全，禁止 SELECT MAX+1）';
```

**(2) `mem_balance_flow` 补幂等键** —— 在 `remark` 字段前后插入一列，并在 `KEY` 段追加唯一索引：

```sql
  `request_id`     VARCHAR(64)     DEFAULT NULL            COMMENT '幂等键（前端生成，防重复充值）',
```

```sql
  UNIQUE KEY `uk_request_id` (`request_id`),
```

> 为什么可空：只有充值/消费等**资金入口**会带幂等键，调整类流水没有。MySQL 的 `UNIQUE` 允许多个 `NULL`，因此可空 + 唯一索引正好满足"有值必唯一、无值不冲突"。

**(3) 变更后必须自查**：
- 表数量 61 → **62**，外键仍是 **86 条**（本次变更不新增外键），唯一键 41 → **42**；
- 脚本**必须保持可重复执行**（原有风格：`SET FOREIGN_KEY_CHECKS = 0` … 末尾恢复为 1，且不要在 `DROP TABLE` 段引入顺序依赖）。

### 4.2 `app/models/` — 62 张表的模型（按域拆分）

**硬性要求**：

1. 每张表一个类，类名用 PascalCase（如 `SalTrade`、`InvStockFlow`、`PrdProduct`），`__tablename__` 与脚本**一字不差**；
2. 每个字段用 `mapped_column` 声明，`Mapped[...]` 类型与脚本对齐；字段名与脚本**一字不差**（脚本里全是 snake_case）；
3. **`nullable` 必须与脚本一致**（`NOT NULL` → `nullable=False`）；有 `DEFAULT` 的字段用 `server_default=text("...")` 声明（`0`、`1`、`0.00`、`'SELF'` 这类都要写）;
4. **外键**：严格按脚本末尾 86 条 `ALTER TABLE` 逐条对应声明 `ForeignKey("目标表.字段", ondelete="RESTRICT")`，**一条不多、一条不少**。
   ⚠️ 注意 `base_store.manager_id` 在脚本里注释明确「不设外键，避免与 sys_user 循环依赖」→ **这张表的这个字段千万不要声明 `ForeignKey`**。凡脚本没有的外键，一律不得自行添加；
5. **唯一约束**：脚本里 42 个 `UNIQUE KEY`（含新增的 `mem_balance_flow.uk_request_id`）全部用 `__table_args__ = (UniqueConstraint(...),)` 声明。业务代码要靠它们兜住 `IntegrityError`（幂等、重号、唯一账号），**漏声明会导致后续阶段难以排查**；
   - 普通 `KEY`（非唯一索引）**不必**声明（属性能优化，不影响正确性）；
   - 复合唯一键的字段顺序必须与脚本一致。
6. **类型映射表**（全库只有这 9 种类型，逐条照此映射）：

| MySQL | SQLAlchemy | 说明 |
| --- | --- | --- |
| `BIGINT [UNSIGNED]` | `BigInteger` | 主键与外键 |
| `INT [UNSIGNED]` | `Integer` | 排序、计数、耗时 |
| `TINYINT` | `SmallInteger` | **状态标志，不是 bool**（接口里是 number 0/1） |
| `DECIMAL(p,s)` | `Numeric(p,s)` | **精度原样保留**，不要统一成 `(12,2)` |
| `VARCHAR(n)` | `String(n)` | 长度必须写，便于比对 |
| `DATETIME` | `DateTime` | |
| `DATE` | `Date` | |
| `TIME` | `Time` | |
| `JSON` | `JSON` | `bi_association_rule` 的 antecedent/consequent 等 |

7. **`app/models/enums.py`**：把脚本注释里出现的**枚举语义**收成常量类（**不要用 `sqlalchemy.Enum`，因为数据库里存的是 VARCHAR**），至少覆盖：
   - 单据状态：`sal_trade.status`（`FINISHED`/`RETURNED`/`VOID`）、`pur_order.status`、`inv_check.status`、`pro_promotion.status`（`RUNNING` 等）
   - 流水类型：`inv_stock_flow.flow_type`（含 `SALE_OUT`/`PURCHASE_IN`/`CHECK_ADJUST` 等全部取值）、`mem_balance_flow.flow_type`、`mem_point_flow`
   - `sys_role.data_scope`：`ALL`/`STORE`/`DEPT`/`SELF`
   - `sys_permission.perm_type`：1 目录 / 2 菜单 / 3 按钮
   - 取值来源：**以 `01_schema.sql` 的字段 `COMMENT` 为准**（注释里写了全部合法值），不要自己发明。
8. `app/models/__init__.py`：导入全部模型类并导出 `__all__`，**保证 `Base.metadata` 里有完整 62 张表**（这是 `check_models.py` 能工作的前提）。

### 4.3 `app/schemas/base.py` 追加一个类型别名

阶段 0 已定义 `Money`（2 位）、`Qty`（3 位）、`DateTimeStr`、`DateStr`。本次**追加**：

```python
# 比率/系数：折扣率、提升度、置信度、换算系数等（DECIMAL(5,4)/(8,4)/(8,5)/(10,4)/(12,4)）
# 为什么单独定义：这些字段精度到 4~5 位小数，若误用 Money（2 位）会把 lift=1.2345 截断成 1.23，
# 直接影响智能分析模块的指标正确性与论文实验数据。
Rate = Annotated[Decimal, PlainSerializer(lambda v: float(round(v, 6)), return_type=float)]
```

并在文件顶部注释的「必须使用别名」清单里加上 `Rate`。

### 4.4 `app/core/sequence.py` — 单号生成器（**本次最需要动脑的部分**）

#### 4.4.1 编号规则（权威，来自 `docs/03` 8.2 节 + 建表脚本字段注释）

| key | 用途 | 格式 | 示例 | 流水宽度 | 日期作用域 | 落库字段 |
| --- | --- | --- | --- | :--: | --- | --- |
| `P` | 商品编码 | `P` + 6 位 | `P000123` | 6 | **全局** | `prd_product.product_code` |
| `S` | 供应商编码 | `S` + 6 位 | `S000012` | 6 | **全局** | `pur_supplier.supplier_code` |
| `M` | 会员卡号 | `M` + `yyyyMMdd` + 4 位 | `M202609150001` | 4 | 按日 | `mem_member.member_no` |
| `BATCH` | 批次号 | `P` + 商品编码 + `-` + `yyyyMMdd` + `-` + 2 位 | `P000123-20260915-01` | 2 | 按日 **+按商品** | `inv_batch.batch_no` |
| `CG` | 采购订单 | `CG` + `yyyyMMdd` + 4 位 | `CG202609150003` | 4 | 按日 | `pur_order.order_no` |
| `SH` | 采购收货单 | `SH` + `yyyyMMdd` + 4 位 | `SH202609150003` | 4 | 按日 | `pur_receipt.receipt_no` |
| `CT` | 采购退货单 | `CT` + `yyyyMMdd` + 4 位 | `CT202609150001` | 4 | 按日 | `pur_return.return_no` |
| `FK` | 采购付款单 | `FK` + `yyyyMMdd` + 4 位 | `FK202609150001` | 4 | 按日 | `pur_payment.pay_no` |
| `XS` | 销售单 | `XS` + `yyyyMMdd` + 4 位 | `XS202609150012` | 4 | 按日 | `sal_trade.trade_no` |
| `TH` | 销售退货单 | `TH` + `yyyyMMdd` + 4 位 | `TH202609150002` | 4 | 按日 | `sal_return.return_no` |
| `PD` | 盘点单 | `PD` + `yyyyMMdd` + 4 位 | `PD202609150001` | 4 | 按日 | `inv_check.check_no` |
| `BS` | 报损单 | `BS` + `yyyyMMdd` + 4 位 | `BS202609150001` | 4 | 按日 | `inv_loss.loss_no` |
| `DB` | 调拨单 | `DB` + `yyyyMMdd` + 4 位 | `DB202609150001` | 4 | 按日 | `inv_transfer.transfer_no` |
| `JB` | 交班单 | `JB` + `yyyyMMdd` + 4 位 | `JB202609150001` | 4 | 按日 | `sal_session.session_no` |
| `CZ` | 充值单 | `CZ` + `yyyyMMdd` + 4 位 | `CZ202609150005` | 4 | 按日 | `mem_balance_flow.source_no` |
| `GD` | 挂单号 | `GD` + `yyyyMMdd` + 4 位 | `GD202609150001` | 4 | 按日 | `sal_hold.hold_no` |

> 两处需要你在代码注释里标出（这是文档与建表脚本的差异，**不要静默处理**）：
> 1. `FK`（付款单号）的规则只存在于建表脚本的字段注释里，**`docs/03` 8.2 节的规则表漏了它**——以脚本注释为准，并在注释里注明此差异；
> 2. `GD`（挂单号）**文档与脚本都没有规定格式**，请你按上表实现（与其余单据保持一致的前缀+日期+4 位流水），并在代码注释与交付说明里显式标注"此为本次补充定义，待确认"。

#### 4.4.2 取号算法（**严格照做，两条容易写错的地方已标出**）

```sql
-- 一条语句完成"存在则自增、不存在则置 1"，并让会话变量持有结果
INSERT INTO sys_no_seq (seq_key, seq_date, current_val)
VALUES (:seq_key, :seq_date, LAST_INSERT_ID(1))
ON DUPLICATE KEY UPDATE current_val = LAST_INSERT_ID(current_val + 1);

SELECT LAST_INSERT_ID();
```

⛔ **坑 1**：`sys_no_seq` 若带 `AUTO_INCREMENT`，则"首次插入"路径上 `LAST_INSERT_ID()` 返回的是**自增主键 ID**（一个很大的数），而不是 1，导致新键第一次取号就得到错误值。这就是 4.1 节去掉自增列、改用复合主键的原因——**不要在表上加自增列**。

⛔ **坑 2**：`LAST_INSERT_ID()` 是**连接（会话）级**的值。上述两条语句**必须在同一个 `Session`（即同一连接）上连续执行**，中间不能换连接、不能插其他查询。若用 `engine.connect()` 手动管理，务必复用同一个 connection 对象。

其余要求：
- 对外暴露一个服务类（如 `NoSeqService`），方法签名类似：
  `def next_no(self, key: str, *, scope: str | None = None, at: date | None = None) -> str`
  —— `scope` 用于 `BATCH` 的商品编码；`at` 允许注入日期，**便于测试跨日行为**（不要用 `datetime.now()` 写死）；
- `seq_date` 的取值规则：**按日作用域**的键用 `at` 当天日期；**全局作用域**的键（`P`/`S`）固定用 `1970-01-01`；`BATCH` 用当天日期 + `seq_key = "BATCH:{product_code}"`（或等价的隔离方式，保证不同商品各自计数）；
- 流水号补零到规定的宽度（`zfill`），**超出宽度时不要静默截断**——必须抛 `BusinessError` 并给出明确信息（宽度 4 位即单日上限 9999 单，门店场景足够，但要显式失败而不是产生重号）；
- **事务归属**：默认与调用方业务写入**同一事务**（理由：回滚时号段一起回滚，不产生空洞；代价是长事务期间持有该键的行锁，对门店级并发完全可接受）。请在 docstring 里写清这个权衡；
- 支持"外部已提供编码则跳过生成"的模式：`P` / `S` 两个键的编码有可能由前端录入（阶段 3 联调时确认），因此生成器要能作为"未提供时的兜底"，**不要在模型层强制生成**。

### 4.5 `scripts/check_models.py` — 结构一致性校验（**先写它，再写模型**）

这是本阶段最有价值的交付物：把"模型和数据库是否一致"从主观承诺变成可执行结论。

**实现要求**：

1. 连真实数据库（复用 `settings` + `engine`），读 `information_schema.tables` / `.columns` / `.statistics` / `.key_column_usage`；
2. 与 `Base.metadata` 做**双向**比对，输出四类结果：
   - ❌ **ERROR**：表缺失/多余、字段缺失/多余、字段类型不兼容、主键不一致、可空性不一致、**模型声明的唯一键与数据库不一致**、**外键数量或指向不一致**；
   - ⚠️ **WARNING**：普通索引（非唯一）差异、注释差异；
3. 类型比对必须走**显式映射白名单**（即 4.2 节那张表），并在脚本内以常量形式写出，例如：
   - `BIGINT` ↔ `BigInteger`
   - `INT` ↔ `Integer`
   - `TINYINT` ↔ `SmallInteger`（也接受 `Integer`）
   - `DECIMAL(p,s)` ↔ `Numeric(p,s)`（**p、s 必须相等**，不接受"近似"）
   - `VARCHAR(n)` ↔ `String(n)`
   - `DATETIME`/`DATE`/`TIME` ↔ `DateTime`/`Date`/`Time`
   - `JSON` ↔ `JSON`
   出现白名单之外的类型时**报 ERROR 并要求人工确认**，不要自动放过；
4. 退出码语义：有 ERROR → `exit(1)`（便于接 CI）；只有 WARNING → `exit(0)`；
5. 输出一份**摘要表**（`表名 / 字段数 / 差异项`）与总计行，例如：
   `比对完成：表 62/62 一致，字段 912/912 一致，外键 86/86 一致，唯一键 42/42 一致，ERROR 0，WARNING 3`；
6. **友好的前置检查**：如果数据库或表不存在，给出明确提示（"未找到数据库 `community_supermarket`，请先执行 `db/01_schema.sql` 与 `db/02_init_data.sql`"），不要抛裸异常堆栈；
7. 命令形式：`python -m scripts.check_models`（或 `python scripts/check_models.py`），支持 `--verbose` 打印逐表明细。

> 实施顺序建议：**先写这个脚本**（此时模型的比对结果必然全是 ERROR），然后按域逐个补模型，每补完一个域就跑一次，直到 ERROR 归零。这样能保证 62 张表一张不漏、一列不错。

### 4.6 `alembic/` — 迁移基线（**只打基线，不生成建表脚本**）

正确做法（**不要用 autogenerate 反向生成 61 张表的建表语句**，那会与 `01_schema.sql` 在索引名、注释、外键顺序上全面冲突）：

1. `alembic init alembic`；
2. 改造 `alembic/env.py`：从 `app.core.config.settings` 读 `database_url`（**同步驱动 PyMySQL**，不要 async），`target_metadata = Base.metadata`，并 `import app.models` 触发全部模型注册；
3. **手动创建一个空基线版本**（`upgrade()` / `downgrade()` 都是 `pass`），并在版本文件头部注释里写清："结构由 `db/01_schema.sql` 维护，本基线仅用于标记当前状态，后续增量变更才使用迁移"；
4. 建库（跑 `01_schema.sql` + `02_init_data.sql`）之后执行 `alembic stamp head`，把数据库标记到基线；
5. `alembic.ini` 里不要写死数据库 URL（交由 `env.py` 从 `settings` 读取），**不要提交任何真实密码**。

验证方式：`alembic current` 显示 `head`；`alembic upgrade head` 是空操作（不产生任何 DDL）。

### 4.7 `scripts/seed.py` — 造数脚本**骨架**

**本阶段只做骨架，不做大规模造数**（真正的 BI 量级数据属阶段 11）。

要求：
- 命令行参数：`--products`、`--members`、`--trades`、`--days`、`--seed`（随机种子，**必须可复现**）、`--dry-run`、`--yes`（跳过确认）；
- 结构：连接 → 参数校验 → 打印将要写入的规模 → 确认 → 分批插入（`bulk_insert_mappings` 或 `session.add_all` 分批 flush）→ 汇总统计；
- **默认 `--dry-run`**：不写库，只打印计划；
- 有 `--yes` 之外的交互式确认时，必须能在非交互环境跳过（避免卡住 CI）；
- 复用 `app.core.database` 的 Session，复用 `app.core.sequence` 生成单号；
- 幂等性说明写在 docstring：重复执行是否会重复写入（建议在写入前检查是否存在演示数据并提示）；
- 本阶段只需保证「能跑通、能打印计划、能写一批测试商品」，**不要**现在就去构造购物篮共现模式（那是阶段 11 的 BI 实验数据）。

### 4.8 测试

| 文件 | 覆盖内容 |
| --- | --- |
| `tests/test_models/test_metadata.py` | `Base.metadata` 里有 **62** 张表；表名集合与 8 个域前缀划分正确；外键总数 **86**；典型唯一键存在（`sys_user.uk_username`、`sal_trade.uk_request_id`、`prd_barcode` 的条码唯一键、`mem_balance_flow.uk_request_id`）；`base_store.manager_id` **没有**外键 |
| `tests/test_core/test_sequence.py` | 各 key 前缀/宽度/格式正确（逐条对照 4.4.1 表）；**同一 key 连续取 20 次无重复且递增**；`P`/`S` 跨日不重置、其余 key 跨日从 1 重新开始（用 `at=` 注入日期）；`BATCH` 不同商品各自计数；**并发 100 次取号无重号**（`ThreadPoolExecutor`，需真实数据库）；宽度溢出时抛 `BusinessError` |
| `tests/test_core/test_response.py`（补充） | `Rate` 别名：`Decimal("1.234567")` 序列化后不被截断为 2 位 |

> 涉及真实数据库的用例，用 `pytest.mark.integration` 标记，并保证在无数据库时**跳过**而不是失败（`pytest.skip`）。

---

## 五、编码规范（沿用阶段 0，不放宽）

1. 所有函数有完整类型注解；公开函数有 `Args` / `Returns` / `Raises` docstring；
2. 注释解释**「为什么」**，尤其要把本提示词里标了 ⛔ 的地方（不自增列、同连接取号、不用 relationship、精度分档）写成代码注释，防止后续被"顺手优化"掉；
3. 不允许裸 `print`、不允许吞异常；
4. 中文注释、英文标识符；
5. `ruff check .` 必须零告警。

---

## 六、明确不做的事（越界即返工）

1. ⛔ 不实现任何业务接口（本阶段只有数据层与工具脚本）；
2. ⛔ 不写 `repositories/` / `services/` 的业务实现（阶段 2 起按模块写）；
3. ⛔ 不写 `relationship()`（见决策 2）；
4. ⛔ 不修改 `docs/01`、`docs/02`、`docs/03`、`docs/04`、`docs/06`、`docs/README.md`（论文文档由人统一改，见第八节）；
5. ⛔ 不修改 `qianduan/`、`.workbuddy/`；
6. ⛔ 不引入 Alembic 之外的迁移工具、不引入 Redis/Celery；
7. ⛔ 不装 pandas / mlxtend / openpyxl / apscheduler；
8. ⛔ 不用 `Base.metadata.create_all()` 建表；数据库结构一律由 `db/01_schema.sql` 创建；
9. ⛔ 不做多余抽象（不要为模型造通用 Mixin 工厂、不要写元类），清晰可读优先。

---

## 七、实施顺序

| 步 | 动作 | 完成判据 |
| :--: | --- | --- |
| 1 | 建库：跑 `db/01_schema.sql` + `db/02_init_data.sql`（`.env` 填好真实连接信息） | 能查出 61 张表 |
| 2 | 改 `db/01_schema.sql`（新增 `sys_no_seq` + `mem_balance_flow.request_id`） | 重跑建库后 62 张表、86 外键、42 唯一键 |
| 3 | 写 `scripts/check_models.py` | 运行后报出"模型 0 张表"，但脚本本身不崩 |
| 4 | 写 `app/models/`（**按域推进**：base_ → sys_ → prd_ → pur_ → inv_ → mem_ → pro_ → sal_ → bi_） | 每完成一个域重跑 check，该域 ERROR 归零 |
| 5 | 全部 9 个域完成 | `check_models.py` 输出 **ERROR 0** |
| 6 | `app/core/sequence.py` + `app/schemas/base.py` 追加 `Rate` | 单号测试全绿（含并发 100 次） |
| 7 | Alembic 基线（`stamp head`） | `alembic current` = head，`upgrade head` 空操作 |
| 8 | `scripts/seed.py` 骨架 + 测试 | `--dry-run` 可跑，`pytest -q` 全绿 |

---

## 八、验收标准（逐条可验证）

| # | 验收项 | 判定方式 |
| :--: | --- | --- |
| 1 | 建表脚本仍可重复执行 | 连续执行两次 `01_schema.sql` + `02_init_data.sql` 均无错误 |
| 2 | 表/外键/唯一键数量正确 | 62 表、86 外键、42 唯一键（贴出 `information_schema` 查询结果） |
| 3 | **模型与数据库零差异** | `python -m scripts.check_models` 输出 `ERROR 0`，表 62/62、外键 86/86、唯一键 42/42 全部一致 |
| 4 | 类型不一致会被抓到 | 手动把一个字段类型改错（如 `Numeric(12,3)` → `Numeric(12,2)`），check 脚本必须报 ERROR；验完改回 |
| 5 | 单号格式正确 | 16 个 key 各生成一次，贴出实际单号，与 4.4.1 表格逐条对齐 |
| 6 | **单号并发安全** | 100 线程并发取同一个 key，得到 100 个互不相同的单号（贴出总数与去重后数量） |
| 7 | 单号跨日行为正确 | 注入两个不同日期，`P`/`S` 继续递增，`XS` 等回到 1 |
| 8 | 迁移基线有效 | `alembic current` 输出 `head`；`alembic upgrade head` 不产生任何 DDL（可开 `--sql` 模式核对为空） |
| 9 | `Rate` 精度未被截断 | 测试断言 `Decimal("1.234567")` 输出不是 `1.23` |
| 10 | 测试全绿 | `pytest -q` 通过，含模型断言与单号并发用例（无数据库时集成用例正确 skip） |
| 11 | 代码质量 | `ruff check .` 零告警 |
| 12 | 文档同步（**仅这两处由你改**） | `db/README.md`：表数量 61→62、`01_schema.sql` 说明行、第 3.2 节表清单加入 `sys_no_seq`、第 5 节缺口清单补充这两项；`docs/07` 第 0.1 节缺口表标注为"已在阶段 1 落地" |
| 13 | **不越界** | `git status` 显示未改动 `docs/01`、`docs/02`、`docs/03`、`docs/04`、`docs/06`、`qianduan/` |

---

## 九、交付时请附上

1. **自检报告**：第八节逐项列出「验收项 → 实际结果 → 验证命令 + 真实输出」，**不要只写"已完成"**；
2. **`check_models.py` 的完整输出**（含摘要总计行）；
3. **16 个单号的实际样例**（贴真实生成的字符串）；
4. **并发测试输出**（线程数、生成总数、去重后数量）；
5. **文档同步清单**（⚠️ 重要）：在 `docs/` 下检索出所有写明"61 张表"或"59 张表"的位置，**逐条列出 `文件:行号` 与原文**，供人工统一修改。已知至少涉及 `docs/01_项目分析报告.md`（3 处）、`docs/02_PRD产品需求文档.md`、`docs/04_数据库设计说明书.md`（写的是 **59** 张，本身就是错的）、`docs/README.md`（多处的口径表）。**你只列清单，不要改这些文件；**
6. **踩坑记录**：过程中遇到的实际问题与解决办法（尤其 `LAST_INSERT_ID` 与 `information_schema` 类型读取的坑）。

======

---

## 附：这份提示词埋的几个"防幻觉"点

| 位置 | 埋的点 | 为什么 |
| --- | --- | --- |
| 1.1 节实测事实 | 直接给出「9 种字段类型」「86 外键」「41 唯一键」「DECIMAL 精度分布」 | 不提交实测数字，模型一定会自己编类型（最常见的是把 `TINYINT` 写成 `Boolean`、把 `DECIMAL(8,5)` 写成 `Numeric(12,2)`） |
| 4.2 节第 4 条 | 特意点名 `base_store.manager_id` 无外键 | 这是唯一"看起来该有外键但没有"的字段，AI 极易自作多情补上，补了就和 86 条对不上 |
| 4.4.2 节坑 1 | `AUTO_INCREMENT` + `LAST_INSERT_ID` 的组合陷阱 | 这是取号惯用法最经典的翻车点：新键第一次取号会拿到自增主键值（比如 1→得到 1043） |
| 4.4.2 节坑 2 | `LAST_INSERT_ID()` 是连接级 | 若换了连接取号，会静默拿到上一条语句的值，表现为"偶发重号"，极难排查 |
| 4.3 节 `Rate` | 精度分档 | 若把所有 DECIMAL 都按金额处理，`lift=1.2345` 会被截断成 `1.23`，**智能分析章节的实验数据直接失真** |
| 验收项 4 | 故意改错类型看脚本能否抓到 | 逼它真验证比对逻辑有效，而不是写个永远输出"全部一致"的假脚本 |
| 验收项 13 | `git status` 证明没越界 | 防止它顺手去改论文文档 |
| 交付项 5 | 让它列"61 张表"的文档位置清单 | 加表后表数量口径会从 61 变 62，**论文文档必须同步**，但改论文属人工决策，让它列清单而不是动手改 |
