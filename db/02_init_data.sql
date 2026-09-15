-- ============================================================================
--  中小型社区超市销售管理系统  ·  初始化数据脚本
--  Community Supermarket Sales Management System  (CSSMS)
-- ----------------------------------------------------------------------------
--  数据库    :  MySQL 8.0
--  脚本版本  :  V1.0
--  编制日期  :  2026-09-15
--  执行顺序  :  必须先执行 01_schema.sql
-- ----------------------------------------------------------------------------
--  本脚本包含
--    第 1 节  门店与收银台
--    第 2 节  计量单位与货架
--    第 3 节  角色与权限（权限码与前端路由严格对齐，共 29 个功能权限）
--    第 4 节  系统用户与用户角色
--    第 5 节  系统参数与数据字典
--    第 6 节  会员等级与会员标签
--    第 7 节  商品分类、SPU 与商品档案（含条码）
--    第 8 节  供应商与供货关系
--    第 9 节  期初库存与库存流水
--    第 10 节 会员档案
--    第 11 节 促销活动
-- ----------------------------------------------------------------------------
--  说明：脚本可重复执行（先清空被管理的表，再按固定 ID 写入）
--        演示账号密码统一为 123456（bcrypt 哈希已生成）
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

USE `community_supermarket`;

-- 清空本脚本负责的表（按固定 ID 重建，保证可重复执行）
TRUNCATE TABLE `base_shelf`;
TRUNCATE TABLE `base_pos`;
TRUNCATE TABLE `base_store`;
TRUNCATE TABLE `base_unit`;
TRUNCATE TABLE `sys_role_permission`;
TRUNCATE TABLE `sys_user_role`;
TRUNCATE TABLE `sys_user`;
TRUNCATE TABLE `sys_role`;
TRUNCATE TABLE `sys_permission`;
TRUNCATE TABLE `sys_config`;
TRUNCATE TABLE `sys_dict`;
TRUNCATE TABLE `sys_captcha`;
TRUNCATE TABLE `sys_backup`;
TRUNCATE TABLE `mem_member_tag`;
TRUNCATE TABLE `mem_tag`;
TRUNCATE TABLE `mem_member`;
TRUNCATE TABLE `mem_level`;
TRUNCATE TABLE `prd_barcode`;
TRUNCATE TABLE `prd_product`;
TRUNCATE TABLE `prd_spu`;
TRUNCATE TABLE `prd_category`;
TRUNCATE TABLE `pur_supplier_product`;
TRUNCATE TABLE `pur_supplier`;
TRUNCATE TABLE `inv_stock_flow`;
TRUNCATE TABLE `inv_stock`;
TRUNCATE TABLE `inv_batch`;
TRUNCATE TABLE `pro_promotion_item`;
TRUNCATE TABLE `pro_promotion`;


-- ============================================================================
--  第 1 节  门店与收银台
-- ============================================================================

INSERT INTO `base_store` (`id`, `store_code`, `store_name`, `address`, `phone`, `business_hours`, `manager_id`, `status`, `remark`) VALUES
(1, 'S000001', '朝阳路店', '朝阳路 128 号',  '010-88880001', '07:00-22:00', 2, 1, '主营门店'),
(2, 'S000002', '文化路店', '文化路 56 号',   '010-88880002', '07:30-21:30', NULL, 1, '二店');

INSERT INTO `base_pos` (`id`, `store_id`, `pos_code`, `pos_name`, `status`) VALUES
(1, 1, 'POS01', '01 号收银台', 1),
(2, 1, 'POS02', '02 号收银台', 1),
(3, 2, 'POS01', '文化路店 01 号收银台', 1);


-- ============================================================================
--  第 2 节  计量单位与货架
-- ============================================================================

-- unit_type: 1 基本单位 / 2 辅助单位（辅助单位按 conversion_rate 折合基本单位）
INSERT INTO `base_unit` (`id`, `unit_name`, `unit_type`, `base_unit_id`, `conversion_rate`, `sort`, `status`) VALUES
(1, '瓶',   1, NULL, 1.0000, 1, 1),
(2, '袋',   1, NULL, 1.0000, 2, 1),
(3, '盒',   1, NULL, 1.0000, 3, 1),
(4, '桶',   1, NULL, 1.0000, 4, 1),
(5, '提',   1, NULL, 1.0000, 5, 1),
(6, '斤',   1, NULL, 1.0000, 6, 1),
(7, '公斤', 1, NULL, 1.0000, 7, 1),
(8, '箱',   2, 1,    12.0000, 8, 1),
(9, '件',   1, NULL, 1.0000, 9, 1),
(10, '包',  1, NULL, 1.0000, 10, 1);

INSERT INTO `base_shelf` (`id`, `store_id`, `shelf_code`, `shelf_name`, `area`, `status`) VALUES
(1, 1, 'A01', 'A 区 01 货架', '生鲜区', 1),
(2, 1, 'B01', 'B 区 01 货架', '粮油区', 1),
(3, 1, 'C01', 'C 区 01 货架', '零食区', 1),
(4, 1, 'D01', 'D 区 01 货架', '饮料区', 1),
(5, 1, 'E01', 'E 区 01 货架', '日化区', 1),
(6, 1, 'R01', '收银台背面堆头', '收银区', 1);


-- ============================================================================
--  第 3 节  角色与权限
--  权限码与前端 src/router/routes.ts 的 meta.perm 严格一致（29 个功能权限）
-- ============================================================================

