# Qoder 提示词 · 阶段 3：基础数据与商品（13 接口）

> **用法**：在 Qoder 里打开 `C:\Users\徐炜城\Desktop\工作空间\houduan`，切到 **Agent / Quest 模式**，把下面 `======` 之间的内容整段粘贴发送。
>
> **前置条件**：阶段 0（骨架）、阶段 1（62 表模型 + `sys_no_seq` 取号器）、阶段 2（认证与 RBAC）均已完成并通过验收，数据库已建好。
>
> **建议分两次发**：先发「请只读这四个地方：`db/02_init_data.sql` 第 324–403 行（分类/SPU/商品/条码数据）、`db/01_schema.sql` 的 `prd_product`/`prd_barcode`/`prd_category`/`base_unit`/`prd_price_history` 五张表、`docs/06_后端接口文档.md` 第 1092–1228 行、`qianduan/src/views/product/edit.vue` 第 116–140 行，然后回答：① 商品条码存在哪张表、一个商品可以有几个条码？② 商品列表的 `stock_qty` 从哪里取？③ 前端提交新增商品时带了哪两个文档里没有的字段？先不要改任何文件」。
> 等它答对（`prd_barcode` 表、一品多码、`inv_stock.quantity`、字段是 `init_stock` 与 `safe_stock`）再发正式提示词。

---

======

## 你的角色与本次任务

你是一名资深 Python 后端工程师，在 FastAPI + SQLAlchemy 2.0 上做商品基础数据模块。

工作区 `houduan` 是「中小型社区超市销售管理系统」的后端工程。阶段 0–2 已完成，现在做**阶段 3：基础数据与商品**——**13 个接口**。

阶段目标（一句话）：**商品能建档、能检索、能扫码、能批量导入。**

接口清单（全部来自 `docs/06_后端接口文档.md` 3.2 节）：

| # | 方法 | 路径 | 权限码 |
| :--: | --- | --- | :--- |
| 1 | GET | `/products` | `goods:product:list` |
| 2 | GET | `/products/{id}` | `goods:product:list` |
| 3 | POST | `/products` | `goods:product:edit` |
| 4 | PUT | `/products/{id}` | `goods:product:edit` |
| 5 | PUT | `/products/{id}/status` | `goods:product:edit` |
| 6 | DELETE | `/products/{id}` | `goods:product:edit` |
| 7 | GET | `/products/barcode/{barcode}` | `pos:use` |
| 8 | GET | `/categories` | `goods:product:list` |
| 9 | POST | `/categories` | `goods:category` |
| 10 | PUT | `/categories/{id}` | `goods:category` |
| 11 | DELETE | `/categories/{id}` | `goods:category` |
| 12 | GET | `/units` | 登录即可（无需权限码） |
| 13 | POST | `/products/import` | `goods:product:edit` |

---

## 一、开工前必读

| 顺序 | 文件 | 要确认的内容 |
| :--: | --- | --- |
| 1 | `db/01_schema.sql` 的 `prd_category` / `prd_product` / `prd_barcode` / `prd_price_history` / `base_unit` / `prd_spu` | **字段名、类型、精度、唯一键**（这是结构唯一真相源） |
| 2 | `db/02_init_data.sql` 第 **324–403 行** | 18 条分类（5 一级 + 9 二级 + 4 三级）、20 个商品、21 条条码、10 个单位 |
| 3 | `docs/06_后端接口文档.md` **2.3 / 2.4 节**（Schema）与 **3.2 节**（13 接口）、**1.4 节**（错误码）、**1.5 节**（分页） | 请求/响应字段 |
| 4 | `qianduan/src/views/product/edit.vue`、`index.vue`、`category.vue`、`detail.vue` + `qianduan/src/api/product.ts` | 前端实际提交的字段与消费的字段（**行为真相源**） |
| 5 | `qianduan/src/mock/index.ts` 第 **602–638 行** | Mock 的商品列表/详情实现（**有缺陷，见 1.2 节第 ⑩ 条**） |

### 1.1 实测事实（已逐条核对，作为你的实现基准）

**分类（`prd_category` 共 18 条）**

- 一级 5 条：`C01` 生鲜果蔬、`C02` 粮油调味、`C03` 休闲食品、`C04` 酒水饮料、`C05` 日用百货
- 二级 9 条：`C0101`~`C0501`
- 三级 4 条：`C010101` 叶菜类、`C010102` 茄果类、`C010201` 仁果类、`C010202` 热带水果
- ⚠️ **只有「生鲜果蔬」下有三级**，其余一级分类下只有二级

**商品（`prd_product` 共 20 个，`P000001`~`P000020`）**

`category_id` 分布：`13`(2)、`21`(2)、`22`(2)、`31`(3)、`41`(4)、`42`(2)、`51`(1)、`111`(2)、`121`(1)、`122`(1)

