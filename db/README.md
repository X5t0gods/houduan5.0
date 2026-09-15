# 数据库交付说明

> 依据《数据设计方案》与《后端接口文档》生成的 MySQL 建库交付包。
> 每个接口的数据支撑都在第 4 节逐条列出，确保**后端照着接口文档实现时有表可用**。

| 项目 | 内容 |
| --- | --- |
| 数据库 | MySQL 8.0（InnoDB / utf8mb4） |
| 库名 | `community_supermarket` |
| 表数量 | **61 张**（8 个数据域） |
| 外键 | 86 条（全部 RESTRICT，无级联删除） |
| 字符集 | utf8mb4 / utf8mb4_general_ci |
| 生成日期 | 2026-09-15 |

---

## 1 文件说明

| 文件 | 作用 | 大小 |
| --- | --- | --- |
| `01_schema.sql` | **建库建表**：创建数据库 + 61 张表 + 86 条外键 | 约 92 KB |
| `02_init_data.sql` | **初始化数据**：权限树、用户、商品、会员、促销等 | 约 35 KB |
| `README.md` | 本文件：使用说明与接口映射 | — |

两个脚本**可重复执行**（结构用 `DROP TABLE IF EXISTS`，数据先 `TRUNCATE` 再按固定 ID 写入）。

---

## 2 执行方式

### 2.1 命令行

```bash
# 先建结构，再灌数据（顺序不能反）
mysql -uroot -p --default-character-set=utf8mb4 < 01_schema.sql
mysql -uroot -p --default-character-set=utf8mb4 < 02_init_data.sql
```

### 2.2 在 MySQL 客户端内执行

```sql
source /path/to/01_schema.sql;
source /path/to/02_init_data.sql;
```

### 2.3 注意事项

- `01_schema.sql` 内含 `CREATE DATABASE IF NOT EXISTS community_supermarket`，
  并会 `DROP TABLE IF EXISTS` 重建所有表——**在已有数据的库上执行会清空数据**；
- 脚本开头 `SET FOREIGN_KEY_CHECKS = 0`，结尾恢复为 `1`，因此建表顺序不敏感；
- 路径含中文时建议先把脚本复制到纯英文路径再 `source`。

---

## 3 数据库概况

### 3.1 数据域

| 域 | 前缀 | 表数 | 职责 |
| --- | --- | --- | --- |
| 基础数据域 | `base_` | 4 | 门店、收银台、计量单位、货架 |
| 权限与用户域 | `sys_` | **11** | 用户、角色、权限、参数、字典、日志、**验证码**、**备份记录** |
| 商品域 | `prd_` | 7 | 分类、SPU、商品、条码、价格历史、标签 |
| 供应商与采购域 | `pur_` | 9 | 供应商、采购单、收货、退货、付款 |
| 库存域 | `inv_` | 9 | 库存快照、**库存流水**、批次、盘点、报损、调拨 |
| 会员与促销域 | `mem_` / `pro_` | 10 | 会员、等级、积分/储值流水、促销、优惠券 |
| 销售域 | `sal_` | 7 | 销售单、明细、支付、退货、挂单、班次 |
| 智能分析域 | `bi_` | 4 | 分析任务、关联规则、推荐缓存、日统计 |

### 3.2 表清单

```
base_store          base_pos            base_unit           base_shelf
sys_user            sys_role            sys_permission      sys_user_role
sys_role_permission sys_config          sys_dict            sys_operation_log
sys_login_log       sys_captcha         sys_backup
prd_category        prd_spu             prd_product         prd_barcode
prd_price_history   prd_tag             prd_product_tag
pur_supplier        pur_supplier_product pur_order          pur_order_item
pur_receipt         pur_receipt_item    pur_return          pur_return_item
pur_payment
inv_stock           inv_stock_flow      inv_batch           inv_check
inv_check_item      inv_loss            inv_loss_item       inv_transfer
inv_transfer_item
mem_member          mem_level           mem_point_flow      mem_balance_flow
mem_tag             mem_member_tag
pro_promotion       pro_promotion_item  pro_coupon          pro_coupon_record
sal_trade           sal_trade_item      sal_trade_payment   sal_return
sal_return_item     sal_hold            sal_session
bi_analysis_task    bi_association_rule bi_recommend_cache  bi_daily_stat
```

---

## 4 接口 → 数据表映射（128 个接口）