INSERT INTO `sys_role` (`id`, `role_code`, `role_name`, `data_scope`, `description`, `sort`, `status`) VALUES
(1, 'ADMIN',     '系统管理员', 'ALL',   '用户、角色、权限、参数、备份、日志',        1, 1),
(2, 'MANAGER',   '店长',       'STORE', '商品价格、促销、采购审批、全部报表',        2, 1),
(3, 'CASHIER',   '收银员',     'SELF',  '收银结算、会员办理、退货申请、交班',        3, 1),
(4, 'STOCKER',   '库管员',     'STORE', '入库、出库、盘点、报损、临期处理',          4, 1),
(5, 'PURCHASER', '采购员',     'STORE', '供应商维护、采购下单、收货、对账',          5, 1);

-- ---------------------------------------------------------------------------
--  权限树：3 个一级目录 + 29 个功能权限
--  perm_type: 1 目录 / 2 菜单（页面级权限）
--  一级目录的 perm_code 以 menu: 开头，仅用于权限树的层级展示，
--  后端返回 /auth/me 的 permissions 时应只返回 perm_type = 2 的权限码。
-- ---------------------------------------------------------------------------
INSERT INTO `sys_permission` (`id`, `parent_id`, `perm_code`, `perm_name`, `perm_type`, `path`, `icon`, `sort`, `status`) VALUES
-- 一级目录
(1,  0, 'menu:daily',         '日常经营',    1, NULL,             NULL,        1, 1),
(2,  0, 'menu:stock',         '进销存',      1, NULL,             NULL,        2, 1),
(3,  0, 'menu:analysis',      '分析与设置',  1, NULL,             NULL,        3, 1),

-- 日常经营
(11, 1, 'dashboard:view',     '经营总览',        2, '/dashboard',       'DataLine',    1, 1),
(12, 1, 'pos:use',            '收银台',          2, '/pos',             'ShoppingCart',2, 1),
(13, 1, 'pos:session',        '交班对账',        2, '/pos/session',     'Clock',       3, 1),
(14, 1, 'member:list',        '会员管理',        2, '/member',          'User',        4, 1),
(15, 1, 'member:detail',      '会员详情',        2, NULL,               NULL,          5, 1),
(16, 1, 'member:level',       '等级与积分规则',  2, NULL,               NULL,          6, 1),
(17, 1, 'promo:list',         '促销管理',        2, '/promotion',       'Present',     7, 1),
(18, 1, 'promo:edit',         '促销配置',        2, NULL,               NULL,          8, 1),
(19, 1, 'promo:coupon',       '优惠券管理',      2, NULL,               NULL,          9, 1),

-- 进销存
(21, 2, 'goods:product:list', '商品管理',        2, '/product',         'Box',         1, 1),
(22, 2, 'goods:product:edit', '商品建档',        2, NULL,               NULL,          2, 1),
(23, 2, 'goods:category',     '分类管理',        2, NULL,               NULL,          3, 1),
(24, 2, 'pur:order:list',     '采购管理',        2, '/purchase',        'ShoppingBag', 4, 1),
(25, 2, 'pur:order:create',   '采购建单',        2, NULL,               NULL,          5, 1),
(26, 2, 'pur:receipt',        '收货入库',        2, NULL,               NULL,          6, 1),
(27, 2, 'pur:supplier',       '供应商管理',      2, NULL,               NULL,          7, 1),
(28, 2, 'inv:stock:list',     '库存管理',        2, '/inventory',       'TakeawayBox', 8, 1),
(29, 2, 'inv:flow',           '库存流水',        2, NULL,               NULL,          9, 1),
(30, 2, 'inv:check',          '库存盘点',        2, NULL,               NULL,         10, 1),
(31, 2, 'inv:loss',           '报损管理',        2, NULL,               NULL,         11, 1),
(32, 2, 'inv:transfer',       '门店调拨',        2, NULL,               NULL,         12, 1),

-- 分析与设置
(41, 3, 'report:sales',       '销售报表',        2, '/report/sales',    'Histogram',   1, 1),
(42, 3, 'report:goods',       '商品与毛利报表',  2, NULL,               NULL,          2, 1),
(43, 3, 'report:inv',         '库存与采购报表',  2, NULL,               NULL,          3, 1),
(44, 3, 'bi:analysis',        '智能分析',        2, '/bi',              'MagicStick',  4, 1),
(45, 3, 'bi:compare',         '算法性能对比',    2, NULL,               NULL,          5, 1),
(46, 3, 'sys:user',           '用户管理',        2, '/system/user',     'Setting',     6, 1),
(47, 3, 'sys:role',           '角色与权限',      2, NULL,               NULL,          7, 1),
(48, 3, 'sys:config',         '参数与备份',      2, NULL,               NULL,          8, 1);

-- ---------------------------------------------------------------------------
--  角色-权限分配
--    ADMIN     : 全部（目录 + 29 个功能权限）
--    MANAGER   : 除 sys:* 三个权限外的全部
--    CASHIER   : 收银相关 + 会员查看
--    STOCKER   : 商品查看 + 采购收货 + 库存全套
--    PURCHASER : 采购全套 + 商品查看 + 采购报表
-- ---------------------------------------------------------------------------

-- ADMIN：全部权限
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT 1, `id` FROM `sys_permission`;

-- MANAGER：全部功能权限，但不含系统设置三项
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT 2, `id` FROM `sys_permission`
WHERE `perm_code` NOT IN ('sys:user', 'sys:role', 'sys:config');

-- CASHIER：经营总览、收银台、交班、会员查看、促销查看
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT 3, `id` FROM `sys_permission`
WHERE `perm_code` IN (
  'menu:daily',
  'dashboard:view', 'pos:use', 'pos:session',
  'member:list', 'member:detail',
  'promo:list'
);