> ⚠️ 商品**既挂在二级也挂在三级**（二级 16 个、三级 4 个）。表注释写的是「所属**末级**分类ID」——意思是挂哪级都行，只要是叶子。
> **不要**假设商品只挂三级。

**条码（`prd_barcode` 共 21 条）**

- 商品 **14** 有 **2 个条码**（一品多码，真实存在的数据），其余每个商品 1 个
- `barcode_type`：`1` 主条码 / `2` 附加条码

**单位（`base_unit` 共 10 条）**

- 9 个基本单位（瓶/袋/盒/桶/提/斤/公斤/件/包）+ 1 个辅助单位「箱」（`unit_type=2`、`base_unit_id=1`、`conversion_rate=12.0000`）

**库存**：`inv_stock` 20 条、`inv_stock_flow` 有记录 —— 即 **20 个演示商品全部都有库存与流水**。

### 1.2 契约冲突与文档缺口（**以这里给出的口径为准**）

| # | 冲突 / 缺口 | 文档或前端的字面描述 | **你要实现的口径** |
| :--: | --- | --- | --- |
| ① | **2003「二次确认后重试」根本不存在** | 3.2.3 写「售价低于进价（**前端二次确认后带确认标记重试**）」 | `edit.vue:120-122` 实际只有 `ElMessage.warning(...)`，**既没有 `return` 也没有任何 `force`/`confirm` 标记字段**。→ **后端必须自己兜底返回 `2003` 并拒绝保存**。不要因为文档写了"二次确认"就去发明一个 `force` 参数 |
| ② | **`init_stock` / `safe_stock` 是文档外的字段** | `Product` Schema 与 3.2.3 都没有这两个字段 | 前端 `edit.vue` 的 form 里有，且 `{...form}` 会**原样提交**；成功提示还会说「并生成期初入库流水」。→ **本阶段忽略这两个字段（不落库、不生成库存流水）**。原因：期初入库要写 `inv_stock` + `inv_stock_flow` + 批次，属阶段 5。**必须在交付说明里把这个不一致列出来** |
| ③ | **缺少 4 个错误码** | 1.4 节 2xxx 段只有 `2001`/`2002`/`2003`，没有「商品不存在」「分类不存在」「分类被引用」「有业务流水」 | **补充 4 个新码**（见 1.3）。依据：`request.ts:96` 是 `ERROR_MESSAGE[res.code] ?? res.message ?? '操作失败'`——**未知码会自动 fallback 显示后端返回的 `message`**，所以新增码是安全的，但**必须给中文 message** |
| ④ | **`/products/barcode/{barcode}` 的语义** | 权限是 `pos:use`，说明文字却写「用于**商品建档时的条码查重**」 | 查重场景下「找不到」是**正常结果**（说明条码可用），不该弹错误。→ **找不到时返回 `code=0` + `data=null`**。`6001`（未找到该条码对应的商品）留给阶段 4 的 `/sales/cart/resolve` 用。另：前端**从未调用**过这个接口（只有定义），所以不影响现有页面 |
| ⑤ | **`is_allow_discount` 语义** | 文档写「是否允许**打折**」 | 建表脚本注释是「是否参与**促销**」。**以建表脚本为准**（参与促销） |
| ⑥ | **`avg_cost` 精度** | Schema 里和金额字段一样写 `number` | 实际是 **`DECIMAL(12,4)`**（如 `4.2000`）。⛔ **不能用 `Money` 别名**（会截断成 2 位），必须用 **`Rate` 别名**（保留 6 位） |
| ⑦ | **`Product` Schema 缺 2 个字段** | Schema 里没有 | 建表脚本有 `purchase_unit_id`（采购单位）与 `shelf_id`（默认货架）。→ 实体模型与响应模型**都要有**；响应里允许为 `null` |
| ⑧ | **`GET /units` 的返回类型** | 文档写 `Unit[]` | 前端 `api/product.ts:66` 声明的是 `Array<Record<string, unknown>>`。→ **直接返回 `base_unit` 表的行**（含 `sort`），字段按表结构来即可 |
| ⑨ | **分类停用接口会被塞进 `children`** | — | `category.vue:112` 是 `updateCategory(node.id, { ...node, status: 0 })`，`node` **带 `children` 数组**。→ 请求模型必须**允许并忽略未知字段**（Pydantic 默认忽略，但要确认没有开 `extra="forbid"`） |
| ⑩ | **Mock 的分类名取值有缺陷** | — | `mock/index.ts:609` 用 `categories.flatMap(c => c.children).find(...)`，只找二级 → **挂在三级的 4 个商品会显示成 `—`**。⛔ **不要照抄这个逻辑**。后端直接 `JOIN prd_category` 取 `category_name`，三级也要能取到 |

### 1.3 补充错误码（本阶段新增，写进 `app/core/error_codes.py`）