### 4.1 认证与权限（6）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /auth/captcha` | **`sys_captcha`**（写入验证码） |
| `POST /auth/login` | `sys_user`、`sys_login_log`、`sys_captcha`（校验并置 used） |
| `POST /auth/logout` | —（无需持久化） |
| `POST /auth/refresh` | —（令牌自包含） |
| `GET /auth/me` | `sys_user`、`sys_user_role`、`sys_role`、`sys_role_permission`、`sys_permission` |
| `POST /auth/password` | `sys_user` |

### 4.2 商品管理（13）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /products` | `prd_product` + JOIN `prd_category`、`base_unit`、`inv_stock` |
| `GET /products/{id}` | `prd_product`、`prd_barcode` |
| `POST /products` | `prd_product`、`prd_barcode`、`prd_price_history` |
| `PUT /products/{id}` | 同上 + `sys_operation_log`（价格变更留痕） |
| `PUT /products/{id}/status` | `prd_product` |
| `DELETE /products/{id}` | `prd_product`（**仅无业务流水时**） |
| `GET /products/barcode/{barcode}` | `prd_barcode`、`prd_product` |
| `POST /products/import` | `prd_product`、`prd_barcode` |
| `GET /categories` | `prd_category` |
| `POST /categories` | `prd_category` |
| `PUT /categories/{id}` | `prd_category` |
| `DELETE /categories/{id}` | `prd_category`（**被引用时只允许停用**） |
| `GET /units` | `base_unit` |

### 4.3 库存管理（14）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /inventory` | `inv_stock` + JOIN `prd_product`、`base_unit`、`base_store`、`inv_batch` |
| `GET /inventory/flows` | `inv_stock_flow` + JOIN `prd_product`、`inv_batch`、`sys_user` |
| `GET /inventory/flows/export` | `inv_stock_flow` |
| `GET /inventory/warnings` | `inv_stock`、`inv_batch` |
| `GET /inventory/checks` | `inv_check` |
| `GET /inventory/checks/{id}` | `inv_check`、`inv_check_item` |
| `POST /inventory/checks` | `inv_check`、`inv_check_item`（**冻结 snapshot_qty**） |
| `PUT /inventory/checks/{id}/items` | `inv_check_item`（计算 diff_qty） |
| `POST /inventory/checks/{id}/submit` | `inv_check`（状态流转） |
| `POST /inventory/checks/{id}/audit` | `inv_check`、`inv_stock`、**`inv_stock_flow`**（写调整流水） |
| `GET /inventory/losses` | `inv_loss`、`inv_loss_item`（接口返回单商品视图，后端 JOIN 组装） |
| `POST /inventory/losses` | `inv_loss`、`inv_loss_item`（一条报损单写一条明细） |
| `GET /inventory/transfers` | `inv_transfer`、`inv_transfer_item` |
| `POST /inventory/transfers` | `inv_transfer`、`inv_transfer_item` |

### 4.4 采购与供应商（14）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /purchases` | `pur_order` + JOIN `pur_supplier` |
| `GET /purchases/{id}` | `pur_order`、`pur_order_item` |
| `POST /purchases` | `pur_order`、`pur_order_item` |
| `PUT /purchases/{id}` | `pur_order`、`pur_order_item` |
| `POST /purchases/{id}/audit` | `pur_order`（状态流转） |
| `POST /purchases/{id}/void` | `pur_order` |
| `POST /purchases/{id}/receive` | `pur_receipt`、`pur_receipt_item`、`pur_order_item`、`inv_stock`、**`inv_stock_flow`**、`inv_batch`、`prd_product`（重算成本） |
| `POST /purchases/direct-receive` | 同上（建单 + 收货 + 入库一步完成） |
| `POST /purchases/returns` | `pur_return`、`pur_return_item`、`inv_stock`、`inv_stock_flow` |
| `GET /suppliers` | `pur_supplier` |
| `POST /suppliers` | `pur_supplier` |
| `PUT /suppliers/{id}` | `pur_supplier` |
| `GET /suppliers/{supplierId}/statement` | `pur_order`、`pur_payment`、`pur_supplier` |
| `POST /suppliers/payments` | `pur_payment`、`pur_supplier`（冲减应付） |

### 4.5 销售与收银（14）