-- STOCKER：经营总览、商品查看、采购查看与收货、库存全套
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT 4, `id` FROM `sys_permission`
WHERE `perm_code` IN (
  'menu:daily', 'menu:stock',
  'dashboard:view',
  'goods:product:list',
  'pur:order:list', 'pur:receipt',
  'inv:stock:list', 'inv:flow', 'inv:check', 'inv:loss', 'inv:transfer'
);

-- PURCHASER：经营总览、商品查看、采购全套、库存与采购报表
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT 5, `id` FROM `sys_permission`
WHERE `perm_code` IN (
  'menu:daily', 'menu:stock', 'menu:analysis',
  'dashboard:view',
  'goods:product:list',
  'pur:order:list', 'pur:order:create', 'pur:receipt', 'pur:supplier',
  'report:inv'
);


-- ============================================================================
--  第 4 节  系统用户与用户角色
--  演示账号密码统一为 123456（bcrypt cost=10 哈希）
-- ============================================================================

INSERT INTO `sys_user` (`id`, `username`, `password_hash`, `real_name`, `phone`, `store_id`, `status`, `remark`) VALUES
(1, 'admin',     '$2b$10$SXVnXHXSNdwlWQFhSDtNSOowUE9.sq2CxSfjU7nVnUIJdxE/DjxzO', '徐炜城', '13800000001', 1, 1, '系统管理员，拥有全部权限'),
(2, 'manager01', '$2b$10$z.JExW3dbcMhZ2bgduIH0eWpwHi4OxVcbiQLebUk7W9kSMD3i86rC', '王建国', '13800000002', 1, 1, '店长'),
(3, 'cashier01', '$2b$10$9pC7TpMTObkZWABTpoN0uuycAYKtdDLkFEY4dUFawIuBKV0kh9bNu', '李小雨', '13800000003', 1, 1, '收银员'),
(4, 'stocker01', '$2b$10$bn5xLPcCCSLu5d7eSUsiG.kfjV7qlVJvdoYEE0OXBDHMB5qrgV2ke', '陈大山', '13800000004', 1, 1, '库管员'),
(5, 'buyer01',   '$2b$10$L2/fYr.qu.MMHvdfpnQs3uOnkD150ks0CYA.r.DvY23YH2e.hTeUO', '张小美', '13800000005', 1, 1, '采购员');

-- 用户-角色（buyer01 兼店长，演示「一人多岗」）
INSERT INTO `sys_user_role` (`user_id`, `role_id`) VALUES
(1, 1),
(2, 2),
(3, 3),
(4, 4),
(5, 5),
(5, 2);


-- ============================================================================
--  第 5 节  系统参数与数据字典
-- ============================================================================

INSERT INTO `sys_config` (`id`, `config_key`, `config_value`, `value_type`, `group_name`, `remark`) VALUES
(1,  'pos.auto_round',         '0',     'bool',    '收银', '是否自动抹零'),
(2,  'pos.round_mode',         'ROUND', 'string',  '收银', '抹零方式 ROUND四舍五入/FLOOR向下取整'),
(3,  'pos.allow_price_change', '0',     'bool',    '收银', '是否允许收银台改价'),
(4,  'pos.receipt_title',      '社区超市销售管理系统', 'string', '收银', '小票抬头'),
(5,  'inv.cost_method',        'MAVG',  'string',  '库存', '成本核算方法，一期固定为移动加权平均'),
(6,  'inv.near_expiry_days',   '30',    'int',     '库存', '临期预警天数'),
(7,  'inv.warn_ratio',         '0.30',  'decimal', '库存', '库存预警触发比例'),
(8,  'mem.point_rate',         '1.00',  'decimal', '会员', '每消费 1 元获得积分'),
(9,  'mem.point_deduct_rate',  '0.01',  'decimal', '会员', '每积分可抵扣金额'),
(10, 'mem.point_deduct_limit', '0.50',  'decimal', '会员', '积分抵扣上限占应收比例'),
(11, 'sal.promo_max_rate',     '0.80',  'decimal', '销售', '整单优惠上限比例'),
(12, 'sal.return_limit_days',  '7',     'int',     '销售', '退货期限（天）'),
(13, 'sys.session_timeout',    '120',   'int',     '系统', '会话超时（分钟）'),
(14, 'sys.login_fail_limit',   '5',     'int',     '系统', '登录失败锁定阈值'),
(15, 'sys.login_lock_minutes', '15',    'int',     '系统', '登录锁定时长（分钟）'),
(16, 'bi.min_support',         '0.01',  'decimal', '分析', '关联分析默认最小支持度'),
(17, 'bi.min_confidence',      '0.30',  'decimal', '分析', '关联分析默认最小置信度'),
(18, 'bi.min_lift',            '1.00',  'decimal', '分析', '关联分析默认最小提升度');