| 错误码 | 中文 message | 触发场景 |
| :--: | --- | --- |
| `2004` | 商品不存在 | 按 id 查/改/删商品时不存在 |
| `2005` | 分类不存在或操作非法 | 分类 id 不存在；父分类不存在；层级超过三级；该分类下还有子分类（message 要写具体原因） |
| `2006` | 该分类已被 {n} 个商品引用，只能停用不可删除 | 删除被引用分类（**message 里要带真实引用数**） |
| `2007` | 该商品已产生业务流水，不可删除 | 删除有业务流水的商品 |

> 现有 3 个码的复用：`2001` 必填项缺失（商品名称/分类/单位为空、导入文件不是 xlsx 等）、`2002` 商品编码或条码已存在、`2003` 售价低于进价。

### 1.4 关于商品编码与取号器（⛔ 这里有坑）

商品编码格式 `P` + 6 位流水（`P000001`），走阶段 1 的 `sys_no_seq`（`seq_key='P'`，全局 6 位）。

⛔ **坑**：现有 `prd_product` 里已有 `P000001`~`P000020`，而 `sys_no_seq` 是新表、初始为空 → 第一次取号会拿到 `1`，生成 `P000001`，**直接撞 `uk_product_code` 唯一键**。

**处理方式**（按顺序）：
1. 先查 `sys_no_seq` 里 `P` 的 `current_val`；
2. 若为 `NULL` / `0` / 小于现有最大编码，用一个**可重复执行**的脚本 `scripts/init_product_seq.py` 把它初始化为 `SELECT MAX(CAST(SUBSTRING(product_code, 2) AS UNSIGNED)) FROM prd_product`（当前应为 **20**）；
3. 之后再正常取号 → 第一个新商品应是 **`P000021`**。

请把这个脚本作为交付物之一，并在自检报告里贴出「初始化前后 `sys_no_seq` 里 `P` 的值」以及新增商品的编码。

---

## 二、已定技术决策（不得更改）

1. 同步 SQLAlchemy 2.0 + PyMySQL，路由用 `def`（沿用阶段 0）。
2. 分层：**router（参数校验 + 权限声明）→ service（业务编排 + 事务）→ repository（数据访问）**。⛔ 路由里不许出现 SQL/ORM 查询，repository 里不许 `commit`。
3. **业务失败一律 HTTP 200 + 非 0 `code`**。401 只用于「需要登录态但没拿到」，403 用 `code=1040`。
4. 响应体的金额/数量/时间字段**必须用阶段 0/1 定义的别名**：
   - `Money`（2 位）：`purchase_price`、`sale_price`、`member_price`、`min_price`
   - `Rate`（6 位，不是 2 位）：`avg_cost`、`conversion_rate`
   - `Qty`（3 位）：`stock_qty`
   - `DateTimeStr`：`created_at`、`updated_at`
   - ⛔ 不这样做 Pydantic v2 会把 `Decimal` 输出成 **字符串**，前端拿到会算错。
5. **本阶段不套门店数据范围过滤**（`data_scope` / `resolve_store_filter`）：商品、分类、单位是**全局基础数据**，不归属门店。阶段 5 的库存才需要按门店。
6. 允许新增依赖 **`openpyxl`**（导入 Excel 必需）。⛔ 不要装 pandas（阶段 9 才需要）。
7. **不修改前端代码、不修改 `docs/` 下的文档、不修改 `db/*.sql`**。

---

## 三、交付物清单

```
houduan/app/
├── schemas/
│   ├── product.py           # ← 新增：Product / ProductCreate / ProductUpdate / Category / Unit
│   └── common.py            # ← 确认 PageResult 结构（list/total/page/page_size）
├── repositories/
│   ├── product_repository.py   # ← 新增：商品 + 条码 + 价格历史
│   ├── category_repository.py  # ← 新增：分类树
│   └── unit_repository.py      # ← 新增：单位（可合并进 category_repository，自行判断）
├── services/
│   ├── product_service.py   # ← 新增：建档/改档/停启用/删除/导入
│   └── category_service.py  # ← 新增：分类树组装/增改删/引用校验
├── api/v1/
│   ├── product.py           # ← 新增：商品 8 个接口
│   ├── category.py          # ← 新增：分类 4 个接口
│   ├── unit.py              # ← 新增：单位 1 个接口（也可并入 product.py）
│   └── router.py            # ← 注册（tag 分别为「商品管理」「基础数据」）
└── core/error_codes.py      # ← 扩展：新增 2004/2005/2006/2007

houduan/scripts/
└── init_product_seq.py      # ← 新增：把 sys_no_seq 的 P 初始化为现有最大商品编码

houduan/tests/
├── test_services/test_product_service.py    # 校验规则、条码 diff、价格历史
├── test_services/test_category_service.py   # 树组装、层级限制、引用校验
├── test_services/test_import_service.py     # 导入逐行校验与部分成功
└── test_api/test_product_api.py             # 集成：13 接口 + 权限边界
```

---

## 四、逐个功能的实现要求

### 4.1 `GET /products` 商品分页查询

