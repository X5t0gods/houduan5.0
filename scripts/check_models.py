"""模型 ↔ 数据库结构一致性校验（阶段 1 交付物）。

设计原则
--------
1. **以真实数据库为基准**：db/01_schema.sql 建出的库是唯一真相源，
   Base.metadata 是待验证方；两者双向比对。
2. **类型比对走显式白名单**（TYPE_WHITELIST），白名单外的类型直接 ERROR，
   不自动放过。这是"62 张表一列不错"的最后一道防线。
3. **退出码语义**：ERROR > 0 → exit(1)（CI 可阻断），仅 WARNING → exit(0)。

用法
----
    python -m scripts.check_models            # 摘要模式
    python -m scripts.check_models --verbose  # 逐表明细

比对维度
--------
- 表集合：缺失 / 多余
- 每表字段：缺失 / 多余 / 类型 / 可空性 / 默认值存在性
- 主键：字段集合与顺序
- 唯一键：字段集合（含复合唯一键的顺序）
- 外键：数量 + 每条源字段→目标表.字段

不比对
------
- 普通索引（KEY 非 UNIQUE）：属性能优化，差异归 WARNING
- 表 / 字段的 COMMENT：仅信息，差异归 WARNING
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Windows PowerShell / cmd 默认 GBK，强制将标准输出重配为 UTF-8 避免中文乱码与 emoji 崩溃
# （Python 3.7+ 支持 reconfigure）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        # 如果 reconfigure 失败（极少数环境），不阻断主流程，只是可能中文乱码
        pass

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Time,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.database import Base, engine

# 触发全部模型注册；否则 Base.metadata 是空的
# 阶段 1 交付物：app/models/__init__.py 会汇总导入所有域的模型
try:
    import app.models  # noqa: F401
except ImportError as e:  # pragma: no cover
    print(f"[check_models] 无法导入 app.models：{e}", file=sys.stderr)


# 排除列表：工具链自己维护的元数据表，不属于业务模型范畴
# - alembic_version：Alembic 版本跟踪表（`alembic stamp head` 后自动创建）
EXCLUDED_TABLES: set[str] = {"alembic_version"}


# ---------- 类型白名单 ----------
# MySQL information_schema.columns.DATA_TYPE（小写） → 允许的 SQLAlchemy 类型
# 为什么 TINYINT → SmallInteger：接口约定 tinyint 输出 number（0/1）不是 bool，
# 用 SmallInteger 避免 SQLAlchemy 把 TINYINT(1) 自动识别成 Boolean。
TYPE_WHITELIST: dict[str, tuple[type, ...]] = {
    "bigint": (BigInteger, Integer),
    "int": (Integer, BigInteger),
    "mediumint": (Integer,),
    "smallint": (SmallInteger, Integer),
    "tinyint": (SmallInteger, Integer),
    "decimal": (Numeric,),
    "varchar": (String,),
    "char": (String,),
    "text": (String,),
    "mediumtext": (String,),
    "longtext": (String,),
    "datetime": (DateTime,),
    "timestamp": (DateTime,),
    "date": (Date,),
    "time": (Time,),
    "json": (JSON,),
}


@dataclass
class Issue:
    """单条差异记录。"""

    level: str  # "ERROR" | "WARNING"
    table: str
    message: str
    column: str | None = None


@dataclass
class Report:
    """校验报告容器。"""

    issues: list[Issue] = field(default_factory=list)
    tables_db: set[str] = field(default_factory=set)
    tables_model: set[str] = field(default_factory=set)
    columns_total: int = 0
    columns_matched: int = 0
    fk_total: int = 0
    fk_matched: int = 0
    uk_total: int = 0
    uk_matched: int = 0

    def add_error(self, table: str, message: str, column: str | None = None) -> None:
        """记录一条 ERROR。"""
        self.issues.append(Issue("ERROR", table, message, column))

    def add_warning(self, table: str, message: str, column: str | None = None) -> None:
        """记录一条 WARNING。"""
        self.issues.append(Issue("WARNING", table, message, column))

    @property
    def error_count(self) -> int:
        """ERROR 总数。"""
        return sum(1 for i in self.issues if i.level == "ERROR")

    @property
    def warning_count(self) -> int:
        """WARNING 总数。"""
        return sum(1 for i in self.issues if i.level == "WARNING")


# ---------- 数据库侧读取 ----------


def _preflight_check(eng: Engine) -> str | None:
    """前置检查：数据库是否存在、是否可连接。

    Returns:
        None 表示通过；否则返回友好错误消息（不抛裸异常）。
    """
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT SCHEMA_NAME FROM information_schema.schemata "
                     "WHERE SCHEMA_NAME = :name"),
                {"name": settings.DB_NAME},
            ).fetchone()
            if row is None:
                return (
                    f"未找到数据库 `{settings.DB_NAME}`。\n"
                    f"请先执行：\n"
                    f"  mysql -u root -p < db/01_schema.sql\n"
                    f"  mysql -u root -p {settings.DB_NAME} < db/02_init_data.sql"
                )
            table_count = conn.execute(
                text("SELECT COUNT(*) FROM information_schema.tables "
                     "WHERE table_schema = :name"),
                {"name": settings.DB_NAME},
            ).scalar()
            if table_count == 0:
                return (
                    f"数据库 `{settings.DB_NAME}` 存在但没有任何表。\n"
                    f"请先执行 db/01_schema.sql 建表。"
                )
        return None
    except OperationalError as e:
        return (
            f"无法连接 MySQL（{settings.DB_HOST}:{settings.DB_PORT}）：{e.orig}\n"
            f"请检查 .env 的 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD 是否正确，"
            f"以及 MySQL 服务是否已启动。"
        )


def fetch_db_tables(eng: Engine, schema: str) -> set[str]:
    """读取数据库中的所有表名（自动排除工具链元数据表）。"""
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables "
                 "WHERE table_schema = :s AND table_type = 'BASE TABLE'"),
            {"s": schema},
        ).fetchall()
    return {r[0] for r in rows if r[0] not in EXCLUDED_TABLES}


def fetch_db_columns(eng: Engine, schema: str, table: str) -> dict[str, dict[str, Any]]:
    """读取指定表的所有字段元信息。

    Returns:
        {column_name: {data_type, char_len, num_precision, num_scale,
                       is_nullable, column_default, extra}}
    """
    sql = text("""
        SELECT column_name, data_type, character_maximum_length,
               numeric_precision, numeric_scale, is_nullable,
               column_default, extra
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t
        ORDER BY ordinal_position
    """)
    with eng.connect() as conn:
        rows = conn.execute(sql, {"s": schema, "t": table}).fetchall()
    return {
        r[0]: {
            "data_type": (r[1] or "").lower(),
            "char_len": r[2],
            "num_precision": r[3],
            "num_scale": r[4],
            "is_nullable": r[5] == "YES",
            "column_default": r[6],
            "extra": r[7] or "",
        }
        for r in rows
    }


def fetch_db_pk(eng: Engine, schema: str, table: str) -> list[str]:
    """读取主键字段列表（按顺序）。"""
    sql = text("""
        SELECT column_name FROM information_schema.key_column_usage
        WHERE table_schema = :s AND table_name = :t AND constraint_name = 'PRIMARY'
        ORDER BY ordinal_position
    """)
    with eng.connect() as conn:
        rows = conn.execute(sql, {"s": schema, "t": table}).fetchall()
    return [r[0] for r in rows]


def fetch_db_unique_keys(eng: Engine, schema: str, table: str) -> dict[str, list[str]]:
    """读取所有唯一键（不含主键）。

    Returns:
        {index_name: [column_names in order]}
    """
    sql = text("""
        SELECT index_name, column_name, seq_in_index
        FROM information_schema.statistics
        WHERE table_schema = :s AND table_name = :t AND non_unique = 0
              AND index_name <> 'PRIMARY'
        ORDER BY index_name, seq_in_index
    """)
    with eng.connect() as conn:
        rows = conn.execute(sql, {"s": schema, "t": table}).fetchall()
    result: dict[str, list[str]] = defaultdict(list)
    for index_name, column_name, _seq in rows:
        result[index_name].append(column_name)
    return dict(result)


def fetch_db_foreign_keys(eng: Engine, schema: str, table: str) -> list[dict[str, Any]]:
    """读取指定表的所有外键。

    Returns:
        [{name, column, ref_table, ref_column}]
    """
    sql = text("""
        SELECT constraint_name, column_name, referenced_table_name, referenced_column_name
        FROM information_schema.key_column_usage
        WHERE table_schema = :s AND table_name = :t
              AND referenced_table_name IS NOT NULL
        ORDER BY constraint_name
    """)
    with eng.connect() as conn:
        rows = conn.execute(sql, {"s": schema, "t": table}).fetchall()
    return [
        {"name": r[0], "column": r[1], "ref_table": r[2], "ref_column": r[3]}
        for r in rows
    ]


# ---------- 类型比对 ----------


def _normalize_default(value: Any) -> str | None:
    """归一化默认值以便比较。

    - None → None
    - 字符串两端引号剥掉（MySQL 返回 'SELF' 会带引号）
    - 关键字统一大写（CURRENT_TIMESTAMP）
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.upper() in ("NULL", "NONE"):
        return None
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        s = s[1:-1]
    if s.upper() == "CURRENT_TIMESTAMP":
        return "CURRENT_TIMESTAMP"
    return s


