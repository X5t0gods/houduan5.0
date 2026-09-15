-- ============================================================================
--  中小型社区超市销售管理系统  ·  数据库建表脚本
--  Community Supermarket Sales Management System  (CSSMS)
-- ----------------------------------------------------------------------------
--  数据库    :  MySQL 8.0
--  字符集    :  utf8mb4 / utf8mb4_general_ci
--  存储引擎  :  InnoDB
--  脚本版本  :  V1.0
--  编制日期  :  2026-09-15
--  表数量    :  62 张，按 8 个数据域组织
--  外键数量  :  86 条（本次变更不新增外键）
--  唯一键数量:  42 个（含本次新增的 mem_balance_flow.uk_request_id）
-- ----------------------------------------------------------------------------
--  设计依据
--    1.《详细功能需求文档》第 3、4 章
--    2.《后端接口文档》—— 128 个接口的字段诉求
--    3.《数据设计方案》—— 十条关键设计决策
-- ----------------------------------------------------------------------------
--  设计约定
--  1. 主键统一为 BIGINT UNSIGNED AUTO_INCREMENT，字段名 id
--  2. 金额字段统一 DECIMAL(12,2)，数量字段统一 DECIMAL(12,3)
--  3. 业务表统一带 created_at；可变更的表带 updated_at
--  4. 业务数据逻辑删除：以 status 标记（1 启用 / 0 停用），不做物理删除
--  5. 外键约束统一在脚本末尾集中创建，便于调整建表顺序
--  6. 流水类表只增不改（库存流水、资金流水、操作日志）
--  7. 交易明细冗余商品名与成本价快照，保证历史单据不被后续变更污染
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `community_supermarket`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_general_ci;

USE `community_supermarket`;


-- ============================================================================
--  第一节  基础数据域（base_）
-- ============================================================================

DROP TABLE IF EXISTS `base_store`;
CREATE TABLE `base_store` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '门店ID',
  `store_code`     VARCHAR(20)     NOT NULL                COMMENT '门店编码',
  `store_name`     VARCHAR(64)     NOT NULL                COMMENT '门店名称',
  `address`        VARCHAR(200)    DEFAULT NULL            COMMENT '门店地址',
  `phone`          VARCHAR(20)     DEFAULT NULL            COMMENT '联系电话',
  `business_hours` VARCHAR(64)     DEFAULT NULL            COMMENT '营业时间，如 07:00-22:00',
  `manager_id`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '店长用户ID（不设外键，避免与 sys_user 循环依赖）',
  `status`         TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1营业 0停业',
  `remark`         VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_store_code` (`store_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='门店信息';

DROP TABLE IF EXISTS `base_pos`;
CREATE TABLE `base_pos` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '收银台ID',
  `store_id`   BIGINT UNSIGNED NOT NULL                COMMENT '所属门店ID',
  `pos_code`   VARCHAR(20)     NOT NULL                COMMENT '收银台编码',
  `pos_name`   VARCHAR(64)     NOT NULL                COMMENT '收银台名称',
  `status`     TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_store_pos` (`store_id`, `pos_code`),
  KEY `idx_store` (`store_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收银台';

DROP TABLE IF EXISTS `base_unit`;
CREATE TABLE `base_unit` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '单位ID',
  `unit_name`      VARCHAR(20)     NOT NULL                COMMENT '单位名称，如 瓶/箱/斤',
  `unit_type`      TINYINT         NOT NULL DEFAULT 1      COMMENT '单位类型 1基本单位 2辅助单位',
  `base_unit_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属基本单位ID',
  `conversion_rate` DECIMAL(12,4)  NOT NULL DEFAULT 1.0000 COMMENT '换算系数，1辅助单位=系数×基本单位',
  `sort`           INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`         TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_unit_name` (`unit_name`),
  KEY `idx_base_unit` (`base_unit_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计量单位';

DROP TABLE IF EXISTS `base_shelf`;
CREATE TABLE `base_shelf` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '货架ID',
  `store_id`   BIGINT UNSIGNED NOT NULL                COMMENT '所属门店ID',
  `shelf_code` VARCHAR(20)     NOT NULL                COMMENT '货架编码',
  `shelf_name` VARCHAR(64)     NOT NULL                COMMENT '货架名称',
  `area`       VARCHAR(32)     DEFAULT NULL            COMMENT '所属区域，如 生鲜区/日化区',
  `remark`     VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `status`     TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_store_shelf` (`store_id`, `shelf_code`),
  KEY `idx_store` (`store_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='货架/库位';


-- ============================================================================
--  第二节  权限与用户域（sys_）
-- ============================================================================