**Query**：`page`、`page_size`（≤100）、`keyword`（模糊匹配 **商品名称 / 商品编码 / 条码**）、`category_id`（可选，Mock 支持但文档没写，建议一并支持）

**响应**：`PageResult<Product>`，每条需含 `category_name`、`unit_name`、`stock_qty`。

⛔ **关键坑：条码匹配不能用 `JOIN`**

条码在 `prd_barcode` 表，商品 14 有 2 个条码。若用 `JOIN prd_barcode` 匹配条码，**商品 14 会在结果里出现两行**（列表重复、分页 total 虚高）。

→ **必须用 `EXISTS` 子查询**：

```sql
WHERE (p.product_name LIKE :kw OR p.product_code LIKE :kw
       OR EXISTS (SELECT 1 FROM prd_barcode b WHERE b.product_id = p.id AND b.barcode LIKE :kw))
```

`stock_qty` 从 `inv_stock` 取（本阶段**只读**，不写）：

```sql
LEFT JOIN inv_stock s ON s.product_id = p.id AND s.store_id = :store_id
```

`store_id` 取当前登录用户的 `store_id`；取不到时为 `0`。⛔ 用 `LEFT JOIN` + `COALESCE(s.quantity, 0)`，**不要用 INNER JOIN**（没有库存记录的商品会整条消失）。

排序：按 `id` 倒序（最新建的在前）。

### 4.2 `GET /products/{id}` 商品详情

- 需含 `barcodes: string[]`（JOIN `prd_barcode`，按 `barcode_type` 升序、主条码在前）
- 同样要含 `category_name` / `unit_name` / `stock_qty`
- 不存在 → `2004`

### 4.3 `POST /products` 新增商品

**请求字段**（前端 `edit.vue` 会提交 `{...form}`，字段比文档多，**必须容忍未知字段**）：

`product_name`、`category_id`、`unit_id`、`purchase_price`、`sale_price`、`member_price`、`min_price`、`spec`、`brand`、`is_weight`、`is_perishable`、`shelf_life_days`、`is_allow_return`、`is_allow_discount`、`is_allow_point`、`remark`、`barcodes`（数组）、`barcode`（单值，冗余字段）、`init_stock`、`safe_stock`

**处理规则**：

| 项 | 规则 |
| --- | --- |
| `product_code` | **后端自动生成**（`sys_no_seq` 的 `P`，见 1.4）。若请求里传了，则用传入值并校验唯一 |
| 条码 | 优先取 `barcodes` 数组；为空时退回 `barcode` 单值。第一个 → `barcode_type=1`（主条码），其余 → `2`（附加） |
| `init_stock` / `safe_stock` | **忽略**（见 1.2 第 ② 条） |
| `member_price` | 可为空；前端会兜底成 `sale_price` |
| 分类 | 必须存在，否则 `2005` |
| 单位 | 必须存在，否则 `2001` |

**校验顺序**（FRS-PRD-002，按顺序，先报先返回）：

1. 必填：`product_name` / `category_id` / `unit_id` 缺失 → `2001`
2. `category_id` 不存在 → `2005`
3. `product_code` 或任一条码已存在 → `2002`
4. `sale_price < purchase_price` → **`2003`**（⛔ 见 1.2 第 ① 条，前端不会拦，后端必须拦）
5. `is_weight = 1` 时必须设置 `unit_id`（本就是必填，顺带确认）→ 否则 `2001`
6. `is_perishable = 1` 时 `shelf_life_days` 必填 → 否则 `2001`

> 文档还写了「建议 `purchase_price ≤ member_price ≤ sale_price`」——注意是**建议**，不是强制。⛔ **不要对 member_price 做强制校验**（初始数据里 `member_price` 普遍低于 `sale_price` 但高于 `purchase_price`，强制校验会让部分合法数据过不去）。只在 `sale_price < purchase_price` 时报 `2003`。

**事务**：`prd_product` + `prd_barcode`（可能多条）在同一事务。
**日志**：写 `sys_operation_log`（`module='商品'`、`action='新增商品'`、`target_type='prd_product'`、`target_id=新商品ID`、`result=1`）。

### 4.4 `PUT /products/{id}` 修改商品

- 不存在 → `2004`
- 校验规则同 4.3，但**排除自身**（编码/条码查重要 `id != :id`）
- ⛔ **条码用 diff 方式更新，不要全量删了重建**：

  ```
  现有条码集合 A，提交的集合 B
  - B - A  → INSERT
  - A - B  → DELETE
  - A ∩ B  → 保持不变（不要动 created_at）
  ```
  理由：全量重建会丢失 `created_at`，且制造无谓的删除/插入。
- **价格变更写 `prd_price_history`**（FRS-PRD-003）：
  - 比较 `purchase_price` / `sale_price` / `member_price` 三个价，**哪个变了就为哪个单独写一条记录**（`change_type` 分别是 `'进价'` / `'售价'` / `'会员价'`）
  - 每条记录填 `old_xxx_price` / `new_xxx_price`、`changed_by=当前用户ID`、`reason`（可为空）
  - ⛔ 三个价都没变时**不要写**历史（否则改个备注也会刷一条垃圾记录）
