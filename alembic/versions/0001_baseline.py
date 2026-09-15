"""0001 baseline

结构由 db/01_schema.sql + db/02_init_data.sql 维护，本基线**不执行任何 DDL**，
仅用于标记当前数据库状态。

使用方法：
    1. 先跑 db/01_schema.sql 与 db/02_init_data.sql 建库
    2. 执行 `alembic stamp head` 把数据库标记到本基线
    3. 之后的表结构变更才用 `alembic revision --autogenerate` 生成迁移

⚠️ 决策 12（docs/07 第 2.3 节）：
   ⛔ 不要用 `alembic revision --autogenerate` 反向生成 61 张表的建表脚本
   —— 那会与 01_schema.sql 在索引名、注释、外键顺序上全面冲突。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-15

"""
from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """基线版本：不执行任何 DDL。

    结构由 db/01_schema.sql 唯一维护，本迁移仅作为 alembic 版本历史的起点。
    """
    pass


def downgrade() -> None:
    """基线版本回滚：不执行任何 DDL。

    ⛔ 不要在此处 DROP 全部表 —— 那会摧毁整个数据库。
    若真需要清空，直接跑 db/01_schema.sql（含 DROP TABLE IF EXISTS）。
    """
    pass