INSERT INTO `sys_dict` (`dict_type`, `item_key`, `item_value`, `sort`) VALUES
-- 支付方式
('pay_method', 'CASH',    '现金',     1),
('pay_method', 'WECHAT',  '微信支付', 2),
('pay_method', 'ALIPAY',  '支付宝',   3),
('pay_method', 'CARD',    '银行卡',   4),
('pay_method', 'BALANCE', '会员储值', 5),
('pay_method', 'POINTS',  '积分抵扣', 6),
('pay_method', 'OTHER',   '其他',     7),
-- 库存流水类型
('stock_flow_type', 'PURCHASE_IN',         '采购入库',     1),
('stock_flow_type', 'SALE_OUT',            '销售出库',     2),
('stock_flow_type', 'SALE_RETURN_IN',      '销售退货入库', 3),
('stock_flow_type', 'PURCHASE_RETURN_OUT', '采购退货出库', 4),
('stock_flow_type', 'CHECK_ADJUST',        '盘点调整',     5),
('stock_flow_type', 'LOSS_OUT',            '报损出库',     6),
('stock_flow_type', 'TRANSFER_IN',         '调拨入库',     7),
('stock_flow_type', 'TRANSFER_OUT',        '调拨出库',     8),
('stock_flow_type', 'INIT',                '期初库存',     9),
-- 报损类型
('loss_type', 'BREAKAGE',   '破损报废', 1),
('loss_type', 'EXPIRED',    '过期报废', 2),
('loss_type', 'FRESH_LOSS', '生鲜损耗', 3),
('loss_type', 'OTHER',      '其他',     4),
-- 盘点方式
('check_type', 'ALL',      '全盘',     1),
('check_type', 'PART',     '抽盘',     2),
('check_type', 'CATEGORY', '按分类盘', 3),
('check_type', 'SHELF',    '按货架盘', 4),
-- 会员来源
('member_source', 'STORE',  '到店办理', 1),
('member_source', 'REFER',  '老客推荐', 2),
('member_source', 'ONLINE', '线上注册', 3);

-- 备份记录（示例：每日 02:30 自动备份 + 一次手工备份）
-- 对应接口 GET /system/backups（注意接口输出的 size 对应本表 file_size）
INSERT INTO `sys_backup` (`file_name`, `file_path`, `file_size`, `trigger_type`, `backup_type`, `result`, `cost_ms`, `operator`, `created_at`) VALUES
('backup_20260913_023000.sql.gz', '/data/backup/', 18432000, 'AUTO',   'FULL', 1, 8420, 1, '2026-09-13 02:30:00'),
('backup_20260914_023000.sql.gz', '/data/backup/', 18874368, 'AUTO',   'FULL', 1, 8650, 1, '2026-09-14 02:30:00'),
('backup_20260915_023000.sql.gz', '/data/backup/', 19267584, 'AUTO',   'FULL', 1, 8210, 1, '2026-09-15 02:30:00'),
('backup_20260915_183000.sql.gz', '/data/backup/', 19398656, 'MANUAL', 'FULL', 1, 9120, 1, '2026-09-15 18:30:00');


-- ============================================================================
--  第 6 节  会员等级与会员标签
-- ============================================================================

INSERT INTO `mem_level` (`id`, `level_name`, `level_value`, `discount_rate`, `upgrade_condition`, `condition_value`, `benefit_desc`, `status`) VALUES
(1, '普通会员', 1, 1.0000, 'CONSUME',    0.00, '消费累计积分，积分可抵现',              1),
(2, '银卡会员', 2, 0.9800, 'CONSUME', 1000.00, '享 98 折，生日双倍积分',                1),
(3, '金卡会员', 3, 0.9500, 'CONSUME', 5000.00, '享 95 折，生日双倍积分，专属促销',      1);

INSERT INTO `mem_tag` (`id`, `tag_name`, `tag_type`, `tag_color`, `rule_desc`, `sort`, `status`) VALUES
(1, '高价值',     'AUTO',   '#d4380d', '累计消费 ≥ 5000 元',           1, 1),
(2, '生鲜偏好',   'AUTO',   '#389e0d', '生鲜类消费占比 ≥ 40%',         2, 1),
(3, '沉睡会员',   'AUTO',   '#8c8c8c', '距最近一次消费 > 60 天',       3, 1),
(4, '新客',       'AUTO',   '#1677ff', '注册时间 ≤ 30 天',             4, 1),
(5, '熟客',       'MANUAL', '#722ed1', '门店手工标记',                 5, 1);


-- ============================================================================
--  第 7 节  商品分类、SPU 与商品档案
-- ============================================================================

-- 三级分类：一级 5 个 / 二级 9 个 / 三级 4 个
INSERT INTO `prd_category` (`id`, `parent_id`, `category_code`, `category_name`, `level`, `sort`, `status`) VALUES
(1,  0, 'C01',      '生鲜果蔬',   1, 1, 1),
(2,  0, 'C02',      '粮油调味',   1, 2, 1),
(3,  0, 'C03',      '休闲食品',   1, 3, 1),
(4,  0, 'C04',      '酒水饮料',   1, 4, 1),
(5,  0, 'C05',      '日用百货',   1, 5, 1),

(11, 1, 'C0101',    '新鲜蔬菜',   2, 1, 1),
(12, 1, 'C0102',    '时令水果',   2, 2, 1),
(13, 1, 'C0103',    '肉禽蛋品',   2, 3, 1),
(21, 2, 'C0201',    '米面粮油',   2, 1, 1),
(22, 2, 'C0202',    '调味品',     2, 2, 1),
(31, 3, 'C0301',    '休闲零食',   2, 1, 1),
(41, 4, 'C0401',    '饮料',       2, 1, 1),
(42, 4, 'C0402',    '酒类',       2, 2, 1),
(51, 5, 'C0501',    '日用清洁',   2, 1, 1),

(111, 11, 'C010101', '叶菜类',    3, 1, 1),
(112, 11, 'C010102', '茄果类',    3, 2, 1),
(121, 12, 'C010201', '仁果类',    3, 1, 1),
(122, 12, 'C010202', '热带水果',  3, 2, 1);