- 写 `sys_operation_log`（`action='修改商品'`）

### 4.5 `PUT /products/{id}/status` 停用 / 启用

- 请求体 `{"status": 1}`（`1` 在售 / `0` 停用），响应 `data: null`
- 不存在 → `2004`
- **只改状态位，不做其他业务判断**
- 停用后收银台扫码应返回 `6002` —— 那是**阶段 4** 的事，本阶段不实现
- 写 `sys_operation_log`（`action='停用商品'` / `'启用商品'`）

### 4.6 `DELETE /products/{id}` 删除商品

**仅当商品无任何业务流水时允许删除，否则返回 `2007`。**

⛔ **这里有三个必须注意的点**：

**点 1：判定范围**。需要检查这些表是否引用了该商品（只要有任一记录就拒绝）：

`sal_trade_item`、`sal_return_item`、`pur_order_item`、`pur_receipt_item`、`pur_return_item`、`inv_stock`、`inv_stock_flow`、`inv_batch`、`inv_check_item`、`inv_loss_item`、`inv_transfer_item`

> **不算业务流水**的表（这些是配置/关联，删除商品时应一并清理，不阻止删除）：`prd_barcode`、`prd_product_tag`、`pur_supplier_product`、`prd_price_history`、`pro_promotion_item`（`gift_product_id`）、`bi_recommend_cache`
> ⚠️ 但 `pro_promotion_item` 与 `bi_recommend_cache` 属于阶段 6/9，**本阶段只需清理 `prd_barcode` 与 `prd_product_tag` 即可**，其余在交付说明里标注为待办。

**点 2：⛔ 必须先删子表**。18 张表外键引用 `prd_product(id)` 且**全部是 `RESTRICT`**。若不先删 `prd_barcode`，删商品会直接抛外键约束错误（500），而不是优雅的业务错误。

**点 3：20 个演示商品一个都删不掉**。它们**全部**有 `inv_stock` + `inv_stock_flow` 记录 → 点删除必然返回 `2007`。
→ **这是预期行为**，请在交付说明里写清楚。要验证删除成功路径，**必须先新建一个商品再删它**（新建商品没有 `inv_stock` 记录，因为本阶段不写库存）。

### 4.7 `GET /products/barcode/{barcode}` 按条码查商品

- 权限 `pos:use`
- **找不到时返回 `code=0` + `data=null`**（不是 `6001`，理由见 1.2 第 ④ 条）
- ⛔ 商品已停用（`status=0`）时**照样返回**（这个接口的用途是查重，不是收银）；阶段 4 的 `/sales/cart/resolve` 才需要判断停用
- 条码匹配要支持 `prd_barcode` 里的附加条码（不只主条码）

### 4.8 `GET /categories` 分类树

- 返回**树形结构**（`children` 嵌套），最多三级
- 按 `sort` 升序、`id` 升序排列
- `children` 为空时返回 `[]`（不要返回 `null`，前端 `el-tree` 会出问题）
- 不需要分页
- ⛔ **不要用递归 SQL / 每层一次查询**（N+1）。分类最多三级且总量小，**一次性查出全部 18 条再在内存里组装树**即可，并把这个理由写成注释

### 4.9 ~ 4.11 分类增 / 改 / 删

**新增 `POST /categories`**
- `parent_id = 0` → `level = 1`
- `parent_id != 0` → 父分类必须存在，`level = 父.level + 1`
- ⛔ `level > 3` → `2005`（message 写明「分类最多三级」）
- `category_code` 唯一（`uk_category_code`）→ 重复返回 `2002`
- ⛔ **必须容忍 `children` 等未知字段**（见 1.2 第 ⑨ 条）

**修改 `PUT /categories/{id}`**
- 不存在 → `2005`
- ⛔ **禁止把父分类改成自己的子孙**（会把树搞成环）。校验：新 `parent_id` 不能等于自己，也不能是自己的后代 → 否则 `2005`
- 层级变化时要重算 `level`

**删除 `DELETE /categories/{id}`**
- 不存在 → `2005`
- **有子分类** → `2005`（message：「该分类下还有子分类，请先处理子分类」）
- **被商品引用** → `2006`，**message 必须带真实引用数**：`该分类已被 {n} 个商品引用，只能停用不可删除`
  （前端 `category.vue:111` 的文案是 `该分类已被 ${used} 个商品引用（FRS-PRD-004），只能停用不可删除`，引用数由后端提供）
- 无引用无子分类 → 真删

### 4.12 `GET /units` 计量单位列表

- 登录即可，**不加权限码**
- 返回 `base_unit` 全部记录，按 `sort` 升序
- 直接返回表行（含 `unit_type`、`base_unit_id`、`conversion_rate`、`sort`），字段类型用对应别名（`conversion_rate` → `Rate`）
- 不需要分页