def _model_server_default(col: Any) -> str | None:
    """从 SQLAlchemy Column 提取 server_default 的字符串形式。"""
    sd = col.server_default
    if sd is None:
        return None
    arg = getattr(sd, "arg", None)
    if arg is None:
        return str(sd)
    if hasattr(arg, "text"):
        return _normalize_default(arg.text)
    return _normalize_default(arg)


def check_column_type(
    report: Report,
    table: str,
    col_name: str,
    db_meta: dict[str, Any],
    model_col: Any,
) -> None:
    """比对单个字段的类型、可空性、默认值。"""
    db_type = db_meta["data_type"]
    model_type = model_col.type

    # 1. 类型白名单检查
    if db_type not in TYPE_WHITELIST:
        report.add_error(
            table,
            f"字段 `{col_name}` 的 MySQL 类型 `{db_type}` 不在白名单中，请人工确认映射",
            col_name,
        )
        return

    allowed = TYPE_WHITELIST[db_type]
    if not isinstance(model_type, allowed):
        expected = tuple(t.__name__ for t in allowed)
        report.add_error(
            table,
            f"字段 `{col_name}` 类型不匹配：DB={db_type} → 期望 {expected}，"
            f"模型={type(model_type).__name__}",
            col_name,
        )
        return

    # 2. DECIMAL 精度必须完全一致
    if db_type == "decimal":
        db_p, db_s = db_meta["num_precision"], db_meta["num_scale"]
        m_p = getattr(model_type, "precision", None)
        m_s = getattr(model_type, "scale", None)
        if (db_p, db_s) != (m_p, m_s):
            report.add_error(
                table,
                f"字段 `{col_name}` DECIMAL 精度不一致：DB=({db_p},{db_s})，模型=({m_p},{m_s})",
                col_name,
            )

    # 3. VARCHAR/CHAR 长度必须一致
    if db_type in ("varchar", "char"):
        db_len = db_meta["char_len"]
        m_len = getattr(model_type, "length", None)
        if db_len != m_len:
            report.add_error(
                table,
                f"字段 `{col_name}` 长度不一致：DB=VARCHAR({db_len})，模型=String({m_len})",
                col_name,
            )

    # 4. 可空性必须一致
    db_nullable = db_meta["is_nullable"]
    model_nullable = bool(model_col.nullable)
    if db_nullable != model_nullable:
        report.add_error(
            table,
            f"字段 `{col_name}` 可空性不一致：DB={'NULL' if db_nullable else 'NOT NULL'}，"
            f"模型 nullable={model_nullable}",
            col_name,
        )

    # 5. 默认值存在性
    db_default = _normalize_default(db_meta["column_default"])
    model_default = _model_server_default(model_col)
    # auto_increment 字段不需要 server_default
    if "auto_increment" in db_meta["extra"].lower():
        return
    if db_default is not None and model_default is None:
        report.add_error(
            table,
            f"字段 `{col_name}` DB 有默认值 `{db_default}`，模型未声明 server_default",
            col_name,
        )
    elif db_default is None and model_default is not None:
        report.add_warning(
            table,
            f"字段 `{col_name}` DB 无默认值，模型却声明了 server_default=`{model_default}`",
            col_name,
        )
    elif (
        db_default is not None
        and model_default is not None
        and db_default.upper() != model_default.upper()
    ):
        report.add_warning(
            table,
            f"字段 `{col_name}` 默认值不一致：DB=`{db_default}`，模型=`{model_default}`",
            col_name,
        )