-- SPU（同款多规格的聚合，示例）
INSERT INTO `prd_spu` (`id`, `spu_name`, `category_id`, `brand`, `description`, `status`) VALUES
(1, '可口可乐', 41, '可口可乐', '碳酸饮料，多规格',  1),
(2, '青岛啤酒', 42, '青岛啤酒', '经典瓶装啤酒',      1),
(3, '乐事薯片', 31, '乐事',     '膨化休闲食品',      1);

-- 商品档案（SKU）：unit_id 见第 2 节；is_weight=1 为称重商品
INSERT INTO `prd_product` (`id`, `product_code`, `product_name`, `category_id`, `spu_id`, `brand`, `spec`, `unit_id`, `purchase_price`, `sale_price`, `member_price`, `min_price`, `avg_cost`, `is_weight`, `is_perishable`, `shelf_life_days`, `is_allow_return`, `is_allow_discount`, `is_allow_point`, `shelf_id`, `status`) VALUES
(1,  'P000001', '有机小番茄',     111, NULL, NULL,     '500g',  6,  4.20, 6.80,  6.30,  5.50,  4.2000, 1, 1, 5,   1, 1, 1, 1, 1),
(2,  'P000002', '上海青',         111, NULL, NULL,     '散装',  6,  1.80, 3.20,  3.00,  2.60,  1.8000, 1, 1, 3,   1, 1, 1, 1, 1),
(3,  'P000003', '红富士苹果',     121, NULL, NULL,     '散装',  6,  3.60, 5.98,  5.50,  4.80,  3.6000, 1, 1, 15,  1, 1, 1, 1, 1),
(4,  'P000004', '香蕉',           122, NULL, NULL,     '散装',  6,  2.40, 4.20,  3.90,  3.20,  2.4000, 1, 1, 7,   1, 1, 1, 1, 1),
(5,  'P000005', '土鸡蛋',         13,  NULL, NULL,     '10 枚', 3,  9.80, 14.90, 13.90, 12.00, 9.8000, 0, 1, 30,  1, 1, 1, 1, 1),
(6,  'P000006', '猪后腿肉',       13,  NULL, NULL,     '散装',  6, 16.50, 23.80, 22.50, 20.00,16.5000, 1, 1, 3,   1, 1, 1, 1, 1),
(7,  'P000007', '东北珍珠米',     21,  NULL, NULL,     '5kg',   2, 28.00, 39.90, 37.90, 34.00,28.0000, 0, 0, 365, 1, 1, 1, 2, 1),
(8,  'P000008', '金龙鱼调和油',   21,  NULL, '金龙鱼', '1.8L',  4, 32.50, 45.90, 43.50, 40.00,32.5000, 0, 0, 540, 1, 1, 1, 2, 1),
(9,  'P000009', '海天生抽',       22,  NULL, '海天',   '500ml', 1,  6.50, 9.90,  9.50,  8.00,  6.5000, 0, 0, 540, 1, 1, 1, 2, 1),
(10, 'P000010', '太太乐鸡精',     22,  NULL, '太太乐', '200g',  2,  5.80, 8.50,  8.20,  7.00,  5.8000, 0, 0, 540, 1, 1, 1, 2, 1),
(11, 'P000011', '乐事薯片原味',   31,  3,    '乐事',   '70g',   2,  5.20, 8.50,  8.00,  6.80,  5.2000, 0, 0, 180, 1, 1, 1, 3, 1),
(12, 'P000012', '奥利奥饼干',     31,  NULL, '奥利奥', '116g',  2,  6.80, 11.50, 10.80, 9.50,  6.8000, 0, 0, 270, 1, 1, 1, 3, 1),
(13, 'P000013', '农夫山泉',       41,  NULL, '农夫山泉','550ml',1,  1.20, 2.00,  1.90,  1.60,  1.2000, 0, 0, 365, 1, 1, 1, 4, 1),
(14, 'P000014', '可口可乐',       41,  1,    '可口可乐','500ml',1,  2.40, 3.80,  3.60,  3.00,  2.4000, 0, 0, 270, 1, 1, 1, 4, 1),
(15, 'P000015', '雪碧',           41,  NULL, '雪碧',   '500ml', 1,  2.40, 3.80,  3.60,  3.00,  2.4000, 0, 0, 270, 1, 1, 1, 4, 1),
(16, 'P000016', '康师傅冰红茶',   41,  NULL, '康师傅', '500ml', 1,  2.60, 4.00,  3.80,  3.20,  2.6000, 0, 0, 365, 1, 1, 1, 4, 1),
(17, 'P000017', '青岛啤酒',       42,  2,    '青岛啤酒','500ml',1,  3.60, 5.50,  5.20,  4.50,  3.6000, 0, 0, 240, 1, 1, 1, 4, 1),
(18, 'P000018', '百威啤酒',       42,  NULL, '百威',   '500ml', 1,  5.20, 8.00,  7.60,  6.80,  5.2000, 0, 0, 240, 1, 1, 1, 4, 1),
(19, 'P000019', '黄飞红花生米',   31,  NULL, '黄飞红', '110g',  2,  5.20, 7.50,  7.20,  6.20,  5.2000, 0, 0, 180, 1, 1, 1, 3, 1),
(20, 'P000020', '心相印抽纸',     51,  NULL, '心相印', '3 层 120 抽', 5, 12.00, 18.90, 17.90, 16.00, 12.0000, 0, 0, NULL, 1, 1, 1, 5, 1);