### 4.13 `POST /products/import` Excel 批量导入

**请求**：`multipart/form-data`，字段名 **`file`**（前端 `index.vue:41` 是 `fd.append('file', file.raw)`，不能改名）

**响应**：

```json
{ "total": 10, "success": 8, "failed": 2,
  "errors": ["第 3 行：商品编码已存在", "第 5 行：商品名称不能为空"] }
```

**实现要求**：

1. **只支持 `.xlsx`**（用 `openpyxl` 的 `read_only=True` 模式）。`.xls` 是老格式，`xlrd` 2.x 已不支持 xlsx、反向也不兼容 —— **收到 `.xls` 直接返回 `2001`**，message 说明「请上传 .xlsx 格式文件」。把这个取舍写进注释。
2. **逐行校验 + 逐行提交**，⛔ **不允许整批回滚**（文档明确要求「部分成功即可用」）。
   建议：每行一个独立事务（或用 savepoint）。几百行的量级下逐行提交的性能可接受，把理由写成注释。
3. 每行的校验规则与 4.3 **完全一致**（必填、分类存在、编码/条码重复、售价低于进价）。
4. ⛔ **要校验本批内部的重复**：第 3 行和第 7 行填了同一个编码，两行都应失败（不能第一行成功、第二行撞库）。
5. `errors` 格式固定为 `第 {行号} 行：{原因}`，**行号从表头之后的第 1 行数据开始计为「第 1 行」**（即 Excel 的第 2 行）。
6. 分类列：按 **分类名称** 匹配（不是编码）。找不到分类 → 该行失败。
7. 条码列：支持多个条码用 `,` 或 `、` 分隔（一品多码）。
8. 导入的商品编码**同样走 `sys_no_seq` 的 `P`**（若 Excel 里没填编码）。
9. 文件大小/行数上限：建议限制 **≤ 2000 行**，超出返回 `2001`。
10. 写 `sys_operation_log`（`action='导入商品'`，`after_value` 可放 `{"total":x,"success":y,"failed":z}`）

**Excel 模板**（前端没有模板下载接口，但你需要定义列顺序并在交付说明里写清楚）：

| 列 | 字段 | 必填 |
|:--:|---|:--:|
| A | 商品名称 | ✅ |
| B | 分类名称 | ✅ |
| C | 单位名称 | ✅ |
| D | 商品编码 | 否（不填则自动生成） |
| E | 规格 | 否 |
| F | 条码（多个用逗号分隔） | 否 |
| G | 进价 | ✅ |
| H | 售价 | ✅ |
| I | 会员价 | 否 |
| J | 是否称重（1/0） | 否 |
| K | 保质期天数 | 否 |
| L | 备注 | 否 |

---

## 五、编码规范（沿用阶段 0，不放宽）

1. 完整类型注解；公开函数 `Args` / `Returns` / `Raises` docstring。
2. 注释解释**为什么**——尤其把 1.2 节的 10 处冲突、4.1 的 EXISTS 坑、4.4 的条码 diff、4.6 的外键 RESTRICT、1.4 的取号器冲突写成代码注释。
3. 不裸 `print`、不吞异常；异常信息不要回显给前端。
4. 中文注释、英文标识符；`ruff check .` 零告警。

---

## 六、明确不做的事（越界即返工）

1. ⛔ 不实现其他阶段的接口（库存、采购、会员、促销、销售、BI 一律不做）。
2. ⛔ **不写 `inv_stock` / `inv_stock_flow`**（阶段 5 负责）——本阶段**只读** `inv_stock.quantity` 用于列表展示。
3. ⛔ **不处理 `init_stock`（期初库存）**——见 1.2 第 ② 条。
4. ⛔ 不写分类的「批量删除 / 移动 / 拖拽排序」。
5. ⛔ 不做商品图片上传（`image_url` 只是字符串字段，不做文件服务）。
6. ⛔ 不做 SPU 的增删改（`prd_spu` 本阶段只读，商品建档不强制关联 SPU）。
7. ⛔ 不装 pandas / mlxtend（只装 openpyxl）。
8. ⛔ 不改前端代码、不改 `docs/`、不改 `db/*.sql`（包括**不要**给 `prd_product` 加字段）。
9. ⛔ 不做多余抽象（不要为 13 个接口造通用 CRUD 框架）。

---

## 七、实施顺序

| 步 | 动作 | 完成判据 |
|:--:|---|---|
| 1 | `scripts/init_product_seq.py` + 运行 | `sys_no_seq` 的 `P` = 20；新增第一个商品得到 `P000021` |
| 2 | `schemas/product.py` + 错误码扩展 | 序列化测试：`sale_price` 是 number、`avg_cost` 保留 4 位 |
| 3 | `category_repository` / `category_service` + 4 个分类接口 | 树结构正确（18 条、3 级）、引用校验生效 |
| 4 | `unit_repository` + `/units` | 返回 10 条、按 sort 排序 |
| 5 | `product_repository` + 商品 CRUD 5 个接口 | 列表无重复行、详情带 barcodes |
| 6 | 条码查重接口 + 删除（含子表清理） | 新建→删除成功；演示商品删除返回 2007 |
| 7 | 导入接口 | 含错误行的 xlsx 能返回逐行报告且部分成功 |
| 8 | 集成测试 + `ruff` | 全绿 |