# ---------- 表级比对 ----------


def check_table(report: Report, eng: Engine, schema: str, table_name: str) -> None:
    """对单张表做完整比对（字段/主键/唯一键/外键）。"""
    model_table = Base.metadata.tables.get(table_name)
    if model_table is None:
        report.add_error(table_name, "模型缺失：数据库有此表但 Base.metadata 中没有")
        return

    db_cols = fetch_db_columns(eng, schema, table_name)
    model_cols = {c.name: c for c in model_table.columns}

    # 字段缺失/多余
    missing_in_model = set(db_cols) - set(model_cols)
    extra_in_model = set(model_cols) - set(db_cols)
    for name in sorted(missing_in_model):
        report.add_error(table_name, f"字段缺失：DB 有 `{name}`，模型未声明", name)
    for name in sorted(extra_in_model):
        report.add_error(table_name, f"字段多余：模型有 `{name}`，DB 中不存在", name)

    # 字段类型 / 可空 / 默认值
    for name in sorted(set(db_cols) & set(model_cols)):
        report.columns_total += 1
        before = report.error_count
        check_column_type(report, table_name, name, db_cols[name], model_cols[name])
        if report.error_count == before:
            report.columns_matched += 1

    # 主键比对（字段集合与顺序）
    db_pk = fetch_db_pk(eng, schema, table_name)
    model_pk = [c.name for c in model_table.primary_key.columns]
    if db_pk != model_pk:
        report.add_error(
            table_name,
            f"主键不一致：DB={db_pk}，模型={model_pk}",
        )

    # 唯一键比对（含复合唯一键顺序）
    db_uks = fetch_db_unique_keys(eng, schema, table_name)
    model_uks: dict[str, list[str]] = {}
    for constraint in model_table.constraints:
        if constraint.__class__.__name__ == "UniqueConstraint":
            cols = [c.name for c in constraint.columns]
            name = constraint.name or f"_anon_{'_'.join(cols)}"
            model_uks[name] = cols
    # 也接受 Column(unique=True) 声明的单列唯一键
    for col in model_table.columns:
        if col.unique and col.name not in {c for cols in model_uks.values() for c in cols}:
            model_uks[f"_col_{col.name}"] = [col.name]

    report.uk_total += len(db_uks)
    # 按"字段集合"匹配（名字可不同，因为 SQLAlchemy 允许不命名）
    db_uk_sets = {tuple(cols): name for name, cols in db_uks.items()}
    model_uk_sets = {tuple(cols): name for name, cols in model_uks.items()}
    for cols_tuple, db_name in db_uk_sets.items():
        if cols_tuple in model_uk_sets:
            report.uk_matched += 1
        else:
            report.add_error(
                table_name,
                f"唯一键 `{db_name}` 缺失：DB 有 UNIQUE{list(cols_tuple)}，模型未声明",
            )
    for cols_tuple, model_name in model_uk_sets.items():
        if cols_tuple not in db_uk_sets:
            report.add_error(
                table_name,
                f"唯一键多余：模型声明 `{model_name}` UNIQUE{list(cols_tuple)}，DB 中不存在",
            )

    # 外键比对（数量 + 每条源→目标）
    db_fks = fetch_db_foreign_keys(eng, schema, table_name)
    model_fks: list[dict[str, str]] = []
    for col in model_table.columns:
        for fk in col.foreign_keys:
            # fk.target_fullname 形如 "table.column"
            target = fk.target_fullname
            if "." in target:
                ref_table, ref_col = target.split(".", 1)
                model_fks.append(
                    {"column": col.name, "ref_table": ref_table, "ref_column": ref_col}
                )

    report.fk_total += len(db_fks)
    db_fk_set = {(f["column"], f["ref_table"], f["ref_column"]) for f in db_fks}
    model_fk_set = {(f["column"], f["ref_table"], f["ref_column"]) for f in model_fks}
    for _fk_tuple in db_fk_set & model_fk_set:
        report.fk_matched += 1
    for fk_tuple in sorted(db_fk_set - model_fk_set):
        report.add_error(
            table_name,
            f"外键缺失：DB 有 `{fk_tuple[0]}` → `{fk_tuple[1]}.{fk_tuple[2]}`，模型未声明",
            fk_tuple[0],
        )
    for fk_tuple in sorted(model_fk_set - db_fk_set):
        report.add_error(
            table_name,
            f"外键多余：模型声明 `{fk_tuple[0]}` → `{fk_tuple[1]}.{fk_tuple[2]}`，DB 中不存在",
            fk_tuple[0],
        )