-- 商品条码（一品多码：可口可乐额外挂一个整箱条码）
INSERT INTO `prd_barcode` (`product_id`, `barcode`, `barcode_type`, `status`) VALUES
(1,  '6900000000011', 1, 1),
(2,  '6900000000028', 1, 1),
(3,  '6900000000035', 1, 1),
(4,  '6900000000042', 1, 1),
(5,  '6900000000059', 1, 1),
(6,  '6900000000066', 1, 1),
(7,  '6900000000073', 1, 1),
(8,  '6900000000080', 1, 1),
(9,  '6900000000097', 1, 1),
(10, '6900000000103', 1, 1),
(11, '6900000000110', 1, 1),
(12, '6900000000127', 1, 1),
(13, '6900000000134', 1, 1),
(14, '6900000000141', 1, 1),
(14, '6900000000995', 2, 1),
(15, '6900000000158', 1, 1),
(16, '6900000000165', 1, 1),
(17, '6900000000172', 1, 1),
(18, '6900000000189', 1, 1),
(19, '6900000000196', 1, 1),
(20, '6900000000202', 1, 1);


-- ============================================================================
--  第 8 节  供应商与供货关系
-- ============================================================================

INSERT INTO `pur_supplier` (`id`, `supplier_code`, `supplier_name`, `contact_name`, `phone`, `address`, `settle_type`, `credit_days`, `balance_payable`, `status`, `remark`) VALUES
(1, 'S000001', '绿源生鲜配送',       '刘经理', '13911110001', '城郊农产品批发市场 A 区 12 号', 'CASH',   0,   0.00, 1, '生鲜蔬果主供'),
(2, 'S000002', '华联粮油批发',       '赵经理', '13911110002', '粮油批发市场 3 号库',           'PERIOD', 30, 12860.00, 1, '米面粮油，月结 30 天'),
(3, 'S000003', '旺旺食品经销',       '孙经理', '13911110003', '食品城 B 座 208',               'PERIOD', 15, 4320.50, 1, '休闲食品'),
(4, 'S000004', '汇通饮料贸易',       '周经理', '13911110004', '快消品物流园 6 号仓',           'PERIOD', 30, 8640.00, 1, '饮料与酒水'),
(5, 'S000005', '恒安日化用品',       '吴经理', '13911110005', '日化批发中心 C21',              'CASH',   0,   0.00, 1, '纸品与日化');

INSERT INTO `pur_supplier_product` (`supplier_id`, `product_id`, `supply_price`, `is_default`) VALUES
(1, 1,  4.20, 1), (1, 2,  1.80, 1), (1, 3,  3.60, 1), (1, 4,  2.40, 1),
(1, 5,  9.80, 1), (1, 6, 16.50, 1),
(2, 7, 28.00, 1), (2, 8, 32.50, 1), (2, 9,  6.50, 1), (2, 10, 5.80, 1),
(3, 11, 5.20, 1), (3, 12, 6.80, 1), (3, 19, 5.20, 1),
(4, 13, 1.20, 1), (4, 14, 2.40, 1), (4, 15, 2.40, 1), (4, 16, 2.60, 1),
(4, 17, 3.60, 1), (4, 18, 5.20, 1),
(5, 20, 12.00, 1);


-- ============================================================================
--  第 9 节  期初库存与库存流水
--  库存采用「流水驱动」：inv_stock 是快照，inv_stock_flow 是唯一真相源。
--  期初建账时同时写入两条记录，保证「快照 = 流水累计」。
-- ============================================================================

INSERT INTO `inv_stock` (`store_id`, `product_id`, `quantity`, `safe_qty`, `max_qty`, `avg_cost`) VALUES
(1, 1,   42.500, 20.000, 200.000,  4.2000),
(1, 2,   18.200, 20.000, 200.000,  1.8000),
(1, 3,   56.800, 15.000, 150.000,  3.6000),
(1, 4,    8.600, 10.000, 100.000,  2.4000),
(1, 5,   36.000, 15.000, 120.000,  9.8000),
(1, 6,   12.400, 10.000,  80.000, 16.5000),
(1, 7,   46.000, 10.000, 100.000, 28.0000),
(1, 8,   28.000, 10.000, 100.000, 32.5000),
(1, 9,   64.000, 15.000, 150.000,  6.5000),
(1, 10,  52.000, 15.000, 150.000,  5.8000),
(1, 11,  88.000, 20.000, 200.000,  5.2000),
(1, 12,  46.000, 20.000, 200.000,  6.8000),
(1, 13, 240.000, 48.000, 480.000,  1.2000),
(1, 14, 168.000, 36.000, 360.000,  2.4000),
(1, 15, 144.000, 36.000, 360.000,  2.4000),
(1, 16,  96.000, 24.000, 240.000,  2.6000),
(1, 17,  86.000, 24.000, 240.000,  3.6000),
(1, 18,  42.000, 24.000, 240.000,  5.2000),
(1, 19,  76.000, 20.000, 200.000,  5.2000),
(1, 20,  34.000, 10.000, 100.000, 12.0000);

