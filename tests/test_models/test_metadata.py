"""Base.metadata 自身的一致性测试（阶段 1 交付物）。

⚠️ 这些测试**不依赖数据库**（只查 Base.metadata），可以在无 DB 环境下运行。
   与 scripts/check_models.py 的分工：
   - check_models.py：模型 ↔ 真实数据库 的双向比对（集成级）
   - 本文件：模型自身元数据的断言（单元级）
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import UniqueConstraint

# ⚠️ 关键：import app.models 触发全部 62 张表模型注册到 Base.metadata
import app.models  # noqa: F401
from app.core.database import Base


class TestMetadataCompleteness:
    """测试 Base.metadata 是否包含全部 62 张表。"""

    def test_metadata_has_62_tables(self) -> None:
        """Base.metadata 必须注册 62 张表（61 原有 + sys_no_seq）。"""
        assert len(Base.metadata.tables) == 62

    def test_tables_split_by_8_domains(self) -> None:
        """表名按 8 个数据域前缀划分（base/sys/prd/pur/inv/mem/pro/sal/bi = 9 前缀）。

        实际是 9 个前缀（mem 和 pro 分开算），但归属"8 个数据域"
        —— mem/pro 同属"会员与促销域"。
        """
        prefix_counter: Counter[str] = Counter()
        for table_name in Base.metadata.tables:
            prefix = table_name.split("_", 1)[0]
            prefix_counter[prefix] += 1

        # 按 db/01_schema.sql 尾注：base 4 + sys 12 + prd 7 + pur 9 + inv 9
        # + mem/pro 10 + sal 7 + bi 4 = 62
        assert prefix_counter["base"] == 4, f"base_ 域应 4 张，实际 {prefix_counter['base']}"
        sys_count = prefix_counter["sys"]
        assert sys_count == 12, f"sys_ 域应 12 张（含 sys_no_seq），实际 {sys_count}"
        assert prefix_counter["prd"] == 7
        assert prefix_counter["pur"] == 9
        assert prefix_counter["inv"] == 9
        assert prefix_counter["mem"] + prefix_counter["pro"] == 10
        assert prefix_counter["sal"] == 7
        assert prefix_counter["bi"] == 4
        # 全部前缀必须都在白名单内
        allowed_prefixes = {"base", "sys", "prd", "pur", "inv", "mem", "pro", "sal", "bi"}
        assert set(prefix_counter) == allowed_prefixes

    def test_sys_no_seq_registered(self) -> None:
        """阶段 1 新增的 sys_no_seq 必须已注册。"""
        assert "sys_no_seq" in Base.metadata.tables

    def test_all_tables_have_comment(self) -> None:
        """所有表都必须有 comment（与 db/01_schema.sql 保持一致）。"""
        for name, table in Base.metadata.tables.items():
            assert table.comment, f"表 {name} 缺少 comment"


class TestForeignKeys:
    """测试外键声明。"""

    def test_total_foreign_keys_is_86(self) -> None:
        """Base.metadata 中的外键总数必须是 86 条（与 db/01_schema.sql 一致）。"""
        fk_count = 0
        for table in Base.metadata.tables.values():
            for column in table.columns:
                fk_count += len(column.foreign_keys)
        assert fk_count == 86, f"外键总数应 86，实际 {fk_count}"

    def test_all_fks_are_restrict(self) -> None:
        """所有外键必须声明 ondelete='RESTRICT'（与脚本一致）。"""
        for table in Base.metadata.tables.values():
            for constraint in table.constraints:
                if constraint.__class__.__name__ == "ForeignKeyConstraint":
                    assert constraint.ondelete == "RESTRICT", (
                        f"{table.name}.{constraint.name} 的 ondelete 应为 RESTRICT，"
                        f"实际 {constraint.ondelete}"
                    )

    def test_base_store_manager_id_has_no_fk(self) -> None:
        """⚠️ base_store.manager_id 必须**不**声明外键。

        脚本注释明说"避免与 sys_user 循环依赖" —— 这是刻意的设计，
        若模型误加 FK 会与 sys_user.store_id → base_store.id 形成循环，
        阶段 1 之后的建表/迁移都会失败。
        """
        table = Base.metadata.tables["base_store"]
        manager_id_col = table.columns["manager_id"]
        assert len(manager_id_col.foreign_keys) == 0, (
            "base_store.manager_id 不应有外键（脚本注释：避免与 sys_user 循环依赖）"
        )

    def test_known_circular_avoidance_fks(self) -> None:
        """抽查几处"故意不加 FK"的字段（脚本未声明，模型也必须不声明）。"""
        no_fk_columns = [
            ("pur_return", "source_receipt_id"),
            ("pur_return", "store_id"),
            ("pur_receipt", "store_id"),
            ("inv_loss_item", "batch_id"),
            ("sal_trade_item", "batch_id"),
            ("sal_return_item", "batch_id"),
            ("sal_hold", "member_id"),
            ("pro_coupon_record", "used_trade_id"),
            ("pro_promotion", "source_rule_id"),
            ("pro_promotion_item", "target_id"),
        ]
        for table_name, col_name in no_fk_columns:
            col = Base.metadata.tables[table_name].columns[col_name]
            assert len(col.foreign_keys) == 0, (
                f"{table_name}.{col_name} 不应有外键（脚本未声明）"
            )


class TestUniqueConstraints:
    """测试唯一约束声明（业务代码靠这些兜住 IntegrityError）。"""

    def _collect_uk_columns(self, table_name: str) -> list[tuple[str, ...]]:
        """收集一张表所有 UniqueConstraint 的字段元组（含 Column.unique=True 的隐式唯一）。"""
        table = Base.metadata.tables[table_name]
        result: list[tuple[str, ...]] = []
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                result.append(tuple(c.name for c in constraint.columns))
        for col in table.columns:
            if col.unique:
                result.append((col.name,))
        return result

    def test_sys_user_uk_username(self) -> None:
        """sys_user.username 必须唯一（阶段 2 登录靠此）。"""
        assert ("username",) in self._collect_uk_columns("sys_user")

    def test_sal_trade_uk_request_id(self) -> None:
        """sal_trade.request_id 必须唯一（幂等键兜底）。"""
        assert ("request_id",) in self._collect_uk_columns("sal_trade")

    def test_mem_balance_flow_uk_request_id(self) -> None:
        """⭐ 阶段 1 新增：mem_balance_flow.request_id 必须唯一（充值幂等）。"""
        assert ("request_id",) in self._collect_uk_columns("mem_balance_flow")

    def test_prd_barcode_uk_barcode(self) -> None:
        """prd_barcode.barcode 必须唯一（收银扫码入口，错误码 2002）。"""
        assert ("barcode",) in self._collect_uk_columns("prd_barcode")

    def test_mem_member_uk_phone(self) -> None:
        """mem_member.phone 必须唯一（错误码 9001）。"""
        assert ("phone",) in self._collect_uk_columns("mem_member")

    def test_inv_stock_composite_uk(self) -> None:
        """inv_stock (store_id, product_id) 复合唯一键（顺序必须一致）。"""
        uks = self._collect_uk_columns("inv_stock")
        assert ("store_id", "product_id") in uks

    def test_sys_no_seq_composite_pk(self) -> None:
        """sys_no_seq 复合主键 (seq_key, seq_date)，且不含 id 列。"""
        table = Base.metadata.tables["sys_no_seq"]
        pk_cols = [c.name for c in table.primary_key.columns]
        assert pk_cols == ["seq_key", "seq_date"]
        assert "id" not in table.columns, "sys_no_seq 不应有 id 列（LAST_INSERT_ID 会返回错误值）"

    def test_total_unique_constraints_42(self) -> None:
        """全库唯一键总数应为 42 个（含阶段 1 新增的 mem_balance_flow.uk_request_id）。

        与 scripts/check_models.py 的口径一致：只统计 UniqueConstraint + Column.unique=True，
        不含主键。
        """
        total = 0
        for table in Base.metadata.tables.values():
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    total += 1
            for col in table.columns:
                if col.unique:
                    total += 1
        assert total == 42, f"唯一键总数应 42，实际 {total}"


class TestServerDefaults:
    """测试关键默认值声明（created_at/updated_at 必须走 DB 端）。"""

    def test_created_at_uses_server_default(self) -> None:
        """所有 created_at 字段必须声明 server_default=CURRENT_TIMESTAMP。

        ⛔ 不用 Python 侧 default=datetime.now —— 会覆盖 DB 行为、时区口径混乱。
        """
        for name, table in Base.metadata.tables.items():
            if "created_at" in table.columns:
                col = table.columns["created_at"]
                assert col.server_default is not None, (
                    f"{name}.created_at 缺少 server_default"
                )
                # 检查是否包含 CURRENT_TIMESTAMP
                # ⚠️ server_default.arg 是 TextClause，不能用 truthiness判断（会报
                # "Boolean value of this clause is not defined"），必须 is not None
                sd_arg = col.server_default.arg
                sd_text = str(sd_arg).upper() if sd_arg is not None else ""
                assert "CURRENT_TIMESTAMP" in sd_text, (
                    f"{name}.created_at 的 server_default 应为 CURRENT_TIMESTAMP，实际 {sd_text}"
                )

    def test_no_python_side_datetime_default(self) -> None:
        """任何 datetime 字段都不应用 Python 侧 default=datetime.now / date.today。

        只允许 server_default（DB 端）。
        """
        import datetime as _dt

        for name, table in Base.metadata.tables.items():
            for col in table.columns:
                if col.default is not None:
                    # 排除 server_default（那不是 Python 侧 default）
                    arg = getattr(col.default, "arg", None)
                    assert not callable(arg) or arg not in (_dt.datetime.now, _dt.date.today), (
                        f"{name}.{col.name} 不应用 Python 侧 default=datetime.now/date.today"
                    )