| 接口 | 主要数据表 |
| --- | --- |
| `POST /sales/cart/resolve` | `prd_barcode`、`prd_product`、`inv_stock` |
| `POST /sales/cart/calc` | `pro_promotion`、`pro_promotion_item`、`mem_level`、`mem_member` |
| `POST /sales/trades` | **`sal_trade`**、`sal_trade_item`、`sal_trade_payment`、`inv_stock`、`inv_stock_flow`、`mem_member`、`mem_balance_flow`、`mem_point_flow`、`pro_promotion`（累计统计） |
| `GET /sales/trades` | `sal_trade` + JOIN `mem_member`、`sys_user` |
| `GET /sales/trades/{id}` | `sal_trade`、`sal_trade_item`、`sal_trade_payment` |
| `POST /sales/trades/return` | `sal_return`、`sal_return_item`、`sal_trade`、`sal_trade_item`、`inv_stock`、`inv_stock_flow`、`mem_balance_flow`、`mem_point_flow` |
| `POST /sales/trades/{id}/void` | `sal_trade`、`inv_stock`、`inv_stock_flow` |
| `POST /sales/trades/{tradeId}/receipt` | `sal_trade`（**`print_count` 累加**） |
| `POST /sales/holds` | `sal_hold` |
| `GET /sales/holds` | `sal_hold` |
| `POST /sales/holds/{holdNo}/resume` | `sal_hold` |
| `DELETE /sales/holds/{holdNo}` | `sal_hold` |
| `GET /sales/sessions/current` | `sal_session`、`base_pos`、`sys_user` |
| `POST /sales/sessions/close` | `sal_session`（写实点现金与差异说明） |

### 4.6 会员管理（14）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /members` | `mem_member` + JOIN `mem_level` |
| `POST /members/search` | `mem_member`（手机号后 4 位模糊匹配） |
| `GET /members/{id}` | `mem_member`、`mem_level` |
| `POST /members` | `mem_member` + `mem_balance_flow`（初始充值） |
| `PUT /members/{id}` | `mem_member` |
| `PUT /members/{id}/status` | `mem_member` |
| `GET /members/levels` | `mem_level` |
| `PUT /members/levels/{id}` | `mem_level` |
| `POST /members/{id}/recharge` | `mem_member`、**`mem_balance_flow`**（本金/赠送分开记账） |
| `GET /members/balance-flows` | `mem_balance_flow` |
| `GET /members/point-flows` | `mem_point_flow` |
| `POST /members/{id}/points/adjust` | `mem_member`、`mem_point_flow` |
| `GET /members/{id}/profile` | `sal_trade`、`sal_trade_item`（聚合消费档案） |
| `GET /members/export` | `mem_member`（脱敏导出） |

### 4.7 促销管理（12）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /promotions` | `pro_promotion` |
| `GET /promotions/{id}` | `pro_promotion`、`pro_promotion_item` |
| `POST /promotions` | `pro_promotion`、`pro_promotion_item` |
| `PUT /promotions/{id}` | 同上 |
| `PUT /promotions/{id}/status` | `pro_promotion` |
| `DELETE /promotions/{id}` | `pro_promotion`、`pro_promotion_item` |
| `POST /promotions/simulate` | `pro_promotion`、`pro_promotion_item`、`prd_product`（**不落库**） |
| `GET /promotions/{id}/effect` | `pro_promotion`、`sal_trade`、`sal_trade_item` |
| `GET /coupons` | `pro_coupon` |
| `POST /coupons` | `pro_coupon` |
| `POST /promotions/from-rule` | `pro_promotion`、**`bi_association_rule`**（标记 adopted） |
| `POST /promotions/match` | `pro_promotion`、`pro_promotion_item` |

### 4.8 统计报表（12）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /reports/dashboard` | `sal_trade`、`sal_trade_item`、`inv_stock`、`inv_batch`、`mem_member`、`pur_order` |
| `GET /reports/sales/daily` | `sal_trade`、`sal_return` |
| `GET /reports/sales/hourly` | `sal_trade` |
| `GET /reports/goods/rank` | `sal_trade_item` |
| `GET /reports/goods/category` | `sal_trade_item` + `prd_product`、`prd_category` |
| `GET /reports/gross-profit` | `sal_trade_item`（用 `cost_price` 快照算毛利） |
| `GET /reports/goods/slow-moving` | `prd_product`、`inv_stock`、`sal_trade_item` |
| `GET /reports/inventory/inout` | `inv_stock_flow`（**勾稽校验来源**） |
| `GET /reports/purchase` | `pur_order`、`pur_supplier` |
| `GET /reports/member` | `mem_member`、`sal_trade` |
| `GET /reports/cashier` | `sal_trade`、`sal_session` |
| `GET /reports/{type}/export` | 同对应报表 |