-- 期初库存流水（flow_type = INIT，direction = 1）
INSERT INTO `inv_stock_flow` (`store_id`, `product_id`, `flow_type`, `direction`, `quantity`, `before_qty`, `after_qty`, `unit_cost`, `amount`, `source_type`, `source_no`, `operator`, `remark`) VALUES
(1, 1,  'INIT', 1,  42.500, 0.000,  42.500,  4.2000,  178.50, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 2,  'INIT', 1,  18.200, 0.000,  18.200,  1.8000,   32.76, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 3,  'INIT', 1,  56.800, 0.000,  56.800,  3.6000,  204.48, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 4,  'INIT', 1,   8.600, 0.000,   8.600,  2.4000,   20.64, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 5,  'INIT', 1,  36.000, 0.000,  36.000,  9.8000,  352.80, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 6,  'INIT', 1,  12.400, 0.000,  12.400, 16.5000,  204.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 7,  'INIT', 1,  46.000, 0.000,  46.000, 28.0000, 1288.00, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 8,  'INIT', 1,  28.000, 0.000,  28.000, 32.5000,  910.00, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 9,  'INIT', 1,  64.000, 0.000,  64.000,  6.5000,  416.00, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 10, 'INIT', 1,  52.000, 0.000,  52.000,  5.8000,  301.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 11, 'INIT', 1,  88.000, 0.000,  88.000,  5.2000,  457.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 12, 'INIT', 1,  46.000, 0.000,  46.000,  6.8000,  312.80, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 13, 'INIT', 1, 240.000, 0.000, 240.000,  1.2000,  288.00, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 14, 'INIT', 1, 168.000, 0.000, 168.000,  2.4000,  403.20, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 15, 'INIT', 1, 144.000, 0.000, 144.000,  2.4000,  345.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 16, 'INIT', 1,  96.000, 0.000,  96.000,  2.6000,  249.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 17, 'INIT', 1,  86.000, 0.000,  86.000,  3.6000,  309.60, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 18, 'INIT', 1,  42.000, 0.000,  42.000,  5.2000,  218.40, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 19, 'INIT', 1,  76.000, 0.000,  76.000,  5.2000,  395.20, 'INIT', 'QC20260915001', 1, '期初建账'),
(1, 20, 'INIT', 1,  34.000, 0.000,  34.000, 12.0000,  408.00, 'INIT', 'QC20260915001', 1, '期初建账');

-- 保质期商品批次（示例，用于临期预警演示）
INSERT INTO `inv_batch` (`batch_no`, `product_id`, `store_id`, `production_date`, `expiry_date`, `init_qty`, `remain_qty`, `unit_cost`, `status`) VALUES
('P000001-20260910-01', 1, 1, '2026-09-10', '2026-09-15', 30.000,  12.500,  4.2000, 1),
('P000003-20260901-01', 3, 1, '2026-09-01', '2026-09-16', 40.000,  26.800,  3.6000, 1),
('P000005-20260901-01', 5, 1, '2026-09-01', '2026-10-01', 24.000,  16.000,  9.8000, 1),
('P000006-20260913-01', 6, 1, '2026-09-13', '2026-09-16', 10.000,   6.400, 16.5000, 1);


-- ============================================================================
--  第 10 节  会员档案
-- ============================================================================

INSERT INTO `mem_member` (`id`, `member_no`, `phone`, `real_name`, `gender`, `birthday`, `level_id`, `balance`, `gift_balance`, `points`, `total_consume`, `consume_count`, `last_consume_at`, `source`, `status`, `remark`) VALUES
(1, 'M202501010001', '13812348866', '张秀兰', 2, '1985-03-12', 3, 386.50, 30.00, 1286, 8642.30, 96, '2026-09-13 18:20:00', 'STORE',  1, '金卡会员，生鲜常客'),
(2, 'M202501010002', '13901237788', '李建国', 1, '1978-11-05', 2, 128.00,  0.00,  542, 3218.60, 47, '2026-09-14 09:35:00', 'STORE',  1, '银卡会员'),
(3, 'M202501010003', '13712349900', '王芳',   2, '1992-07-21', 2,   0.00,  0.00,  318, 2145.80, 33, '2026-09-10 20:10:00', 'REFER',  1, NULL),
(4, 'M202502150004', '13611112233', '刘德胜', 1, '1968-01-30', 1,  50.00, 10.00,   96,  486.20, 12, '2026-08-28 17:45:00', 'STORE',  1, '沉睡会员'),
(5, 'M202503200005', '13522223344', '陈美玲', 2, '1995-09-08', 1, 200.00, 20.00,  412, 1268.40, 25, '2026-09-15 08:12:00', 'ONLINE', 1, '线上注册');

-- 会员标签
INSERT INTO `mem_member_tag` (`member_id`, `tag_id`) VALUES
(1, 1), (1, 2),
(2, 2),
(4, 3),
(5, 4);


-- ============================================================================
--  第 11 节  促销活动
--  优先级 priority 数值小者先算：SPECIAL 1 → DISCOUNT 2 → COMBO 3 →
--  FULL_REDUCE 4 → FULL_GIFT 5 → COUPON 6 → MEMBER 7
-- ============================================================================