# ---------- 主入口 ----------


def run_check(verbose: bool = False) -> Report:
    """执行全库校验，返回报告。"""
    report = Report()

    # 前置检查
    err = _preflight_check(engine)
    if err:
        print(f"[check_models] {err}", file=sys.stderr)
        sys.exit(2)

    schema = settings.DB_NAME
    report.tables_db = fetch_db_tables(engine, schema)
    report.tables_model = set(Base.metadata.tables.keys())

    # 表集合差异
    missing = report.tables_db - report.tables_model
    extra = report.tables_model - report.tables_db
    for t in sorted(missing):
        report.add_error(t, "模型缺失：数据库有此表但 Base.metadata 中没有")
    for t in sorted(extra):
        report.add_error(t, "模型多余：Base.metadata 有此表但数据库中不存在")

    # 逐表比对（只对两边都存在的表做深入比对，避免重复报错）
    for t in sorted(report.tables_db & report.tables_model):
        check_table(report, engine, schema, t)

    return report


def print_report(report: Report, verbose: bool) -> None:
    """打印校验报告。"""
    print("=" * 78)
    print("模型 <-> 数据库 一致性校验")
    print("=" * 78)
    print(f"数据库：{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    print()

    # 摘要
    tables_matched = len(report.tables_db & report.tables_model)
    print("【摘要】")
    print(f"  表    ：{tables_matched}/{len(report.tables_db)} 一致"
          f"（模型侧 {len(report.tables_model)}）")
    print(f"  字段  ：{report.columns_matched}/{report.columns_total} 一致")
    print(f"  外键  ：{report.fk_matched}/{report.fk_total} 一致")
    print(f"  唯一键：{report.uk_matched}/{report.uk_total} 一致")
    print(f"  ERROR：{report.error_count}    WARNING：{report.warning_count}")
    print()

    # 明细
    if report.issues:
        # 按表分组
        by_table: dict[str, list[Issue]] = defaultdict(list)
        for issue in report.issues:
            by_table[issue.table].append(issue)

        if verbose:
            print("【明细】")
            for table in sorted(by_table):
                print(f"\n  > {table}")
                for issue in by_table[table]:
                    prefix = "[ERROR]" if issue.level == "ERROR" else "[WARN] "
                    col_hint = f"[{issue.column}] " if issue.column else ""
                    print(f"    {prefix} {col_hint}{issue.message}")
        else:
            # 摘要模式：只按表打印错误数量，最多显示前 20 张问题表
            print("【问题表 TOP 20】")
            problem_tables = sorted(
                by_table.items(),
                key=lambda kv: -sum(1 for i in kv[1] if i.level == "ERROR"),
            )[:20]
            for table, issues in problem_tables:
                errs = sum(1 for i in issues if i.level == "ERROR")
                warns = sum(1 for i in issues if i.level == "WARNING")
                first = issues[0].message
                print(f"  {table:32s} ERROR={errs:3d} WARN={warns:2d}  例：{first[:60]}")
            if len(by_table) > 20:
                print(f"  ... 另有 {len(by_table) - 20} 张问题表未列出，加 --verbose 查看全部")
        print()

    # 结论
    print("=" * 78)
    if report.error_count == 0:
        print(f"[OK] 校验通过（WARNING {report.warning_count} 条，不影响正确性）")
    else:
        print(f"[FAIL] 校验失败：ERROR {report.error_count} 条，WARNING {report.warning_count} 条")
    print("=" * 78)


def main() -> int:
    """命令行入口。

    Returns:
        0 = 通过（可能有 WARNING）；1 = 有 ERROR；2 = 前置检查失败
    """
    parser = argparse.ArgumentParser(
        prog="check_models",
        description="校验 SQLAlchemy 模型与真实数据库结构的一致性",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="打印每张表的完整差异明细（默认只打印 TOP 20 问题表）",
    )
    args = parser.parse_args()

    report = run_check(verbose=args.verbose)
    print_report(report, verbose=args.verbose)

    return 1 if report.error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
