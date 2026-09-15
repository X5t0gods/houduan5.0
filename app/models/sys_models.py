"""权限与用户域（sys_）—— 12 张表（含阶段 1 新增的 sys_no_seq）。

对应 db/01_schema.sql 第二节。

⚠️ sys_no_seq 是本次阶段 1 新增：为 16 类单据/编码提供并发安全的流水号。
   复合主键 (seq_key, seq_date)，⛔ 不加 AUTO_INCREMENT —— 因为
   LAST_INSERT_ID(expr) 在首次插入路径上会返回自增 ID（一个巨大值）而非 expr，
   去掉自增列才能让"首次插入"与"重复更新"两条路径返回同一语义。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysRole(Base):
    """角色（sys_role）。"""

    __tablename__ = "sys_role"
    __table_args__ = (
        UniqueConstraint("role_code", name="uk_role_code"),
        {"comment": "角色"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="角色ID")
    role_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="角色编码，如 ADMIN/MANAGER/CASHIER")
    role_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="角色名称")
    # data_scope 决定 repository 层的数据过滤规则（阶段 2 起接入）
    data_scope: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'SELF'"), comment="数据范围 ALL/STORE/DEPT/SELF")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="角色说明")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SysPermission(Base):
    """权限/菜单（sys_permission）。

    ⚠️ perm_code 必须与前端一字不差（错一个字母 = 一整个菜单消失），见阶段 0 permissions.py。
    """

    __tablename__ = "sys_permission"
    __table_args__ = (
        UniqueConstraint("perm_code", name="uk_perm_code"),
        {"comment": "权限/菜单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="权限ID")
    parent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), comment="父级权限ID，0为顶级")
    perm_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限编码，如 goods:product:create")
    perm_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限名称")
    perm_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="类型 1目录 2菜单（页面级权限） 3按钮（操作级权限）")
    path: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="前端路由或接口路径")
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="菜单图标")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SysUser(Base):
    """系统用户（sys_user）。

    ⚠️ password_hash 存 bcrypt cost=10 的 $2b$ 哈希；演示账号密码统一 123456，
       直接复用 db/02_init_data.sql 的哈希，不要重新生成（阶段 0 决策）。
    """

    __tablename__ = "sys_user"
    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        {"comment": "系统用户"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="用户ID")
    username: Mapped[str] = mapped_column(String(32), nullable=False, comment="登录账号")
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, comment="密码哈希（bcrypt）")
    real_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="真实姓名")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="手机号")
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="头像地址")
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("base_store.id", ondelete="RESTRICT"), nullable=True, comment="所属门店ID")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后登录时间")
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, comment="最后登录IP")
    # 登录失败锁定策略（阶段 2）：连续 5 次失败锁 15 分钟，返回错误码 1003
    login_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="连续登录失败次数")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="锁定截止时间")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SysUserRole(Base):
    """用户-角色关联（sys_user_role）。"""

    __tablename__ = "sys_user_role"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uk_user_role"),
        {"comment": "用户-角色关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=False, comment="用户ID")
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id", ondelete="RESTRICT"), nullable=False, comment="角色ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SysRolePermission(Base):
    """角色-权限关联（sys_role_permission）。"""

    __tablename__ = "sys_role_permission"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uk_role_perm"),
        {"comment": "角色-权限关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id", ondelete="RESTRICT"), nullable=False, comment="角色ID")
    permission_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_permission.id", ondelete="RESTRICT"), nullable=False, comment="权限ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SysOperationLog(Base):
    """操作日志（sys_operation_log）。

    ⚠️ 只增不改（审计规范）：改价、退货、作废、盘点调整、库存手工调整、
       权限变更、备份恢复、参数修改 100% 记录，含 before/after JSON 快照。
    """

    __tablename__ = "sys_operation_log"
    __table_args__ = {"comment": "操作日志（只增不改）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=True, comment="操作人ID")
    user_name: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="操作人姓名（冗余）")
    module: Mapped[str] = mapped_column(String(32), nullable=False, comment="业务模块，如 商品/销售")
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="操作类型，如 新增/修改/删除/审核")
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="目标对象类型")
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="目标对象ID")
    before_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="操作前数据快照")
    after_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="操作后数据快照")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, comment="来源IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="客户端标识")
    cost_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时（毫秒）")
    result: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="结果 1成功 0失败")
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="操作时间")


class SysLoginLog(Base):
    """登录日志（sys_login_log）。"""

    __tablename__ = "sys_login_log"
    __table_args__ = {"comment": "登录日志"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    username: Mapped[str] = mapped_column(String(32), nullable=False, comment="登录账号")
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="RESTRICT"), nullable=True, comment="用户ID（登录成功时）")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, comment="来源IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="客户端标识")
    result: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="结果 1成功 0失败")
    fail_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="登录时间")


class SysConfig(Base):
    """系统参数配置（sys_config）。"""

    __tablename__ = "sys_config"
    __table_args__ = (
        UniqueConstraint("config_key", name="uk_config_key"),
        {"comment": "系统参数配置"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="参数编码，如 pos.auto_round")
    config_value: Mapped[str] = mapped_column(String(255), nullable=False, comment="参数值")
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'string'"), comment="值类型 string/int/decimal/bool/json")
    group_name: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="参数分组，如 收银/库存/会员")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="参数说明")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="最后修改人")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SysDict(Base):
    """数据字典（sys_dict）。"""

    __tablename__ = "sys_dict"
    __table_args__ = (
        UniqueConstraint("dict_type", "item_key", name="uk_dict_item"),
        {"comment": "数据字典"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="字典ID")
    dict_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="字典类型，如 pay_method")
    item_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="字典项键")
    item_value: Mapped[str] = mapped_column(String(128), nullable=False, comment="字典项值")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="排序")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="状态 1启用 0停用")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")


class SysCaptcha(Base):
    """图形验证码（sys_captcha）。

    ⚠️ 无 Redis 部署下的替代存储（决策 10）：验证码落表 + expire_at + APScheduler 清理。
    """

    __tablename__ = "sys_captcha"
    __table_args__ = (
        UniqueConstraint("captcha_key", name="uk_captcha_key"),
        {"comment": "图形验证码（无 Redis 部署的替代存储）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    captcha_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="验证码标识（UUID，前端原样回传）")
    captcha_code: Mapped[str] = mapped_column(String(8), nullable=False, comment="验证码内容")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, comment="请求来源IP（防刷）")
    used: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"), comment="是否已使用 1是 0否（用过即失效）")
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="过期时间（建议签发后 2 分钟）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")


class SysBackup(Base):
    """数据库备份记录（sys_backup）。

    ⚠️ 接口输出字段名 `size` 对应表列 `file_size`（后端序列化时改名）。
    """

    __tablename__ = "sys_backup"
    __table_args__ = (
        UniqueConstraint("file_name", name="uk_file_name"),
        {"comment": "数据库备份记录"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="备份记录ID")
    file_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="备份文件名，如 backup_20260915_023000.sql.gz")
    file_path: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备份文件存放路径")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), comment="文件大小（字节，接口输出为 size）")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'MANUAL'"), comment="触发方式 MANUAL手动/AUTO自动")
    backup_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'FULL'"), comment="备份类型 FULL全量/INCR增量")
    result: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"), comment="结果 1成功 0失败")
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="失败原因")
    cost_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="备份耗时（毫秒）")
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近一次被用于恢复的时间")
    operator: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="备份时间")


class SysNoSeq(Base):
    """单号流水序列（sys_no_seq）—— 阶段 1 新增。

    ⚠️ 关键设计（脚本注释也强调了）：
    1. 复合主键 (seq_key, seq_date)，**不加 AUTO_INCREMENT 列**
       原因：MySQL 的 LAST_INSERT_ID(expr) 惯用法在「首次插入」路径上会返回自增 ID，
       导致新键第一次取号得到错误的巨大值；去掉自增列才能让两条路径返回同一语义。
    2. 取号 SQL（见 app/core/sequence.py）：
         INSERT INTO sys_no_seq (seq_key, seq_date, current_val)
         VALUES (:k, :d, LAST_INSERT_ID(1))
         ON DUPLICATE KEY UPDATE current_val = LAST_INSERT_ID(current_val + 1);
         SELECT LAST_INSERT_ID();
       ⛔ 两条语句必须在同一 Session/Connection 上连续执行，LAST_INSERT_ID() 是会话级值。
    3. seq_date 规则：按日作用域的键用当天日期；P/S 全局键固定用 1970-01-01。
    """

    __tablename__ = "sys_no_seq"
    __table_args__ = {"comment": "单号流水序列（并发安全，禁止 SELECT MAX+1）"}

    # ⛔ 复合主键，无 id 列，无 AUTO_INCREMENT
    seq_key: Mapped[str] = mapped_column(String(32), primary_key=True, comment="业务键 XS/CG/SH/CT/FK/PD/BS/DB/JB/CZ/GD/P/S/M/BATCH")
    seq_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="日期分区；不按日重置的键（P/S）固定 1970-01-01")
    # INT UNSIGNED：单日单键最多 42 亿号，业务侧再按 key 的宽度限制（如 4 位 → 9999）
    current_val: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="当前已分配的最大流水号")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