DROP TABLE IF EXISTS `sys_role`;
CREATE TABLE `sys_role` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '角色ID',
  `role_code`   VARCHAR(32)     NOT NULL                COMMENT '角色编码，如 ADMIN/MANAGER/CASHIER',
  `role_name`   VARCHAR(32)     NOT NULL                COMMENT '角色名称',
  `data_scope`  VARCHAR(16)     NOT NULL DEFAULT 'SELF' COMMENT '数据范围 ALL/STORE/DEPT/SELF',
  `description` VARCHAR(255)    DEFAULT NULL            COMMENT '角色说明',
  `sort`        INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`      TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_code` (`role_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色';

DROP TABLE IF EXISTS `sys_permission`;
CREATE TABLE `sys_permission` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '权限ID',
  `parent_id`  BIGINT UNSIGNED NOT NULL DEFAULT 0      COMMENT '父级权限ID，0为顶级',
  `perm_code`  VARCHAR(64)     NOT NULL                COMMENT '权限编码，如 goods:product:create',
  `perm_name`  VARCHAR(64)     NOT NULL                COMMENT '权限名称',
  `perm_type`  TINYINT         NOT NULL DEFAULT 1      COMMENT '类型 1目录 2菜单（页面级权限） 3按钮（操作级权限）',
  `path`       VARCHAR(128)    DEFAULT NULL            COMMENT '前端路由或接口路径',
  `icon`       VARCHAR(64)     DEFAULT NULL            COMMENT '菜单图标',
  `sort`       INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`     TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_perm_code` (`perm_code`),
  KEY `idx_parent` (`parent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限/菜单';

DROP TABLE IF EXISTS `sys_user`;
CREATE TABLE `sys_user` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户ID',
  `username`         VARCHAR(32)     NOT NULL                COMMENT '登录账号',
  `password_hash`    VARCHAR(128)    NOT NULL                COMMENT '密码哈希（bcrypt）',
  `real_name`        VARCHAR(32)     NOT NULL                COMMENT '真实姓名',
  `phone`            VARCHAR(20)     DEFAULT NULL            COMMENT '手机号',
  `avatar`           VARCHAR(255)    DEFAULT NULL            COMMENT '头像地址',
  `store_id`         BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属门店ID',
  `status`           TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `last_login_at`    DATETIME        DEFAULT NULL            COMMENT '最后登录时间',
  `last_login_ip`    VARCHAR(45)     DEFAULT NULL            COMMENT '最后登录IP',
  `login_fail_count` INT             NOT NULL DEFAULT 0      COMMENT '连续登录失败次数',
  `locked_until`     DATETIME        DEFAULT NULL            COMMENT '锁定截止时间',
  `remark`           VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`),
  KEY `idx_store` (`store_id`),
  KEY `idx_phone` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统用户';

DROP TABLE IF EXISTS `sys_user_role`;
CREATE TABLE `sys_user_role` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id`    BIGINT UNSIGNED NOT NULL                COMMENT '用户ID',
  `role_id`    BIGINT UNSIGNED NOT NULL                COMMENT '角色ID',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_role` (`user_id`, `role_id`),
  KEY `idx_role` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-角色关联';

DROP TABLE IF EXISTS `sys_role_permission`;
CREATE TABLE `sys_role_permission` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `role_id`       BIGINT UNSIGNED NOT NULL                COMMENT '角色ID',
  `permission_id` BIGINT UNSIGNED NOT NULL                COMMENT '权限ID',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_perm` (`role_id`, `permission_id`),
  KEY `idx_perm` (`permission_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色-权限关联';

DROP TABLE IF EXISTS `sys_operation_log`;
CREATE TABLE `sys_operation_log` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志ID',
  `user_id`      BIGINT UNSIGNED DEFAULT NULL            COMMENT '操作人ID',
  `user_name`    VARCHAR(32)     DEFAULT NULL            COMMENT '操作人姓名（冗余）',
  `module`       VARCHAR(32)     NOT NULL                COMMENT '业务模块，如 商品/销售',
  `action`       VARCHAR(32)     NOT NULL                COMMENT '操作类型，如 新增/修改/删除/审核',
  `target_type`  VARCHAR(32)     DEFAULT NULL            COMMENT '目标对象类型',
  `target_id`    BIGINT UNSIGNED DEFAULT NULL            COMMENT '目标对象ID',
  `before_value` JSON            DEFAULT NULL            COMMENT '操作前数据快照',
  `after_value`  JSON            DEFAULT NULL            COMMENT '操作后数据快照',
  `ip`           VARCHAR(45)     DEFAULT NULL            COMMENT '来源IP',
  `user_agent`   VARCHAR(255)    DEFAULT NULL            COMMENT '客户端标识',
  `cost_ms`      INT             DEFAULT NULL            COMMENT '耗时（毫秒）',
  `result`       TINYINT         NOT NULL DEFAULT 1      COMMENT '结果 1成功 0失败',
  `error_msg`    VARCHAR(500)    DEFAULT NULL            COMMENT '失败原因',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
  PRIMARY KEY (`id`),
  KEY `idx_user_time` (`user_id`, `created_at`),
  KEY `idx_target` (`target_type`, `target_id`),
  KEY `idx_module_time` (`module`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作日志（只增不改）';

DROP TABLE IF EXISTS `sys_login_log`;
CREATE TABLE `sys_login_log` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志ID',
  `username`    VARCHAR(32)     NOT NULL                COMMENT '登录账号',
  `user_id`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '用户ID（登录成功时）',
  `ip`          VARCHAR(45)     DEFAULT NULL            COMMENT '来源IP',
  `user_agent`  VARCHAR(255)    DEFAULT NULL            COMMENT '客户端标识',
  `result`      TINYINT         NOT NULL                COMMENT '结果 1成功 0失败',
  `fail_reason` VARCHAR(128)    DEFAULT NULL            COMMENT '失败原因',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '登录时间',
  PRIMARY KEY (`id`),
  KEY `idx_username_time` (`username`, `created_at`),
  KEY `idx_time` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录日志';

DROP TABLE IF EXISTS `sys_config`;
CREATE TABLE `sys_config` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `config_key`   VARCHAR(64)     NOT NULL                COMMENT '参数编码，如 pos.auto_round',
  `config_value` VARCHAR(255)    NOT NULL                COMMENT '参数值',
  `value_type`   VARCHAR(16)     NOT NULL DEFAULT 'string' COMMENT '值类型 string/int/decimal/bool/json',
  `group_name`   VARCHAR(32)     DEFAULT NULL            COMMENT '参数分组，如 收银/库存/会员',
  `remark`       VARCHAR(255)    DEFAULT NULL            COMMENT '参数说明',
  `updated_by`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '最后修改人',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_config_key` (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统参数配置';

DROP TABLE IF EXISTS `sys_dict`;
CREATE TABLE `sys_dict` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '字典ID',
  `dict_type`  VARCHAR(64)     NOT NULL                COMMENT '字典类型，如 pay_method',
  `item_key`   VARCHAR(64)     NOT NULL                COMMENT '字典项键',
  `item_value` VARCHAR(128)    NOT NULL                COMMENT '字典项值',
  `sort`       INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`     TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `remark`     VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_dict_item` (`dict_type`, `item_key`),
  KEY `idx_type` (`dict_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据字典';

-- ----------------------------------------------------------------------------
-- 图形验证码：方案约定不引入 Redis 等外部依赖（见《数据设计方案》1.3 设计约束），
-- 因此验证码落在数据库表中，由定时任务清理过期记录。
-- 对应接口：GET /auth/captcha 写入，POST /auth/login 校验后置 used=1
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sys_captcha`;
CREATE TABLE `sys_captcha` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `captcha_key`  VARCHAR(64)     NOT NULL                COMMENT '验证码标识（UUID，前端原样回传）',
  `captcha_code` VARCHAR(8)      NOT NULL                COMMENT '验证码内容',
  `ip`           VARCHAR(45)     DEFAULT NULL            COMMENT '请求来源IP（防刷）',
  `used`         TINYINT         NOT NULL DEFAULT 0      COMMENT '是否已使用 1是 0否（用过即失效）',
  `expire_at`    DATETIME        NOT NULL                COMMENT '过期时间（建议签发后 2 分钟）',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_captcha_key` (`captcha_key`),
  KEY `idx_expire` (`expire_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='图形验证码（无 Redis 部署的替代存储）';

-- ----------------------------------------------------------------------------
-- 数据库备份记录
-- 对应接口：GET /system/backups（列表）、POST /system/backups（立即备份）、
--           POST /system/backups/restore（恢复）
-- 注：接口返回的 size 字段对应本表的 file_size（后端输出时改名）
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sys_backup`;
CREATE TABLE `sys_backup` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '备份记录ID',
  `file_name`    VARCHAR(128)    NOT NULL                COMMENT '备份文件名，如 backup_20260915_023000.sql.gz',
  `file_path`    VARCHAR(255)    DEFAULT NULL            COMMENT '备份文件存放路径',
  `file_size`    BIGINT UNSIGNED NOT NULL DEFAULT 0      COMMENT '文件大小（字节，接口输出为 size）',
  `trigger_type` VARCHAR(16)     NOT NULL DEFAULT 'MANUAL' COMMENT '触发方式 MANUAL手动/AUTO自动',
  `backup_type`  VARCHAR(16)     NOT NULL DEFAULT 'FULL' COMMENT '备份类型 FULL全量/INCR增量',
  `result`       TINYINT         NOT NULL DEFAULT 1      COMMENT '结果 1成功 0失败',
  `error_msg`    VARCHAR(500)    DEFAULT NULL            COMMENT '失败原因',
  `cost_ms`      INT             DEFAULT NULL            COMMENT '备份耗时（毫秒）',
  `restored_at`  DATETIME        DEFAULT NULL            COMMENT '最近一次被用于恢复的时间',
  `operator`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '操作人ID',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '备份时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_file_name` (`file_name`),
  KEY `idx_time` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据库备份记录';

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


-- ============================================================================
--  第三节  商品域（prd_）
-- ============================================================================

DROP TABLE IF EXISTS `prd_category`;
CREATE TABLE `prd_category` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '分类ID',
  `parent_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0      COMMENT '父分类ID，0为顶级',
  `category_code` VARCHAR(20)     NOT NULL                COMMENT '分类编码',
  `category_name` VARCHAR(64)     NOT NULL                COMMENT '分类名称',
  `level`         TINYINT         NOT NULL DEFAULT 1      COMMENT '层级 1/2/3',
  `sort`          INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`        TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_category_code` (`category_code`),
  KEY `idx_parent` (`parent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品分类（最多三级）';

DROP TABLE IF EXISTS `prd_spu`;
CREATE TABLE `prd_spu` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'SPU ID',
  `spu_name`    VARCHAR(64)     NOT NULL                COMMENT '标准商品名称',
  `category_id` BIGINT UNSIGNED NOT NULL                COMMENT '所属分类ID',
  `brand`       VARCHAR(32)     DEFAULT NULL            COMMENT '品牌',
  `description` VARCHAR(255)    DEFAULT NULL            COMMENT '商品描述',
  `status`      TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_category` (`category_id`),
  KEY `idx_name` (`spu_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品标准单元（SPU）';

DROP TABLE IF EXISTS `prd_product`;
CREATE TABLE `prd_product` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品ID',
  `product_code`       VARCHAR(20)     NOT NULL                COMMENT '商品编码，P+6位流水',
  `product_name`       VARCHAR(64)     NOT NULL                COMMENT '商品名称',
  `category_id`        BIGINT UNSIGNED NOT NULL                COMMENT '所属末级分类ID',
  `spu_id`             BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属SPU ID',
  `brand`              VARCHAR(32)     DEFAULT NULL            COMMENT '品牌',
  `spec`               VARCHAR(32)     DEFAULT NULL            COMMENT '规格，如 330ml',
  `unit_id`            BIGINT UNSIGNED NOT NULL                COMMENT '基本单位ID',
  `purchase_unit_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '采购单位ID',
  `purchase_price`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '参考进价',
  `sale_price`         DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '零售价',
  `member_price`       DECIMAL(12,2)   DEFAULT NULL            COMMENT '会员价',
  `min_price`          DECIMAL(12,2)   DEFAULT NULL            COMMENT '最低售价（改价下限）',
  `avg_cost`           DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '移动加权平均成本',
  `is_weight`          TINYINT         NOT NULL DEFAULT 0      COMMENT '是否称重商品 1是 0否',
  `is_perishable`      TINYINT         NOT NULL DEFAULT 0      COMMENT '是否保质期商品 1是 0否',
  `shelf_life_days`    INT             DEFAULT NULL            COMMENT '保质期天数',
  `is_allow_return`    TINYINT         NOT NULL DEFAULT 1      COMMENT '是否允许退货 1是 0否',
  `is_allow_discount`  TINYINT         NOT NULL DEFAULT 1      COMMENT '是否参与促销 1是 0否',
  `is_allow_point`     TINYINT         NOT NULL DEFAULT 1      COMMENT '是否参与积分 1是 0否',
  `image_url`          VARCHAR(255)    DEFAULT NULL            COMMENT '商品图片地址',
  `shelf_id`           BIGINT UNSIGNED DEFAULT NULL            COMMENT '默认陈列货架ID',
  `status`             TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1在售 0停用（有业务流水时只可停用）',
  `remark`             VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_product_code` (`product_code`),
  KEY `idx_category` (`category_id`),
  KEY `idx_spu` (`spu_id`),
  KEY `idx_name` (`product_name`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品档案（SKU）';

DROP TABLE IF EXISTS `prd_barcode`;
CREATE TABLE `prd_barcode` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id`   BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `barcode`      VARCHAR(32)     NOT NULL                COMMENT '条码',
  `barcode_type` TINYINT         NOT NULL DEFAULT 1      COMMENT '类型 1主条码 2附加条码',
  `status`       TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_barcode` (`barcode`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品条码（一商品多码）';

DROP TABLE IF EXISTS `prd_price_history`;
CREATE TABLE `prd_price_history` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id`         BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `old_purchase_price` DECIMAL(12,2)   DEFAULT NULL            COMMENT '原进价',
  `new_purchase_price` DECIMAL(12,2)   DEFAULT NULL            COMMENT '新进价',
  `old_sale_price`     DECIMAL(12,2)   DEFAULT NULL            COMMENT '原售价',
  `new_sale_price`     DECIMAL(12,2)   DEFAULT NULL            COMMENT '新售价',
  `change_type`        VARCHAR(16)     NOT NULL                COMMENT '变更类型 进价/售价/会员价',
  `changed_by`         BIGINT UNSIGNED DEFAULT NULL            COMMENT '变更人ID',
  `reason`             VARCHAR(255)    DEFAULT NULL            COMMENT '变更原因',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '变更时间',
  PRIMARY KEY (`id`),
  KEY `idx_product_time` (`product_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品价格变更历史';

DROP TABLE IF EXISTS `prd_tag`;
CREATE TABLE `prd_tag` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '标签ID',
  `tag_name`   VARCHAR(32)     NOT NULL                COMMENT '标签名称，如 新品/爆款/季节性',
  `tag_color`  VARCHAR(16)     DEFAULT NULL            COMMENT '标签颜色',
  `sort`       INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`     TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tag_name` (`tag_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品标签';

DROP TABLE IF EXISTS `prd_product_tag`;
CREATE TABLE `prd_product_tag` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `tag_id`     BIGINT UNSIGNED NOT NULL                COMMENT '标签ID',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_product_tag` (`product_id`, `tag_id`),
  KEY `idx_tag` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品-标签关联';


-- ============================================================================
--  第四节  供应商与采购域（pur_）
-- ============================================================================

DROP TABLE IF EXISTS `pur_supplier`;
CREATE TABLE `pur_supplier` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '供应商ID',
  `supplier_code`   VARCHAR(20)     NOT NULL                COMMENT '供应商编码，S+6位流水',
  `supplier_name`   VARCHAR(64)     NOT NULL                COMMENT '供应商名称',
  `contact_name`    VARCHAR(32)     DEFAULT NULL            COMMENT '联系人',
  `phone`           VARCHAR(20)     DEFAULT NULL            COMMENT '联系电话',
  `address`         VARCHAR(200)    DEFAULT NULL            COMMENT '地址',
  `settle_type`     VARCHAR(16)     DEFAULT 'CASH'          COMMENT '结算方式 CASH现结/PERIOD账期',
  `credit_days`     INT             NOT NULL DEFAULT 0      COMMENT '账期天数',
  `balance_payable` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '应付余额',
  `remark`          VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `status`          TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1合作中 0停用',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_supplier_code` (`supplier_code`),
  KEY `idx_name` (`supplier_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商档案';

DROP TABLE IF EXISTS `pur_supplier_product`;
CREATE TABLE `pur_supplier_product` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `supplier_id`      BIGINT UNSIGNED NOT NULL                COMMENT '供应商ID',
  `product_id`       BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `supply_price`     DECIMAL(12,2)   DEFAULT NULL            COMMENT '供货价',
  `is_default`       TINYINT         NOT NULL DEFAULT 0      COMMENT '是否默认供应商 1是 0否',
  `last_purchase_at` DATETIME        DEFAULT NULL            COMMENT '最近采购时间',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_supplier_product` (`supplier_id`, `product_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商-商品供货关系';

DROP TABLE IF EXISTS `pur_order`;
CREATE TABLE `pur_order` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '采购订单ID',
  `order_no`      VARCHAR(32)     NOT NULL                COMMENT '采购单号 CG+日期+流水',
  `supplier_id`   BIGINT UNSIGNED NOT NULL                COMMENT '供应商ID',
  `store_id`      BIGINT UNSIGNED NOT NULL                COMMENT '收货门店ID',
  `order_date`    DATE            NOT NULL                COMMENT '下单日期',
  `expect_date`   DATE            DEFAULT NULL            COMMENT '预计到货日期',
  `total_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '订单金额合计',
  `source_type`   VARCHAR(16)     NOT NULL DEFAULT 'MANUAL' COMMENT '来源 MANUAL手工/REPLENISH补货建议',
  `status`        VARCHAR(16)     NOT NULL DEFAULT 'DRAFT' COMMENT '状态 DRAFT待审核/AUDITED已审核/PARTIAL部分收货/RECEIVED已收货/FINISHED已完成/VOID已作废',
  `remark`        VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_by`    BIGINT UNSIGNED NOT NULL                COMMENT '制单人ID',
  `audited_by`    BIGINT UNSIGNED DEFAULT NULL            COMMENT '审核人ID',
  `audited_at`    DATETIME        DEFAULT NULL            COMMENT '审核时间',
  `reject_reason` VARCHAR(255)    DEFAULT NULL            COMMENT '驳回原因',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_order_no` (`order_no`),
  KEY `idx_supplier` (`supplier_id`),
  KEY `idx_store_status` (`store_id`, `status`),
  KEY `idx_order_date` (`order_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购订单';

DROP TABLE IF EXISTS `pur_order_item`;
CREATE TABLE `pur_order_item` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id`     BIGINT UNSIGNED NOT NULL                COMMENT '采购订单ID',
  `product_id`   BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`     DECIMAL(12,3)   NOT NULL                COMMENT '采购数量',
  `received_qty` DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '已收货数量',
  `unit_price`   DECIMAL(12,2)   NOT NULL                COMMENT '采购单价',
  `amount`       DECIMAL(12,2)   NOT NULL                COMMENT '行金额 = 数量 × 单价',
  `remark`       VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_order` (`order_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购订单明细';

DROP TABLE IF EXISTS `pur_receipt`;
CREATE TABLE `pur_receipt` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '收货单ID',
  `receipt_no`   VARCHAR(32)     NOT NULL                COMMENT '收货单号 SH+日期+流水',
  `order_id`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '来源采购订单ID（无单入库时为空）',
  `supplier_id`  BIGINT UNSIGNED NOT NULL                COMMENT '供应商ID',
  `store_id`     BIGINT UNSIGNED NOT NULL                COMMENT '收货门店ID',
  `total_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '收货金额合计',
  `is_direct`    TINYINT         NOT NULL DEFAULT 0      COMMENT '是否无单直入 1是 0否',
  `diff_remark`  VARCHAR(255)    DEFAULT NULL            COMMENT '收货差异说明',
  `received_by`  BIGINT UNSIGNED NOT NULL                COMMENT '收货人ID',
  `received_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '收货时间',
  `status`       VARCHAR(16)     NOT NULL DEFAULT 'FINISHED' COMMENT '状态 FINISHED已完成/VOID已作废',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_receipt_no` (`receipt_no`),
  KEY `idx_order` (`order_id`),
  KEY `idx_supplier` (`supplier_id`),
  KEY `idx_store_time` (`store_id`, `received_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购收货单';

DROP TABLE IF EXISTS `pur_receipt_item`;
CREATE TABLE `pur_receipt_item` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `receipt_id`      BIGINT UNSIGNED NOT NULL                COMMENT '收货单ID',
  `product_id`      BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `order_qty`       DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '订单数量',
  `receive_qty`     DECIMAL(12,3)   NOT NULL                COMMENT '实收数量',
  `unit_price`      DECIMAL(12,2)   NOT NULL                COMMENT '收货单价',
  `amount`          DECIMAL(12,2)   NOT NULL                COMMENT '行金额',
  `batch_no`        VARCHAR(40)     DEFAULT NULL            COMMENT '批次号',
  `production_date` DATE            DEFAULT NULL            COMMENT '生产日期',
  `expiry_date`     DATE            DEFAULT NULL            COMMENT '到期日期',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_receipt` (`receipt_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购收货单明细';

DROP TABLE IF EXISTS `pur_return`;
CREATE TABLE `pur_return` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '采购退货单ID',
  `return_no`        VARCHAR(32)     NOT NULL                COMMENT '退货单号 CT+日期+流水',
  `supplier_id`      BIGINT UNSIGNED NOT NULL                COMMENT '供应商ID',
  `store_id`         BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `source_receipt_id` BIGINT UNSIGNED DEFAULT NULL           COMMENT '来源收货单ID',
  `total_amount`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '退货金额',
  `reason`           VARCHAR(255)    DEFAULT NULL            COMMENT '退货原因',
  `status`           VARCHAR(16)     NOT NULL DEFAULT 'FINISHED' COMMENT '状态',
  `created_by`       BIGINT UNSIGNED NOT NULL                COMMENT '制单人ID',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_return_no` (`return_no`),
  KEY `idx_supplier` (`supplier_id`),
  KEY `idx_store_time` (`store_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购退货单';

DROP TABLE IF EXISTS `pur_return_item`;
CREATE TABLE `pur_return_item` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `return_id`  BIGINT UNSIGNED NOT NULL                COMMENT '采购退货单ID',
  `product_id` BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`   DECIMAL(12,3)   NOT NULL                COMMENT '退货数量',
  `unit_price` DECIMAL(12,2)   NOT NULL                COMMENT '退货单价',
  `amount`     DECIMAL(12,2)   NOT NULL                COMMENT '行金额',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_return` (`return_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购退货明细';

DROP TABLE IF EXISTS `pur_payment`;
CREATE TABLE `pur_payment` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '付款记录ID',
  `pay_no`     VARCHAR(32)     NOT NULL                COMMENT '付款单号 FK+日期+流水',
  `supplier_id` BIGINT UNSIGNED NOT NULL               COMMENT '供应商ID',
  `amount`     DECIMAL(12,2)   NOT NULL                COMMENT '付款金额',
  `pay_method` VARCHAR(16)     NOT NULL                COMMENT '付款方式 CASH/TRANSFER/WECHAT/ALIPAY',
  `pay_date`   DATE            NOT NULL                COMMENT '付款日期',
  `operator`   BIGINT UNSIGNED NOT NULL                COMMENT '经办人ID',
  `remark`     VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pay_no` (`pay_no`),
  KEY `idx_supplier_date` (`supplier_id`, `pay_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商付款登记';


-- ============================================================================
--  第五节  库存域（inv_）
-- ============================================================================

DROP TABLE IF EXISTS `inv_stock`;
CREATE TABLE `inv_stock` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id`   BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `product_id` BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`   DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '当前库存数量',
  `safe_qty`   DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '安全库存下限',
  `max_qty`    DECIMAL(12,3)   DEFAULT NULL            COMMENT '库存上限',
  `avg_cost`   DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '当前移动加权平均成本',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_store_product` (`store_id`, `product_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实时库存（库存流水的冗余快照）';

DROP TABLE IF EXISTS `inv_batch`;
CREATE TABLE `inv_batch` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '批次ID',
  `batch_no`        VARCHAR(40)     NOT NULL                COMMENT '批次号',
  `product_id`      BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `store_id`        BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `production_date` DATE            DEFAULT NULL            COMMENT '生产日期',
  `expiry_date`     DATE            DEFAULT NULL            COMMENT '到期日期',
  `init_qty`        DECIMAL(12,3)   NOT NULL                COMMENT '批次初始数量',
  `remain_qty`      DECIMAL(12,3)   NOT NULL                COMMENT '批次剩余数量',
  `unit_cost`       DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '批次单位成本',
  `status`          TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1在库 0已耗尽 2已过期',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_batch_no` (`batch_no`),
  KEY `idx_product_store` (`product_id`, `store_id`),
  KEY `idx_expiry` (`expiry_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品批次与保质期';

DROP TABLE IF EXISTS `inv_stock_flow`;
CREATE TABLE `inv_stock_flow` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '流水ID',
  `store_id`    BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `product_id`  BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `flow_type`   VARCHAR(24)     NOT NULL                COMMENT '流水类型 PURCHASE_IN采购入库/SALE_OUT销售出库/SALE_RETURN_IN退货入库/PURCHASE_RETURN_OUT采购退货/CHECK_ADJUST盘点调整/LOSS_OUT报损出库/TRANSFER_IN调拨入库/TRANSFER_OUT调拨出库/INIT期初',
  `direction`   TINYINT         NOT NULL                COMMENT '方向 1入库 -1出库',
  `quantity`    DECIMAL(12,3)   NOT NULL                COMMENT '变动数量（正数）',
  `before_qty`  DECIMAL(12,3)   NOT NULL                COMMENT '变动前库存',
  `after_qty`   DECIMAL(12,3)   NOT NULL                COMMENT '变动后库存',
  `unit_cost`   DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '单位成本',
  `amount`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '变动金额',
  `source_type` VARCHAR(24)     NOT NULL                COMMENT '来源单据类型',
  `source_no`   VARCHAR(32)     DEFAULT NULL            COMMENT '来源单据号',
  `batch_id`    BIGINT UNSIGNED DEFAULT NULL            COMMENT '批次ID',
  `operator`    BIGINT UNSIGNED DEFAULT NULL            COMMENT '操作人ID',
  `remark`      VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
  PRIMARY KEY (`id`),
  KEY `idx_product_time` (`product_id`, `created_at`),
  KEY `idx_store_time` (`store_id`, `created_at`),
  KEY `idx_source` (`source_type`, `source_no`),
  KEY `idx_type_time` (`flow_type`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存流水（库存数据的唯一真相源，只增不改）';

DROP TABLE IF EXISTS `inv_check`;
CREATE TABLE `inv_check` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '盘点单ID',
  `check_no`    VARCHAR(32)     NOT NULL                COMMENT '盘点单号 PD+日期+流水',
  `store_id`    BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `check_type`  VARCHAR(16)     NOT NULL DEFAULT 'ALL'  COMMENT '盘点方式 ALL全盘/PART抽盘/CATEGORY按分类/SHELF按货架',
  `check_scope` VARCHAR(255)    DEFAULT NULL            COMMENT '盘点范围描述',
  `snapshot_at` DATETIME        NOT NULL                COMMENT '账面快照时间',
  `total_diff_amount` DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '盘点损益金额',
  `status`      VARCHAR(16)     NOT NULL DEFAULT 'DRAFT' COMMENT '状态 DRAFT盘点中/SUBMITTED待审核/AUDITED已审核/VOID已作废',
  `created_by`  BIGINT UNSIGNED NOT NULL                COMMENT '制单人ID',
  `audited_by`  BIGINT UNSIGNED DEFAULT NULL            COMMENT '审核人ID',
  `audited_at`  DATETIME        DEFAULT NULL            COMMENT '审核时间',
  `remark`      VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_check_no` (`check_no`),
  KEY `idx_store_status` (`store_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存盘点单';

DROP TABLE IF EXISTS `inv_check_item`;
CREATE TABLE `inv_check_item` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `check_id`     BIGINT UNSIGNED NOT NULL                COMMENT '盘点单ID',
  `product_id`   BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `book_qty`     DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '账面数量',
  `snapshot_qty` DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '快照数量',
  `actual_qty`   DECIMAL(12,3)   DEFAULT NULL            COMMENT '实盘数量',
  `diff_qty`     DECIMAL(12,3)   DEFAULT NULL            COMMENT '差异数量 = 实盘 - 账面',
  `diff_rate`    DECIMAL(8,4)    DEFAULT NULL            COMMENT '差异率',
  `diff_reason`  VARCHAR(64)     DEFAULT NULL            COMMENT '差异原因 漏记/损耗/错盘/串码',
  `cost_price`   DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '盘点时成本价',
  `profit_loss`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '损益金额',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_check` (`check_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='盘点明细';

DROP TABLE IF EXISTS `inv_loss`;
CREATE TABLE `inv_loss` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '报损单ID',
  `loss_no`      VARCHAR(32)     NOT NULL                COMMENT '报损单号 BS+日期+流水',
  `store_id`     BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `loss_type`    VARCHAR(16)     NOT NULL                COMMENT '报损类型 BREAK破损/EXPIRE过期/FRESH生鲜损耗/OTHER其他',
  `total_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '报损金额',
  `reason`       VARCHAR(255)    DEFAULT NULL            COMMENT '报损原因',
  `status`       VARCHAR(16)     NOT NULL DEFAULT 'FINISHED' COMMENT '状态',
  `created_by`   BIGINT UNSIGNED NOT NULL                COMMENT '制单人ID',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_loss_no` (`loss_no`),
  KEY `idx_store_time` (`store_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存报损单';

DROP TABLE IF EXISTS `inv_loss_item`;
CREATE TABLE `inv_loss_item` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `loss_id`    BIGINT UNSIGNED NOT NULL                COMMENT '报损单ID',
  `product_id` BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`   DECIMAL(12,3)   NOT NULL                COMMENT '报损数量',
  `unit_cost`  DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '单位成本',
  `amount`     DECIMAL(12,2)   NOT NULL                COMMENT '报损金额',
  `batch_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '批次ID',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_loss` (`loss_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='报损明细';

DROP TABLE IF EXISTS `inv_transfer`;
CREATE TABLE `inv_transfer` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '调拨单ID',
  `transfer_no`   VARCHAR(32)     NOT NULL                COMMENT '调拨单号 DB+日期+流水',
  `from_store_id` BIGINT UNSIGNED NOT NULL                COMMENT '调出门店ID',
  `to_store_id`   BIGINT UNSIGNED NOT NULL                COMMENT '调入门店ID',
  `status`        VARCHAR(16)     NOT NULL DEFAULT 'DRAFT' COMMENT '状态 DRAFT待调出/OUT已调出/IN已入库/VOID已作废',
  `created_by`    BIGINT UNSIGNED NOT NULL                COMMENT '制单人ID',
  `out_at`        DATETIME        DEFAULT NULL            COMMENT '调出时间',
  `in_at`         DATETIME        DEFAULT NULL            COMMENT '调入时间',
  `remark`        VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_transfer_no` (`transfer_no`),
  KEY `idx_from_store` (`from_store_id`),
  KEY `idx_to_store` (`to_store_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='门店调拨单';

DROP TABLE IF EXISTS `inv_transfer_item`;
CREATE TABLE `inv_transfer_item` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `transfer_id` BIGINT UNSIGNED NOT NULL                COMMENT '调拨单ID',
  `product_id`  BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`    DECIMAL(12,3)   NOT NULL                COMMENT '调拨数量',
  `unit_cost`   DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '单位成本',
  `amount`      DECIMAL(12,2)   NOT NULL                COMMENT '调拨金额',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_transfer` (`transfer_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='调拨明细';


-- ============================================================================
--  第六节  会员与促销域（mem_ / pro_）
-- ============================================================================

DROP TABLE IF EXISTS `mem_level`;
CREATE TABLE `mem_level` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '等级ID',
  `level_name`        VARCHAR(32)     NOT NULL                COMMENT '等级名称，如 普通/银卡/金卡',
  `level_value`       INT             NOT NULL                COMMENT '等级值，越大越高',
  `discount_rate`     DECIMAL(5,4)    NOT NULL DEFAULT 1.0000 COMMENT '折扣率，如 0.9800 表示98折',
  `upgrade_condition` VARCHAR(16)     NOT NULL DEFAULT 'CONSUME' COMMENT '升级依据 CONSUME累计消费/POINTS累计积分',
  `condition_value`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '升级门槛值',
  `benefit_desc`      VARCHAR(255)    DEFAULT NULL            COMMENT '权益说明',
  `sort`              INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`            TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_level_name` (`level_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员等级';

DROP TABLE IF EXISTS `mem_member`;
CREATE TABLE `mem_member` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '会员ID',
  `member_no`       VARCHAR(24)     NOT NULL                COMMENT '会员卡号 M+日期+流水',
  `phone`           VARCHAR(20)     NOT NULL                COMMENT '手机号',
  `real_name`       VARCHAR(32)     DEFAULT NULL            COMMENT '姓名',
  `gender`          TINYINT         NOT NULL DEFAULT 0      COMMENT '性别 0未知 1男 2女',
  `birthday`        DATE            DEFAULT NULL            COMMENT '生日',
  `level_id`        BIGINT UNSIGNED NOT NULL                COMMENT '会员等级ID',
  `balance`         DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '储值本金余额',
  `gift_balance`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '储值赠送余额',
  `points`          INT             NOT NULL DEFAULT 0      COMMENT '可用积分',
  `total_consume`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '累计消费金额',
  `consume_count`   INT             NOT NULL DEFAULT 0      COMMENT '累计消费笔数',
  `last_consume_at` DATETIME        DEFAULT NULL            COMMENT '最近消费时间',
  `source`          VARCHAR(16)     NOT NULL DEFAULT 'STORE' COMMENT '会员来源 STORE到店/REFER推荐/ONLINE线上',
  `status`          TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1正常 0冻结 2挂失',
  `remark`          VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_member_no` (`member_no`),
  UNIQUE KEY `uk_phone` (`phone`),
  KEY `idx_level` (`level_id`),
  KEY `idx_last_consume` (`last_consume_at`),
  KEY `idx_phone_suffix` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员档案';

DROP TABLE IF EXISTS `mem_point_flow`;
CREATE TABLE `mem_point_flow` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '流水ID',
  `member_id`      BIGINT UNSIGNED NOT NULL                COMMENT '会员ID',
  `flow_type`      VARCHAR(16)     NOT NULL                COMMENT '类型 EARN消费获取/RETURN消费退回/DEDUCT积分抵扣/EXPIRE过期扣减/ADJUST手工调整',
  `points`         INT             NOT NULL                COMMENT '变动积分（正数）',
  `direction`      TINYINT         NOT NULL                COMMENT '方向 1增加 -1减少',
  `before_points`  INT             NOT NULL                COMMENT '变动前积分',
  `after_points`   INT             NOT NULL                COMMENT '变动后积分',
  `source_no`      VARCHAR(32)     DEFAULT NULL            COMMENT '来源单据号',
  `operator`       BIGINT UNSIGNED DEFAULT NULL            COMMENT '操作人ID',
  `remark`         VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
  PRIMARY KEY (`id`),
  KEY `idx_member_time` (`member_id`, `created_at`),
  KEY `idx_source` (`source_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员积分流水（只增不改）';

DROP TABLE IF EXISTS `mem_balance_flow`;
CREATE TABLE `mem_balance_flow` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '流水ID',
  `member_id`      BIGINT UNSIGNED NOT NULL                COMMENT '会员ID',
  `flow_type`      VARCHAR(16)     NOT NULL                COMMENT '类型 RECHARGE充值/GIFT赠送/CONSUME消费/REFUND退款/ADJUST调整',
  `amount`         DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '本金变动金额',
  `gift_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '赠送金变动金额',
  `direction`      TINYINT         NOT NULL                COMMENT '方向 1增加 -1减少',
  `before_balance` DECIMAL(12,2)   NOT NULL                COMMENT '变动前本金余额',
  `after_balance`  DECIMAL(12,2)   NOT NULL                COMMENT '变动后本金余额',
  `before_gift`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '变动前赠送余额',
  `after_gift`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '变动后赠送余额',
  `pay_method`     VARCHAR(16)     DEFAULT NULL            COMMENT '支付方式（充值时）',
  `source_no`      VARCHAR(32)     DEFAULT NULL            COMMENT '来源单据号',
  `request_id`     VARCHAR(64)     DEFAULT NULL            COMMENT '幂等键（前端生成，防重复充值）',
  `operator`       BIGINT UNSIGNED DEFAULT NULL            COMMENT '操作人ID',
  `remark`         VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_request_id` (`request_id`),
  KEY `idx_member_time` (`member_id`, `created_at`),
  KEY `idx_source` (`source_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员储值流水（只增不改）';

DROP TABLE IF EXISTS `mem_tag`;
CREATE TABLE `mem_tag` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '标签ID',
  `tag_name`    VARCHAR(32)     NOT NULL                COMMENT '标签名称，如 高价值/沉睡/生鲜偏好',
  `tag_type`    VARCHAR(16)     NOT NULL DEFAULT 'AUTO' COMMENT '类型 AUTO自动/MANUAL手工',
  `tag_color`   VARCHAR(16)     DEFAULT NULL            COMMENT '标签颜色',
  `rule_desc`   VARCHAR(255)    DEFAULT NULL            COMMENT '自动标签规则说明',
  `sort`        INT             NOT NULL DEFAULT 0      COMMENT '排序',
  `status`      TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tag_name` (`tag_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员标签';

DROP TABLE IF EXISTS `mem_member_tag`;
CREATE TABLE `mem_member_tag` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `member_id`  BIGINT UNSIGNED NOT NULL                COMMENT '会员ID',
  `tag_id`     BIGINT UNSIGNED NOT NULL                COMMENT '标签ID',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_member_tag` (`member_id`, `tag_id`),
  KEY `idx_tag` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会员-标签关联';

DROP TABLE IF EXISTS `pro_promotion`;
CREATE TABLE `pro_promotion` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '促销ID',
  `promo_name`      VARCHAR(64)     NOT NULL                COMMENT '促销名称，如 每周三生鲜日',
  `promo_type`      VARCHAR(16)     NOT NULL                COMMENT '类型 SPECIAL特价/DISCOUNT折扣/FULL_REDUCE满减/FULL_GIFT满赠/COMBO组合套餐/COUPON优惠券/MEMBER会员专享',
  `priority`        INT             NOT NULL DEFAULT 10     COMMENT '优先级，数值小者优先',
  `start_date`      DATE            NOT NULL                COMMENT '生效开始日期',
  `end_date`        DATE            NOT NULL                COMMENT '生效结束日期',
  `week_days`       VARCHAR(16)     DEFAULT NULL            COMMENT '生效星期，如 5,6 表示周五周六，空为每天',
  `start_time`      TIME            DEFAULT NULL            COMMENT '每日生效开始时间',
  `end_time`        TIME            DEFAULT NULL            COMMENT '每日生效结束时间',
  `is_member_only`  TINYINT         NOT NULL DEFAULT 0      COMMENT '是否仅会员 1是 0否',
  `is_stackable`    TINYINT         NOT NULL DEFAULT 1      COMMENT '是否可与其他促销叠加 1是 0否',
  `source_rule_id`  BIGINT UNSIGNED DEFAULT NULL            COMMENT '来源关联规则ID（由智能分析生成时）',
  `trigger_count`   INT             NOT NULL DEFAULT 0      COMMENT '累计触发单数',
  `discount_total`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '累计优惠支出',
  `status`          VARCHAR(16)     NOT NULL DEFAULT 'DRAFT' COMMENT '状态 DRAFT未开始/RUNNING进行中/STOPPED已停用/EXPIRED已结束',
  `created_by`      BIGINT UNSIGNED NOT NULL                COMMENT '创建人ID',
  `remark`          VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_status_date` (`status`, `start_date`, `end_date`),
  KEY `idx_type` (`promo_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='促销规则';

DROP TABLE IF EXISTS `pro_promotion_item`;
CREATE TABLE `pro_promotion_item` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `promo_id`         BIGINT UNSIGNED NOT NULL                COMMENT '促销ID',
  `item_type`        VARCHAR(16)     NOT NULL DEFAULT 'PRODUCT' COMMENT '适用类型 PRODUCT商品/CATEGORY分类',
  `target_id`        BIGINT UNSIGNED NOT NULL                COMMENT '商品ID或分类ID',
  `promo_price`      DECIMAL(12,2)   DEFAULT NULL            COMMENT '特价（特价促销）',
  `discount_rate`    DECIMAL(5,4)    DEFAULT NULL            COMMENT '折扣率（折扣促销）',
  `threshold_amount` DECIMAL(12,2)   DEFAULT NULL            COMMENT '门槛金额（满减/满赠）',
  `reduce_amount`    DECIMAL(12,2)   DEFAULT NULL            COMMENT '减免金额（满减）',
  `gift_product_id`  BIGINT UNSIGNED DEFAULT NULL            COMMENT '赠品商品ID（满赠）',
  `gift_qty`         DECIMAL(12,3)   DEFAULT NULL            COMMENT '赠品数量',
  `combo_qty`        DECIMAL(12,3)   DEFAULT NULL            COMMENT '套餐内数量（组合套餐）',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_promo` (`promo_id`),
  KEY `idx_target` (`item_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='促销规则明细';

DROP TABLE IF EXISTS `pro_coupon`;
CREATE TABLE `pro_coupon` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '优惠券ID',
  `coupon_name`  VARCHAR(64)     NOT NULL                COMMENT '优惠券名称，如 新客5元券',
  `coupon_type`  VARCHAR(16)     NOT NULL                COMMENT '类型 CASH现金券/DISCOUNT折扣券/GIFT赠品券',
  `face_value`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '面值（现金券）',
  `discount_rate` DECIMAL(5,4)   DEFAULT NULL            COMMENT '折扣率（折扣券）',
  `threshold`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '使用门槛',
  `total_qty`    INT             NOT NULL DEFAULT 0      COMMENT '发行总量，0为不限',
  `issued_qty`   INT             NOT NULL DEFAULT 0      COMMENT '已发放数量',
  `used_qty`     INT             NOT NULL DEFAULT 0      COMMENT '已核销数量',
  `valid_from`   DATE            NOT NULL                COMMENT '有效期开始',
  `valid_to`     DATE            NOT NULL                COMMENT '有效期结束',
  `promo_id`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联促销ID',
  `status`       TINYINT         NOT NULL DEFAULT 1      COMMENT '状态 1启用 0停用',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_valid` (`valid_from`, `valid_to`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='优惠券';

DROP TABLE IF EXISTS `pro_coupon_record`;
CREATE TABLE `pro_coupon_record` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `coupon_id`    BIGINT UNSIGNED NOT NULL                COMMENT '优惠券ID',
  `member_id`    BIGINT UNSIGNED NOT NULL                COMMENT '会员ID',
  `use_status`   VARCHAR(16)     NOT NULL DEFAULT 'UNUSED' COMMENT '状态 UNUSED未使用/USED已核销/EXPIRED已过期',
  `used_trade_id` BIGINT UNSIGNED DEFAULT NULL           COMMENT '核销的销售单ID',
  `issued_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发放时间',
  `used_at`      DATETIME        DEFAULT NULL            COMMENT '核销时间',
  PRIMARY KEY (`id`),
  KEY `idx_member_status` (`member_id`, `use_status`),
  KEY `idx_coupon` (`coupon_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='优惠券领用核销记录';


-- ============================================================================
--  第七节  销售域（sal_）
-- ============================================================================

DROP TABLE IF EXISTS `sal_trade`;
CREATE TABLE `sal_trade` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '销售单ID',
  `trade_no`        VARCHAR(32)     NOT NULL                COMMENT '销售单号 XS+日期+流水',
  `request_id`      VARCHAR(64)     NOT NULL                COMMENT '幂等键（前端生成UUID，防重复扣款）',
  `store_id`        BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `pos_id`          BIGINT UNSIGNED NOT NULL                COMMENT '收银台ID',
  `session_id`      BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属收银班次ID',
  `cashier_id`      BIGINT UNSIGNED NOT NULL                COMMENT '收银员ID',
  `member_id`       BIGINT UNSIGNED DEFAULT NULL            COMMENT '会员ID（非会员为空）',
  `total_qty`       DECIMAL(12,3)   NOT NULL DEFAULT 0.000  COMMENT '商品总数量',
  `total_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '商品金额合计',
  `discount_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '优惠合计',
  `point_deduct`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '积分抵扣金额',
  `receivable`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '应收金额',
  `received`        DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '实收金额',
  `change_amount`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '找零金额',
  `cost_amount`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '销售成本合计',
  `gross_profit`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '毛利额',
  `round_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '抹零金额',
  `status`          VARCHAR(16)     NOT NULL DEFAULT 'FINISHED' COMMENT '状态 FINISHED已完成/RETURNED已退货/VOID已作废',
  `trade_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '交易时间',
  `print_count`     INT             NOT NULL DEFAULT 0      COMMENT '小票打印次数（重打累加，对应接口 print_count）',
  `remark`          VARCHAR(255)    DEFAULT NULL            COMMENT '备注',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trade_no` (`trade_no`),
  UNIQUE KEY `uk_request_id` (`request_id`),
  KEY `idx_store_time` (`store_id`, `trade_time`),
  KEY `idx_member` (`member_id`),
  KEY `idx_cashier_time` (`cashier_id`, `trade_time`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售主单';

DROP TABLE IF EXISTS `sal_trade_item`;
CREATE TABLE `sal_trade_item` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `trade_id`        BIGINT UNSIGNED NOT NULL                COMMENT '销售单ID',
  `product_id`      BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `product_name`    VARCHAR(64)     NOT NULL                COMMENT '商品名称快照',
  `spec`            VARCHAR(32)     DEFAULT NULL            COMMENT '规格快照',
  `unit_name`       VARCHAR(20)     DEFAULT NULL            COMMENT '单位快照',
  `quantity`        DECIMAL(12,3)   NOT NULL                COMMENT '销售数量',
  `unit_price`      DECIMAL(12,2)   NOT NULL                COMMENT '成交单价',
  `origin_price`    DECIMAL(12,2)   DEFAULT NULL            COMMENT '原价（用于展示优惠）',
  `cost_price`      DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '出库时点单位成本（锁定毛利）',
  `discount_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '行优惠金额',
  `amount`          DECIMAL(12,2)   NOT NULL                COMMENT '行金额',
  `batch_id`        BIGINT UNSIGNED DEFAULT NULL            COMMENT '出库批次ID',
  `promo_id`        BIGINT UNSIGNED DEFAULT NULL            COMMENT '命中的促销ID',
  `promo_type`      VARCHAR(16)     DEFAULT NULL            COMMENT '命中的促销类型',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_trade` (`trade_id`),
  KEY `idx_product_time` (`product_id`, `created_at`),
  KEY `idx_promo` (`promo_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售明细';

DROP TABLE IF EXISTS `sal_trade_payment`;
CREATE TABLE `sal_trade_payment` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `trade_id`   BIGINT UNSIGNED NOT NULL                COMMENT '销售单ID',
  `pay_method` VARCHAR(16)     NOT NULL                COMMENT '支付方式 CASH现金/WECHAT微信/ALIPAY支付宝/CARD银行卡/BALANCE储值/POINTS积分/OTHER其他',
  `amount`     DECIMAL(12,2)   NOT NULL                COMMENT '支付金额',
  `pay_no`     VARCHAR(64)     DEFAULT NULL            COMMENT '第三方支付流水号',
  `pay_status` VARCHAR(16)     NOT NULL DEFAULT 'SUCCESS' COMMENT '状态 SUCCESS成功/FAIL失败/REFUNDED已退款',
  `pay_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '支付时间',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_trade` (`trade_id`),
  KEY `idx_method_time` (`pay_method`, `pay_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售支付明细（支持混合支付）';

DROP TABLE IF EXISTS `sal_return`;
CREATE TABLE `sal_return` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '退货单ID',
  `return_no`       VARCHAR(32)     NOT NULL                COMMENT '退货单号 TH+日期+流水',
  `source_trade_id` BIGINT UNSIGNED NOT NULL                COMMENT '原销售单ID',
  `store_id`        BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `member_id`       BIGINT UNSIGNED DEFAULT NULL            COMMENT '会员ID',
  `total_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '退货金额',
  `cost_amount`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '成本回冲金额',
  `refund_method`   VARCHAR(16)     NOT NULL                COMMENT '退款方式 CASH/WECHAT/ALIPAY/CARD/BALANCE',
  `refund_status`   VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT '退款状态 PENDING待退款/REFUNDED已退款/FAILED退款失败',
  `refunded_at`     DATETIME        DEFAULT NULL            COMMENT '退款完成时间',
  `reason`          VARCHAR(255)    DEFAULT NULL            COMMENT '退货原因',
  `operator`        BIGINT UNSIGNED NOT NULL                COMMENT '操作人ID',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_return_no` (`return_no`),
  KEY `idx_source_trade` (`source_trade_id`),
  KEY `idx_store_time` (`store_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售退货单';

DROP TABLE IF EXISTS `sal_return_item`;
CREATE TABLE `sal_return_item` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `return_id`  BIGINT UNSIGNED NOT NULL                COMMENT '退货单ID',
  `product_id` BIGINT UNSIGNED NOT NULL                COMMENT '商品ID',
  `quantity`   DECIMAL(12,3)   NOT NULL                COMMENT '退货数量',
  `unit_price` DECIMAL(12,2)   NOT NULL                COMMENT '退货单价（取原成交价）',
  `cost_price` DECIMAL(12,4)   NOT NULL DEFAULT 0.0000 COMMENT '原销售成本价',
  `amount`     DECIMAL(12,2)   NOT NULL                COMMENT '行金额',
  `batch_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '回滚批次ID',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_return` (`return_id`),
  KEY `idx_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售退货明细';

DROP TABLE IF EXISTS `sal_hold`;
CREATE TABLE `sal_hold` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '挂单ID',
  `hold_no`     VARCHAR(32)     NOT NULL                COMMENT '挂单号',
  `store_id`    BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `pos_id`      BIGINT UNSIGNED NOT NULL                COMMENT '收银台ID',
  `member_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '会员ID',
  `cart_json`   JSON            NOT NULL                COMMENT '购物车快照',
  `item_count`  INT             NOT NULL DEFAULT 0      COMMENT '商品件数',
  `amount`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '挂单金额',
  `operator`    BIGINT UNSIGNED NOT NULL                COMMENT '操作人ID',
  `status`      VARCHAR(16)     NOT NULL DEFAULT 'HOLDING' COMMENT '状态 HOLDING挂起/RESUMED已取单/CANCELED已取消',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '挂单时间',
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_hold_no` (`hold_no`),
  KEY `idx_pos_status` (`pos_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收银挂单';

DROP TABLE IF EXISTS `sal_session`;
CREATE TABLE `sal_session` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '班次ID',
  `session_no`     VARCHAR(32)     NOT NULL                COMMENT '班次号 JB+日期+流水',
  `store_id`       BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `pos_id`         BIGINT UNSIGNED NOT NULL                COMMENT '收银台ID',
  `cashier_id`     BIGINT UNSIGNED NOT NULL                COMMENT '收银员ID',
  `open_time`      DATETIME        NOT NULL                COMMENT '开班时间',
  `close_time`     DATETIME        DEFAULT NULL            COMMENT '交班时间',
  `init_cash`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '备用金',
  `sale_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '销售金额',
  `sale_count`     INT             NOT NULL DEFAULT 0      COMMENT '销售笔数',
  `return_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '退货金额',
  `void_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '作废金额',
  `cash_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '现金收款',
  `wechat_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '微信收款',
  `alipay_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '支付宝收款',
  `card_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '银行卡收款',
  `balance_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '储值收款',
  `actual_cash`    DECIMAL(12,2)   DEFAULT NULL            COMMENT '实点现金',
  `cash_diff`      DECIMAL(12,2)   DEFAULT NULL            COMMENT '现金差异',
  `diff_remark`    VARCHAR(255)    DEFAULT NULL            COMMENT '差异说明',
  `status`         VARCHAR(16)     NOT NULL DEFAULT 'OPEN' COMMENT '状态 OPEN营业中/CLOSED待审核/AUDITED已审核',
  `audited_by`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '审核人ID',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_no` (`session_no`),
  KEY `idx_cashier_time` (`cashier_id`, `open_time`),
  KEY `idx_store_status` (`store_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收银班次（交班对账）';


-- ============================================================================
--  第八节  智能分析域（bi_）
-- ============================================================================

DROP TABLE IF EXISTS `bi_analysis_task`;
CREATE TABLE `bi_analysis_task` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '任务ID',
  `task_type`     VARCHAR(24)     NOT NULL                COMMENT '任务类型 ASSOCIATION关联规则/FORECAST销量预测/REPLENISH补货建议',
  `algorithm`     VARCHAR(24)     DEFAULT NULL            COMMENT '算法 FP_GROWTH/APRIORI',
  `params_json`   JSON            DEFAULT NULL            COMMENT '挖掘参数（最小支持度/置信度/提升度/时间窗口等）',
  `data_size`     INT             DEFAULT NULL            COMMENT '参与分析的交易笔数',
  `item_count`    INT             DEFAULT NULL            COMMENT '参与分析的商品数',
  `rule_count`    INT             DEFAULT NULL            COMMENT '产出规则数',
  `status`        VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT '状态 PENDING排队/RUNNING执行中/SUCCESS成功/FAILED失败',
  `start_time`    DATETIME        DEFAULT NULL            COMMENT '开始时间',
  `end_time`      DATETIME        DEFAULT NULL            COMMENT '结束时间',
  `cost_ms`       INT             DEFAULT NULL            COMMENT '耗时（毫秒）',
  `result_summary` VARCHAR(500)   DEFAULT NULL            COMMENT '结果摘要',
  `error_msg`     VARCHAR(500)    DEFAULT NULL            COMMENT '错误信息',
  `created_by`    BIGINT UNSIGNED DEFAULT NULL            COMMENT '触发人ID（为空表示定时任务）',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_type_time` (`task_type`, `created_at`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能分析任务';

DROP TABLE IF EXISTS `bi_association_rule`;
CREATE TABLE `bi_association_rule` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '规则ID',
  `task_id`     BIGINT UNSIGNED NOT NULL                COMMENT '所属分析任务ID',
  `antecedent`  JSON            NOT NULL                COMMENT '前件商品ID集合，如 [12,45]',
  `consequent`  JSON            NOT NULL                COMMENT '后件商品ID集合，如 [78]',
  `antecedent_name` VARCHAR(255) DEFAULT NULL           COMMENT '前件商品名称（冗余，便于展示）',
  `consequent_name` VARCHAR(255) DEFAULT NULL           COMMENT '后件商品名称（冗余，便于展示）',
  `support`     DECIMAL(8,5)    NOT NULL                COMMENT '支持度',
  `confidence`  DECIMAL(8,5)    NOT NULL                COMMENT '置信度',
  `lift`        DECIMAL(10,4)   NOT NULL                COMMENT '提升度',
  `leverage`    DECIMAL(10,5)   DEFAULT NULL            COMMENT '杠杆率',
  `conviction`  DECIMAL(10,4)   DEFAULT NULL            COMMENT '确信度',
  `suggestion`  VARCHAR(64)     DEFAULT NULL            COMMENT '建议动作 捆绑/陈列/套餐/推荐',
  `interpretation` VARCHAR(500) DEFAULT NULL            COMMENT '自动生成的业务解读文本',
  `adopted`     TINYINT         NOT NULL DEFAULT 0      COMMENT '是否已被采纳 1是 0否',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_task` (`task_id`),
  KEY `idx_lift` (`lift`),
  KEY `idx_confidence` (`confidence`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品关联规则';

DROP TABLE IF EXISTS `bi_recommend_cache`;
CREATE TABLE `bi_recommend_cache` (
  `id`                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id`          BIGINT UNSIGNED NOT NULL                COMMENT '触发商品ID',
  `recommend_product_id` BIGINT UNSIGNED NOT NULL               COMMENT '推荐商品ID',
  `score`               DECIMAL(10,4)   NOT NULL DEFAULT 0.0000 COMMENT '推荐得分（提升度×置信度）',
  `rule_id`             BIGINT UNSIGNED DEFAULT NULL            COMMENT '来源规则ID',
  `reason`              VARCHAR(64)     DEFAULT NULL            COMMENT '推荐理由，如 常与啤酒一起购买',
  `updated_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_product_recommend` (`product_id`, `recommend_product_id`),
  KEY `idx_product_score` (`product_id`, `score`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收银台关联推荐缓存';

DROP TABLE IF EXISTS `bi_daily_stat`;
CREATE TABLE `bi_daily_stat` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `stat_date`     DATE            NOT NULL                COMMENT '统计日期',
  `store_id`      BIGINT UNSIGNED NOT NULL                COMMENT '门店ID',
  `sale_amount`   DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '销售额',
  `refund_amount` DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '退货金额',
  `net_amount`    DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '净销售额',
  `sale_count`    INT             NOT NULL DEFAULT 0      COMMENT '销售笔数',
  `avg_price`     DECIMAL(12,2)   NOT NULL DEFAULT 0.00   COMMENT '客单价',
  `cost_amount`   DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '销售成本',
  `gross_profit`  DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '毛利额',
  `gross_rate`    DECIMAL(8,4)    NOT NULL DEFAULT 0.0000 COMMENT '毛利率',
  `member_amount` DECIMAL(14,2)   NOT NULL DEFAULT 0.00   COMMENT '会员消费金额',
  `new_member`    INT             NOT NULL DEFAULT 0      COMMENT '新增会员数',
  `sale_qty`      DECIMAL(14,3)   NOT NULL DEFAULT 0.000  COMMENT '销售件数',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_store` (`stat_date`, `store_id`),
  KEY `idx_date` (`stat_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日统计聚合表（看板与报表数据源）';


-- ============================================================================
--  第九节  外键约束（集中创建，统一命名 fk_源表_字段）
-- ============================================================================

ALTER TABLE `base_pos`              ADD CONSTRAINT `fk_pos_store`            FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `base_shelf`            ADD CONSTRAINT `fk_shelf_store`          FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `base_unit`             ADD CONSTRAINT `fk_unit_base`            FOREIGN KEY (`base_unit_id`)    REFERENCES `base_unit` (`id`);

ALTER TABLE `sys_user`              ADD CONSTRAINT `fk_user_store`           FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `sys_user_role`         ADD CONSTRAINT `fk_ur_user`              FOREIGN KEY (`user_id`)         REFERENCES `sys_user` (`id`);
ALTER TABLE `sys_user_role`         ADD CONSTRAINT `fk_ur_role`              FOREIGN KEY (`role_id`)         REFERENCES `sys_role` (`id`);
ALTER TABLE `sys_role_permission`   ADD CONSTRAINT `fk_rp_role`              FOREIGN KEY (`role_id`)         REFERENCES `sys_role` (`id`);
ALTER TABLE `sys_role_permission`   ADD CONSTRAINT `fk_rp_perm`              FOREIGN KEY (`permission_id`)   REFERENCES `sys_permission` (`id`);
ALTER TABLE `sys_operation_log`     ADD CONSTRAINT `fk_oplog_user`           FOREIGN KEY (`user_id`)         REFERENCES `sys_user` (`id`);
ALTER TABLE `sys_login_log`         ADD CONSTRAINT `fk_loginlog_user`        FOREIGN KEY (`user_id`)         REFERENCES `sys_user` (`id`);

ALTER TABLE `prd_spu`               ADD CONSTRAINT `fk_spu_category`         FOREIGN KEY (`category_id`)     REFERENCES `prd_category` (`id`);
ALTER TABLE `prd_product`           ADD CONSTRAINT `fk_product_category`     FOREIGN KEY (`category_id`)     REFERENCES `prd_category` (`id`);
ALTER TABLE `prd_product`           ADD CONSTRAINT `fk_product_spu`          FOREIGN KEY (`spu_id`)          REFERENCES `prd_spu` (`id`);
ALTER TABLE `prd_product`           ADD CONSTRAINT `fk_product_unit`         FOREIGN KEY (`unit_id`)         REFERENCES `base_unit` (`id`);
ALTER TABLE `prd_product`           ADD CONSTRAINT `fk_product_punit`        FOREIGN KEY (`purchase_unit_id`) REFERENCES `base_unit` (`id`);
ALTER TABLE `prd_product`           ADD CONSTRAINT `fk_product_shelf`        FOREIGN KEY (`shelf_id`)        REFERENCES `base_shelf` (`id`);
ALTER TABLE `prd_barcode`           ADD CONSTRAINT `fk_barcode_product`      FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `prd_price_history`     ADD CONSTRAINT `fk_price_product`        FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `prd_product_tag`       ADD CONSTRAINT `fk_pt_product`           FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `prd_product_tag`       ADD CONSTRAINT `fk_pt_tag`               FOREIGN KEY (`tag_id`)          REFERENCES `prd_tag` (`id`);

ALTER TABLE `pur_supplier_product`  ADD CONSTRAINT `fk_sp_supplier`          FOREIGN KEY (`supplier_id`)     REFERENCES `pur_supplier` (`id`);
ALTER TABLE `pur_supplier_product`  ADD CONSTRAINT `fk_sp_product`           FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `pur_order`             ADD CONSTRAINT `fk_po_supplier`          FOREIGN KEY (`supplier_id`)     REFERENCES `pur_supplier` (`id`);
ALTER TABLE `pur_order`             ADD CONSTRAINT `fk_po_store`             FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `pur_order`             ADD CONSTRAINT `fk_po_creator`           FOREIGN KEY (`created_by`)      REFERENCES `sys_user` (`id`);
ALTER TABLE `pur_order_item`        ADD CONSTRAINT `fk_poi_order`            FOREIGN KEY (`order_id`)        REFERENCES `pur_order` (`id`);
ALTER TABLE `pur_order_item`        ADD CONSTRAINT `fk_poi_product`          FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `pur_receipt`           ADD CONSTRAINT `fk_pr_order`             FOREIGN KEY (`order_id`)        REFERENCES `pur_order` (`id`);
ALTER TABLE `pur_receipt`           ADD CONSTRAINT `fk_pr_supplier`          FOREIGN KEY (`supplier_id`)     REFERENCES `pur_supplier` (`id`);
ALTER TABLE `pur_receipt_item`      ADD CONSTRAINT `fk_pri_receipt`          FOREIGN KEY (`receipt_id`)      REFERENCES `pur_receipt` (`id`);
ALTER TABLE `pur_receipt_item`      ADD CONSTRAINT `fk_pri_product`          FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `pur_return`            ADD CONSTRAINT `fk_pret_supplier`        FOREIGN KEY (`supplier_id`)     REFERENCES `pur_supplier` (`id`);
ALTER TABLE `pur_return_item`       ADD CONSTRAINT `fk_preti_return`         FOREIGN KEY (`return_id`)       REFERENCES `pur_return` (`id`);
ALTER TABLE `pur_return_item`       ADD CONSTRAINT `fk_preti_product`        FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `pur_payment`           ADD CONSTRAINT `fk_pay_supplier`         FOREIGN KEY (`supplier_id`)     REFERENCES `pur_supplier` (`id`);

ALTER TABLE `inv_stock`             ADD CONSTRAINT `fk_stock_store`          FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `inv_stock`             ADD CONSTRAINT `fk_stock_product`        FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `inv_batch`             ADD CONSTRAINT `fk_batch_product`        FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `inv_batch`             ADD CONSTRAINT `fk_batch_store`          FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `inv_stock_flow`        ADD CONSTRAINT `fk_flow_store`           FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `inv_stock_flow`        ADD CONSTRAINT `fk_flow_product`         FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `inv_stock_flow`        ADD CONSTRAINT `fk_flow_batch`           FOREIGN KEY (`batch_id`)        REFERENCES `inv_batch` (`id`);
ALTER TABLE `inv_check`             ADD CONSTRAINT `fk_check_store`          FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `inv_check_item`        ADD CONSTRAINT `fk_checki_check`         FOREIGN KEY (`check_id`)        REFERENCES `inv_check` (`id`);
ALTER TABLE `inv_check_item`        ADD CONSTRAINT `fk_checki_product`       FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `inv_loss`              ADD CONSTRAINT `fk_loss_store`           FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `inv_loss_item`         ADD CONSTRAINT `fk_lossi_loss`           FOREIGN KEY (`loss_id`)         REFERENCES `inv_loss` (`id`);
ALTER TABLE `inv_loss_item`         ADD CONSTRAINT `fk_lossi_product`        FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `inv_transfer`          ADD CONSTRAINT `fk_tf_from_store`        FOREIGN KEY (`from_store_id`)   REFERENCES `base_store` (`id`);
ALTER TABLE `inv_transfer`          ADD CONSTRAINT `fk_tf_to_store`          FOREIGN KEY (`to_store_id`)     REFERENCES `base_store` (`id`);
ALTER TABLE `inv_transfer_item`     ADD CONSTRAINT `fk_tfi_transfer`         FOREIGN KEY (`transfer_id`)     REFERENCES `inv_transfer` (`id`);
ALTER TABLE `inv_transfer_item`     ADD CONSTRAINT `fk_tfi_product`          FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);

ALTER TABLE `mem_member`            ADD CONSTRAINT `fk_member_level`         FOREIGN KEY (`level_id`)        REFERENCES `mem_level` (`id`);
ALTER TABLE `mem_point_flow`        ADD CONSTRAINT `fk_point_member`         FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);
ALTER TABLE `mem_balance_flow`      ADD CONSTRAINT `fk_balance_member`       FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);
ALTER TABLE `mem_member_tag`        ADD CONSTRAINT `fk_mt_member`            FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);
ALTER TABLE `mem_member_tag`        ADD CONSTRAINT `fk_mt_tag`               FOREIGN KEY (`tag_id`)          REFERENCES `mem_tag` (`id`);
ALTER TABLE `pro_promotion_item`    ADD CONSTRAINT `fk_promoi_promo`         FOREIGN KEY (`promo_id`)        REFERENCES `pro_promotion` (`id`);
ALTER TABLE `pro_promotion_item`    ADD CONSTRAINT `fk_promoi_product`       FOREIGN KEY (`gift_product_id`) REFERENCES `prd_product` (`id`);
ALTER TABLE `pro_coupon`            ADD CONSTRAINT `fk_coupon_promo`         FOREIGN KEY (`promo_id`)        REFERENCES `pro_promotion` (`id`);
ALTER TABLE `pro_coupon_record`     ADD CONSTRAINT `fk_cr_coupon`            FOREIGN KEY (`coupon_id`)       REFERENCES `pro_coupon` (`id`);
ALTER TABLE `pro_coupon_record`     ADD CONSTRAINT `fk_cr_member`            FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);

ALTER TABLE `sal_trade`             ADD CONSTRAINT `fk_trade_store`          FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `sal_trade`             ADD CONSTRAINT `fk_trade_pos`            FOREIGN KEY (`pos_id`)          REFERENCES `base_pos` (`id`);
ALTER TABLE `sal_trade`             ADD CONSTRAINT `fk_trade_session`        FOREIGN KEY (`session_id`)      REFERENCES `sal_session` (`id`);
ALTER TABLE `sal_trade`             ADD CONSTRAINT `fk_trade_cashier`        FOREIGN KEY (`cashier_id`)      REFERENCES `sys_user` (`id`);
ALTER TABLE `sal_trade`             ADD CONSTRAINT `fk_trade_member`         FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);
ALTER TABLE `sal_trade_item`        ADD CONSTRAINT `fk_tradei_trade`         FOREIGN KEY (`trade_id`)        REFERENCES `sal_trade` (`id`);
ALTER TABLE `sal_trade_item`        ADD CONSTRAINT `fk_tradei_product`       FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `sal_trade_item`        ADD CONSTRAINT `fk_tradei_promo`         FOREIGN KEY (`promo_id`)        REFERENCES `pro_promotion` (`id`);
ALTER TABLE `sal_trade_payment`     ADD CONSTRAINT `fk_pay_trade`            FOREIGN KEY (`trade_id`)        REFERENCES `sal_trade` (`id`);
ALTER TABLE `sal_return`            ADD CONSTRAINT `fk_return_trade`         FOREIGN KEY (`source_trade_id`) REFERENCES `sal_trade` (`id`);
ALTER TABLE `sal_return`            ADD CONSTRAINT `fk_return_store`         FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `sal_return`            ADD CONSTRAINT `fk_return_member`        FOREIGN KEY (`member_id`)       REFERENCES `mem_member` (`id`);
ALTER TABLE `sal_return_item`       ADD CONSTRAINT `fk_returni_return`       FOREIGN KEY (`return_id`)       REFERENCES `sal_return` (`id`);
ALTER TABLE `sal_return_item`       ADD CONSTRAINT `fk_returni_product`      FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `sal_hold`              ADD CONSTRAINT `fk_hold_store`           FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `sal_hold`              ADD CONSTRAINT `fk_hold_pos`             FOREIGN KEY (`pos_id`)          REFERENCES `base_pos` (`id`);
ALTER TABLE `sal_session`           ADD CONSTRAINT `fk_session_store`        FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);
ALTER TABLE `sal_session`           ADD CONSTRAINT `fk_session_pos`          FOREIGN KEY (`pos_id`)          REFERENCES `base_pos` (`id`);
ALTER TABLE `sal_session`           ADD CONSTRAINT `fk_session_cashier`      FOREIGN KEY (`cashier_id`)      REFERENCES `sys_user` (`id`);

ALTER TABLE `bi_association_rule`   ADD CONSTRAINT `fk_rule_task`            FOREIGN KEY (`task_id`)         REFERENCES `bi_analysis_task` (`id`);
ALTER TABLE `bi_recommend_cache`    ADD CONSTRAINT `fk_rec_product`          FOREIGN KEY (`product_id`)      REFERENCES `prd_product` (`id`);
ALTER TABLE `bi_recommend_cache`    ADD CONSTRAINT `fk_rec_target`           FOREIGN KEY (`recommend_product_id`) REFERENCES `prd_product` (`id`);
ALTER TABLE `bi_recommend_cache`    ADD CONSTRAINT `fk_rec_rule`             FOREIGN KEY (`rule_id`)         REFERENCES `bi_association_rule` (`id`);
ALTER TABLE `bi_daily_stat`         ADD CONSTRAINT `fk_stat_store`           FOREIGN KEY (`store_id`)        REFERENCES `base_store` (`id`);



SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================================
--  脚本结束（仅表结构）
--  表数量：62 张
--    base 4 + sys 12 + prd 7 + pur 9 + inv 9 + mem/pro 10 + sal 7 + bi 4 = 62
--  外键数量：86 条（sys_no_seq 不引入外键）
--  唯一键数量：42 个（含 mem_balance_flow.uk_request_id）
--  初始化数据见 02_init_data.sql（权限树、用户、商品、会员、促销等）
-- ============================================================================