### 4.9 智能数据分析（9）

| 接口 | 主要数据表 |
| --- | --- |
| `POST /bi/analysis/run` | `bi_analysis_task`（建任务）、`sal_trade_item`（数据源） |
| `GET /bi/tasks` | `bi_analysis_task` |
| `GET /bi/tasks/{id}` | `bi_analysis_task` |
| `GET /bi/rules` | `bi_association_rule` |
| `POST /bi/recommend` | `bi_association_rule`、`bi_recommend_cache`、`prd_product` |
| `POST /bi/rules/{ruleId}/adopt` | `bi_association_rule`（置 `adopted=1`）、`pro_promotion` |
| `POST /bi/compare` | `sal_trade_item`（**耗时写 `cost_ms`**，实验数据来源） |
| `GET /bi/forecast` | `sal_trade`、`bi_daily_stat` |
| `GET /bi/replenish` | `inv_stock`、`sal_trade_item`、`pur_supplier_product` |

### 4.10 系统管理（20）

| 接口 | 主要数据表 |
| --- | --- |
| `GET /system/users` | `sys_user` + JOIN `base_store`、`sys_user_role`、`sys_role` |
| `POST /system/users` | `sys_user`、`sys_user_role` |
| `PUT /system/users/{id}` | 同上 |
| `DELETE /system/users/{id}` | `sys_user`、`sys_user_role` |
| `POST /system/users/{id}/reset-password` | `sys_user` |
| `PUT /system/users/{id}/status` | `sys_user` |
| `GET /system/roles` | `sys_role`（聚合 `user_count`） |
| `POST /system/roles` | `sys_role` |
| `PUT /system/roles/{id}` | `sys_role`、`sys_role_permission` |
| `DELETE /system/roles/{id}` | `sys_role`、`sys_role_permission` |
| `GET /system/permissions` | `sys_permission`（树形） |
| `GET /system/configs` | `sys_config` |
| `PUT /system/configs/{id}` | `sys_config`、`sys_operation_log` |
| `GET /system/dicts` | `sys_dict` |
| `GET /system/logs` | `sys_operation_log` |
| `GET /system/login-logs` | `sys_login_log` |
| `GET /system/backups` | **`sys_backup`** |
| `POST /system/backups` | **`sys_backup`**（新增记录） |
| `POST /system/backups/restore` | **`sys_backup`**（更新 `restored_at`） |
| `GET /system/info` | —（运行时信息） |

---

## 5 本次按接口文档补充的内容

逐接口核对时发现 **3 处「接口要用、原表结构没有」** 的缺口，已补齐：

| 缺口 | 影响的接口 | 补充方案 |
| --- | --- | --- |
| **验证码无存储位置** | `GET /auth/captcha`、`POST /auth/login` | 新增表 **`sys_captcha`**。因方案约定不引入 Redis（见《数据设计方案》1.3），验证码落库并设过期时间，由定时任务清理 |
| **备份记录无表** | `GET /system/backups`、`POST /system/backups`、`POST /system/backups/restore` | 新增表 **`sys_backup`**（含 `file_name`、`file_size`、`trigger_type`、`result` 等接口所需字段） |
| **小票打印次数无字段** | `POST /sales/trades/{tradeId}/receipt` | `sal_trade` 增加 **`print_count`** 字段 |

同时修正了 `sys_permission.perm_type` 的注释（原注释写"1 菜单 2 按钮"，与实际使用的
"1 目录 2 菜单 3 按钮"不一致，易误导）。

---

## 6 字段命名差异说明

数据库与接口之间有几处**命名不同但语义对应**的情况，后端输出时需转换：

| 表字段 | 接口字段 | 说明 |
| --- | --- | --- |
| `sys_backup.file_size` | `size` | 备份文件大小 |
| `inv_stock_flow.operator`（用户 ID） | `operator_name` | 需 JOIN `sys_user` 取姓名 |
| `inv_stock_flow.batch_id`（批次 ID） | `batch_no` | 需 JOIN `inv_batch` 取批次号 |
| `inv_check.created_by` / `audited_by` | `created_by_name` | 需 JOIN `sys_user` |
| `pur_order.created_by` | `created_by_name` | 需 JOIN `sys_user` |
| `pro_promotion.created_by` | `created_by_name` | 需 JOIN `sys_user` |
| `mem_balance_flow.operator` | `operator_name` | 需 JOIN `sys_user` |
| `mem_point_flow.operator` | `operator_name` | 需 JOIN `sys_user` |
| `sal_return.operator` | `operator_name` | 需 JOIN `sys_user` |