INSERT INTO `pro_promotion` (`id`, `promo_name`, `promo_type`, `priority`, `start_date`, `end_date`, `week_days`, `start_time`, `end_time`, `is_member_only`, `is_stackable`, `trigger_count`, `discount_total`, `status`, `created_by`, `remark`) VALUES
(1, '可乐特价',           'SPECIAL',     1, '2026-09-01', '2026-12-31', NULL,   '00:00:00', '23:59:59', 0, 0,  1240, 1860.00, 'RUNNING', 1, '可口可乐 500ml 特价 3.20 元'),
(2, '每周三生鲜日',       'DISCOUNT',    2, '2026-08-01', '2026-12-31', '3',    '08:00:00', '21:00:00', 0, 1,   486, 3248.60, 'RUNNING', 1, '生鲜果蔬类 8 折，每周三'),
(3, '啤酒+花生米组合装',  'COMBO',       3, '2026-09-10', '2026-12-31', NULL,   '17:00:00', '22:00:00', 0, 0,   168,  672.00, 'RUNNING', 1, '源自关联规则分析建议'),
(4, '满 50 减 5',         'FULL_REDUCE', 4, '2026-09-01', '2026-12-31', NULL,   '00:00:00', '23:59:59', 0, 1,   312, 1560.00, 'RUNNING', 1, '全场满 50 元减 5 元'),
(5, '新客 5 元券',        'COUPON',      6, '2026-09-01', '2026-12-31', NULL,   '00:00:00', '23:59:59', 1, 0,    96,  480.00, 'RUNNING', 1, '新注册会员自动发放，满 30 可用'),
(6, '金卡会员专享 95 折', 'MEMBER',      7, '2026-01-01', '2026-12-31', NULL,   '00:00:00', '23:59:59', 1, 1,  2680, 8642.30, 'RUNNING', 1, '金卡会员整单 95 折'),
(7, '中秋团圆礼盒',       'COMBO',       3, '2026-09-20', '2026-09-30', NULL,   '00:00:00', '23:59:59', 0, 0,     0,    0.00, 'DRAFT',   1, '中秋限定礼盒装，未上线'),
(8, '夏日饮品折扣季',     'DISCOUNT',    2, '2026-06-01', '2026-08-31', NULL,   '10:00:00', '20:00:00', 0, 1,  2130, 6390.00, 'EXPIRED', 1, '饮料类 9 折，活动已结束');

INSERT INTO `pro_promotion_item` (`promo_id`, `item_type`, `target_id`, `promo_price`, `discount_rate`, `threshold_amount`, `reduce_amount`, `gift_product_id`, `gift_qty`, `combo_qty`) VALUES
-- 可乐特价：可口可乐 3.20
(1, 'PRODUCT', 14, 3.20, NULL,   NULL, NULL, NULL, NULL, NULL),
-- 每周三生鲜日：生鲜类 8 折（按分类）
(2, 'CATEGORY', 11, NULL, 0.8000, NULL, NULL, NULL, NULL, NULL),
(2, 'CATEGORY', 12, NULL, 0.8000, NULL, NULL, NULL, NULL, NULL),
(2, 'CATEGORY', 13, NULL, 0.8000, NULL, NULL, NULL, NULL, NULL),
-- 啤酒+花生米组合装：青岛啤酒 + 黄飞红花生米 组合价 11.50
(3, 'PRODUCT', 17, 11.50, NULL, NULL, NULL, NULL, NULL, 1.000),
(3, 'PRODUCT', 19, 11.50, NULL, NULL, NULL, NULL, NULL, 1.000),
-- 满 50 减 5
(4, 'PRODUCT', 0, NULL, NULL, 50.00, 5.00, NULL, NULL, NULL),
-- 新客券：满 30 减 5
(5, 'PRODUCT', 0, NULL, NULL, 30.00, 5.00, NULL, NULL, NULL),
-- 金卡会员 95 折
(6, 'PRODUCT', 0, NULL, 0.9500, NULL, NULL, NULL, NULL, NULL),
-- 中秋礼盒（示例，未上线）
(7, 'PRODUCT', 1,  88.00, NULL, NULL, NULL, NULL, NULL, 1.000),
(7, 'PRODUCT', 3,  88.00, NULL, NULL, NULL, NULL, NULL, 1.000),
(7, 'PRODUCT', 6,  88.00, NULL, NULL, NULL, NULL, NULL, 1.000),
(7, 'PRODUCT', 7,  88.00, NULL, NULL, NULL, NULL, NULL, 1.000),
-- 夏日饮品（已过期）
(8, 'CATEGORY', 41, NULL, 0.9000, NULL, NULL, NULL, NULL, NULL);

-- 优惠券模板
INSERT INTO `pro_coupon` (`coupon_name`, `coupon_type`, `face_value`, `discount_rate`, `threshold`, `total_qty`, `issued_qty`, `used_qty`, `valid_from`, `valid_to`, `status`) VALUES
('新客 5 元券',    'CASH',     5.00,  NULL,   30.00, 500,  96,  62, '2026-09-01', '2026-12-31', 1),
('满 100 减 10',   'CASH',    10.00,  NULL,  100.00, 300, 120,  78, '2026-09-01', '2026-10-31', 1),
('生鲜 9 折券',    'DISCOUNT', 0.00, 0.9000, 20.00, 200,  54,  31, '2026-09-01', '2026-10-31', 1),
('中秋礼品券',     'GIFT',     0.00,  NULL,   88.00, 100,   0,   0, '2026-09-20', '2026-09-30', 0);


SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================================
--  初始化完成
--  ----------------------------------------------------------------------------
--  演示账号（密码统一 123456）
--    admin      系统管理员  全部权限
--    manager01  店长        除系统设置外全部
--    cashier01  收银员      收银台 / 交班 / 会员查看
--    stocker01  库管员      商品查看 / 采购收货 / 库存全套
--    buyer01    采购员兼店长 采购全套 / 商品查看 / 采购报表
--  ----------------------------------------------------------------------------
--  数据规模
--    门店 2 · 收银台 3 · 货架 6 · 计量单位 10
--    角色 5 · 权限 32（3 目录 + 29 功能）· 用户 5
--    系统参数 18 · 字典 27 · 备份记录 4
--    商品分类 18（三级）· SPU 3 · 商品 20 · 条码 21
--    供应商 5 · 供货关系 20
--    库存 20 条 · 期初流水 20 条 · 批次 4
--    会员等级 3 · 会员标签 5 · 会员 5
--    促销 8 · 促销明细 14 · 优惠券 4
-- ============================================================================