---

## 八、验收标准（逐条可验证）

| # | 验收项 | 判定方式 |
|:--:|---|---|
| 1 | 取号器不撞号 | 跑 `init_product_seq.py` 后贴出 `P` 的值（应为 **20**）；连续新建 3 个商品，编码应为 `P000021`/`P000022`/`P000023`，**不报唯一键冲突** |
| 2 | 列表字段完整 | `GET /products?page=1&page_size=5` 返回 20 条（total=20），每条含 `category_name` / `unit_name` / `stock_qty` |
| 3 | **三级分类名称能取到** | 商品「有机小番茄」（`category_id=111`，三级）的 `category_name` 是 **「叶菜类」**，⛔ **不是 `—`**（这是修正 Mock 缺陷的验证） |
| 4 | **条码搜索不产生重复行** | `GET /products?keyword=<商品14的第二个条码>` → 结果只有 **1 条**，`total=1`；再用商品 14 的**主条码**搜，同样 1 条 |
| 5 | 一品多码 | `GET /products/14` 的 `barcodes` 返回 **2 个**元素 |
| 6 | **售价低于进价被拦** | `POST /products` 传 `purchase_price=10, sale_price=5` → `curl -i` 验证 **HTTP 200** + `code=2003` |
| 7 | 重复条码/编码 | 用已存在的条码建商品 → `2002`；重复 `product_code` → `2002` |
| 8 | 必填校验 | 不传 `product_name` → `2001`；`is_perishable=1` 但不传 `shelf_life_days` → `2001` |
| 9 | 价格变更历史 | 改某商品的 `sale_price` → `prd_price_history` 新增 **1 条**，`change_type='售价'`，`old_sale_price`/`new_sale_price` 正确；**只改备注时不产生新记录** |
| 10 | 条码 diff 更新 | 商品详情改条码（删 1 个加 1 个）→ `prd_barcode` 里保留的条码 `created_at` **未变**，新增的 `created_at` 是新的 |
| 11 | 演示商品删不掉 | 对 20 个演示商品任一执行 `DELETE` → **`2007`**，且 ⛔ **不是 500 外键错误** |
| 12 | 新建商品能删 | 新建 1 个商品 → `DELETE` 成功 → 查库确认 `prd_barcode` 里该商品的条码**已被清理**（不能残留孤儿记录） |
| 13 | 分类树结构 | `GET /categories` 返回 **5 个一级节点**，共 **18 条**；「生鲜果蔬」下有 3 个二级，「新鲜蔬菜」下有 2 个三级；每个节点的 `children` 是数组（叶子为 `[]`，**不是 null**） |
| 14 | 分类层级限制 | 尝试在三级分类下新建子分类 → `2005`，message 含「三级」 |
| 15 | **分类被引用** | 删除 `category_id=41`（4 个商品引用）→ `2006`，message **必须含「4 个商品」** |
| 16 | 分类有子分类 | 删除 `category_id=1`（有子分类）→ `2005` |
| 17 | **分类环检测** | 把 `category_id=1` 的 `parent_id` 改成 `11`（它自己的孙子）→ `2005`，**不能把树搞成环** |
| 18 | 单位列表 | `GET /units` 返回 **10 条**，含「箱」（`unit_type=2`、`conversion_rate` 输出为 number 且是 `12`） |
| 19 | **Decimal 序列化** | 响应里 `sale_price` 是 **number**（不是 `"6.8"` 字符串）；`avg_cost` 保留 **4 位**（如 `4.2` 或 `4.2000` 均可，但**不能是字符串**） |
| 20 | 条码查重语义 | `GET /products/barcode/不存在的条码` → **`code=0` + `data=null`**（⛔ 不是 `6001` 错误）；用商品 14 的**附加条码**查能查到 |
| 21 | **导入部分成功** | 构造 xlsx：10 行数据，其中第 3 行编码重复、第 5 行名称为空 → 返回 `total=10, success=8, failed=2`，`errors` 含「第 3 行」「第 5 行」；查库确认 **8 条已落库** |
| 22 | 导入批内重复 | xlsx 里第 2 行和第 4 行填同一编码 → **两行都失败**（不能一行成功） |
| 23 | 导入非 xlsx | 上传 `.xls` 或 `.txt` → `2001`，message 说明格式不支持 |
| 24 | **权限边界** | `cashier01`（无 `goods:product:edit`）调 `POST /products` → **403**；`stocker01` 调 `DELETE /categories/{id}` → 403；`admin` 全部通过 |
| 25 | 未登录 | 不带 token 调 `GET /products` → **401**（HTTP 状态码真的是 401） |
| 26 | 前端真联调 | `VITE_USE_MOCK=false` 启动前端：商品列表 20 条、分类名正确显示、编辑页能新建商品（编码自动生成）、分类页删除被引用分类时提示带数字、导入弹窗能上传并看到成功/失败数 |
| 27 | 测试与质量 | `pytest -q` 全绿；`ruff check .` 零告警 |