**设计原则**：数据库存**外键 ID**（规范、省空间、支持改名），接口返回**展示名称**（对用户友好），
中间转换由后端完成。这类差异**不需要改表结构**。

此外，接口中还有一批**计算字段**（表里不存，实时算）：

| 接口字段 | 计算口径 |
| --- | --- |
| `StockItem.stock_status` | 由 `quantity` / `safe_qty` / `remain_days` 判定 |
| `StockItem.stock_amount` | `quantity × avg_cost` |
| `StockItem.remain_days` | `inv_batch.expiry_date − 当前日期` |
| `Member.sleeping_days` | `当前日期 − last_consume_at` |
| `Member.level_name` / `discount_rate` | JOIN `mem_level` |

---

## 7 初始化数据

| 数据 | 数量 |
| --- | --- |
| 门店 / 收银台 / 货架 / 计量单位 | 2 / 3 / 6 / 10 |
| 角色 / 权限 / 角色权限关联 / 用户 | 5 / 32 / 89 / 5 |
| 系统参数 / 字典 / 备份记录 | 18 / 27 / 4 |
| 商品分类 / SPU / 商品 / 条码 | 18（三级）/ 3 / 20 / 21 |
| 供应商 / 供货关系 | 5 / 20 |
| 库存记录 / 期初流水 / 批次 | 20 / 20 / 4 |
| 会员等级 / 标签 / 会员 | 3 / 5 / 5 |
| 促销 / 促销明细 / 优惠券 | 8 / 14 / 4 |

### 演示账号（密码统一 `123456`）

| 账号 | 姓名 | 角色 | 权限范围 |
| --- | --- | --- | --- |
| `admin` | 徐炜城 | 系统管理员 | 全部（32 项） |
| `manager01` | 王建国 | 店长 | 除系统设置外全部（29 项） |
| `cashier01` | 李小雨 | 收银员 | 收银台、交班、会员查看（7 项） |
| `stocker01` | 陈大山 | 库管员 | 商品查看、采购收货、库存全套（11 项） |
| `buyer01` | 张小美 | 采购员兼店长 | 采购全套、商品查看、采购报表（10 项） |

密码为 **bcrypt** 哈希（cost=10），直接可用 `bcrypt.checkpw()` 校验。

---

## 8 验证结果

| 验证项 | 结果 |
| --- | --- |
| 建表执行 | ✅ 61 张表创建成功 |
| 外键创建 | ✅ 86 条全部成功 |
| 初始化数据导入 | ✅ 无错误 |
| **从零重建**（DROP DATABASE 后重跑） | ✅ 成功，脚本可重复执行 |
| 权限码与前端比对 | ✅ **29 个功能权限码零差异** |
| 库存勾稽校验 | ✅ **0 条不一致**（`inv_stock.quantity` = `Σ流水`） |
| 各角色权限分配 | ✅ 32 / 29 / 7 / 11 / 10，符合职责设计 |

### 库存勾稽校验 SQL（可随时跑）

```sql
-- 应返回空结果集
SELECT s.product_id, s.quantity AS snapshot_qty,
       IFNULL(SUM(f.quantity * f.direction), 0) AS flow_qty
FROM inv_stock s
LEFT JOIN inv_stock_flow f
  ON f.product_id = s.product_id AND f.store_id = s.store_id
GROUP BY s.product_id, s.quantity
HAVING ABS(s.quantity - IFNULL(SUM(f.quantity * f.direction), 0)) > 0.0001;
```

---

## 9 相关文档

| 文档 | 位置 |
| --- | --- |
| 数据设计方案（十条关键设计决策） | `qianduan/数据库设计/社区超市销售管理系统_数据设计方案.docx` |
| 数据库设计说明书（字段级） | `qianduan/数据库设计/社区超市销售管理系统_数据库设计说明书.docx` |
| 后端接口文档（128 个接口） | `超市管理系统_接口文档/社区超市销售管理系统_后端接口文档.docx` |
| 详细功能需求文档 | `超市管理系统_需求文档/社区超市销售管理系统_详细功能需求文档.docx` |
