"""Alembic 环境配置（阶段 1 定制）。

关键设计
--------
1. **不使用 alembic.ini 的 sqlalchemy.url**：改由 app.core.config.settings 读取，
   避免密码硬编码进入版本控制。
2. **target_metadata = Base.metadata**：import app.models 触发全部 62 张表模型注册。
3. **同步驱动**：与阶段 0 决策 1 一致（PyMySQL，不用 asyncmy）。
4. **不 autogenerate 反向建表**：结构由 db/01_schema.sql 唯一维护，
   本 env.py 只支持后续的**增量迁移**（阶段 1 之后）。

⚠️ 基线策略（决策 12）
--------------------
01_schema.sql + 02_init_data.sql 建库之后执行：
    alembic stamp head
把数据库标记到基线版本（versions/0001_baseline.py，upgrade/downgrade 都是 pass），
之后的表结构变更才通过 alembic revision --autogenerate 生成迁移。
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  # 触发全部模型注册
from alembic import context

# ⚠️ 关键：import app.models 触发所有 62 张表的模型注册到 Base.metadata
# 若不 import，Base.metadata.tables 是空的，autogenerate 会误判"所有表都要删除"
from app.core.config import settings
from app.core.database import Base

# Alembic Config 对象，提供对 .ini 文件中值的访问
config = context.config

# 动态注入数据库 URL（覆盖 alembic.ini 中的占位符），密码不进版本控制
config.set_main_option("sqlalchemy.url", settings.database_url)

# 解释 .ini 中的日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标元数据：用于 autogenerate 支持
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """以 'offline' 模式运行迁移。

    仅用 URL 配置 context，不需要 Engine；跳过 Engine 创建也就不需要 DBAPI 可用。
    context.execute() 会直接把 SQL 字符串输出到脚本。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,       # 检测字段类型变更（阶段 1 之后有用）
        compare_server_default=True,  # 检测默认值变更
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """以 'online' 模式运行迁移 —— 需要创建 Engine 并关联 connection 到 context。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