> 第 26 条是本阶段的**最终验收**：列表和分类名只有真前端能看出问题（尤其是第 3 条的三级分类名）。

---

## 九、交付时请附上

1. **自检报告**：第八节逐项「验收项 → 实际结果 → 验证命令 + 真实输出」，**不要只写"已完成"**；
2. **取号器实测**：`init_product_seq.py` 执行前后 `sys_no_seq` 里 `P` 的值，以及新建 3 个商品的编码；
3. **三级分类名的真实返回**（证明不是 `—`）；
4. **导入测试用的 xlsx 文件**（放到 `tests/fixtures/` 下，含故意造错的行），以及导入响应的真实 JSON；
5. **`prd_price_history` 的真实记录**（改价后）；
6. **1.2 节 10 处冲突的处理说明** + **你自行决定的补充约定清单**（至少含：`init_stock` 忽略、4 个新错误码、`.xls` 不支持、条码查重返回 null、导入列顺序定义）；
7. **待办清单**：本阶段没做但后续要补的（`init_stock` 期初库存、`pro_promotion_item`/`bi_recommend_cache` 的清理）；
8. 踩坑记录。

======

---

## 附：这份提示词埋的"防幻觉"点

| 位置 | 埋的点 | 为什么 |
|---|---|---|
| 1.2 ① | 揭穿「二次确认后重试」并不存在 | 文档白纸黑字写了这个流程，但前端既没 `return` 也没 `force` 字段。不点破，它会去发明一个前端根本不会传的 `force` 参数，结果低于进价的商品照样能存进去 |
| 1.2 ② | 点出 `init_stock` / `safe_stock` 两个文档外字段 | 前端会提交，但期初入库属阶段 5。不点破，它要么报错拒收（前端永远保存失败），要么顺手写库存逻辑（越界到阶段 5） |
| 1.4 | 取号器与现有 20 个商品撞号 | `sys_no_seq` 是新表、初始为空，第一次取号得到 `P000001` 直接撞唯一键。不预先初始化，第一个新建商品就会 500 |
| 4.1 | ⛔ 条码搜索必须用 `EXISTS` 不能 `JOIN` | 商品 14 有 2 个条码，`JOIN` 会让它在列表里出现两次、`total` 虚高。这是**真实数据**会触发的 bug，不是理论风险 |
| 4.1 | `stock_qty` 用 `LEFT JOIN` 不是 `INNER JOIN` | 用 INNER JOIN 会让没有 `inv_stock` 记录的商品（比如刚建的）直接从列表消失 |
| 4.4 | 条码用 diff 更新而非全量重建 | 全量删建会丢 `created_at`，制造无谓的删除/插入 |
| 4.4 | 三个价都没变时不写价格历史 | 否则改个备注也刷一条历史记录，审计表很快被垃圾数据填满 |
| 4.6 | 删商品必须先清 `prd_barcode` | 18 张表外键**全部 RESTRICT**，不清子表会抛 500 外键错误而不是优雅的 2007 |
| 4.6 | 明说「20 个演示商品一个都删不掉」 | 不预先说明，验收时会以为删除功能坏了；同时也指明了验证成功路径的方法（新建再删） |
| 4.8 | 分类树一次性查全量再内存组装 | 防止它写递归 SQL 或每层一次查询（N+1）。18 条数据没必要优化 |
| 4.9 | 禁止把父分类改成自己的子孙 | 会把树搞成环，页面直接白屏。AI 几乎不会主动想到这个校验 |
| 4.13 | 导入要校验**批内**重复 | 只查数据库的话，第 2 行成功、第 4 行撞库，报告会很难看且数据不一致 |
| 4.13 | 明确只支持 `.xlsx` | `xlrd` 2.x 不支持 xlsx、openpyxl 不支持 xls，两者不兼容。不明确会浪费时间折腾 `.xls` |
| 验收 3 | 三级分类名必须是「叶菜类」不是 `—` | Mock 的 `flatMap(children)` 有这个缺陷，照抄就会复现 |
| 验收 4 | 用商品 14 的两个条码分别搜索，都只返回 1 条 | 直接验证 EXISTS 坑有没有踩 |
| 验收 20 | 条码查重找不到时 `code=0` + `data=null` | 防止它照抄文档里的 `6001`（那是收银扫码用的，查重场景下会误弹错误） |
| 1.3 | 说明新增错误码是安全的 | 依据是 `request.ts:96` 的 `?? res.message` fallback。不说清楚，它可能会硬塞进 `2001` 导致提示文案驴唇不对马嘴 |
