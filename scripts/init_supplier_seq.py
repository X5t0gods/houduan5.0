"""初始化 sys_no_seq 的 S 键（供应商编码序列），对齐现有 pur_supplier 最大编码。

⚠️ 为什么需要这个脚本（spec 2.3 的坑）
--------------------------------------
- 供应商编码格式 `S` + 6 位流水（如 S000001），走 `sys_no_seq`（seq_key='S'，全局作用域）
- **坑**：现有 `pur_supplier` 已有 S000001-S000005，但 `sys_no_seq` 里**根本没有 S 键**
  （阶段 6 实测确认：`SELECT * FROM sys_no_seq WHERE seq_key='S'` 返回空）
- 若不初始化就直接取号，第一个新供应商会拿到 `S000001`，**撞 uk_supplier_code 唯一键**

⚠️ 关于 `SELECT MAX(...)` 的取舍
--------------------------------
docs/03 8.2 节明令**禁止**用 `SELECT MAX(...)+1` 做**取号手段**（并发不安全）。
本脚本用 MAX 是**一次性初始化对齐**，只在部署/迁移时执行，**不参与运行时取号**，
不违反禁令。运行时取号仍由 `app/core/sequence.py::NoSeqService.next_no()` 完成。

⚠️ 幂等性：用 `GREATEST(current_val, max_val)` 保证不回退已有计数器，可重复执行。

用法：
    python -m scripts.init_supplier_seq
    python -m scripts.init_supplier_seq --dry-run   # 只打印不写库
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import date

from sqlalchemy import text

from app.core.database import SessionLocal

# Windows 控制台 GBK：与其他脚本一样强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


# S 键是全局作用域（跨日不重置），seq_date 固定用 EPOCH_DATE
EPOCH_DATE = date(1970, 1, 1)


def get_max_supplier_seq(session) -> int:
    """查询 pur_supplier 里 `S` + 数字格式编码的最大流水号。

    Returns:
        最大流水号（如 5 表示现有 S000005）；无任何符合格式的编码时返 0。
    """
    result = session.execute(
        text(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(supplier_code, 2) AS UNSIGNED)), 0) "
            "FROM pur_supplier WHERE supplier_code REGEXP '^S[0-9]+$'"
        )
    ).scalar_one()
    return int(result or 0)


def get_current_seq_val(session) -> int | None:
    """查询 sys_no_seq 里 S 键的 current_val；不存在时返 None。"""
    row = session.execute(
        text(
            "SELECT current_val FROM sys_no_seq "
            "WHERE seq_key = 'S' AND seq_date = :d"
        ),
        {"d": EPOCH_DATE},
    ).fetchone()
    return int(row[0]) if row else None


def align_seq(session, max_val: int, *, dry_run: bool = False) -> int:
    """把 sys_no_seq 的 S 键对齐到 max_val（用 GREATEST 不回退）。

    Args:
        session: 数据库会话。
        max_val: 目标值（一般是现有 pur_supplier 的最大编码流水号）。
        dry_run: True 时不写库，只返回预期结果。

    Returns:
        对齐后的 current_val（dry_run 时是预期值）。
    """
    current = get_current_seq_val(session)

    if dry_run:
        return max(current or 0, max_val)

    # UPSERT：不存在则插入 max_val，存在则取 GREATEST 避免回退
    session.execute(
        text(
            "INSERT INTO sys_no_seq (seq_key, seq_date, current_val) "
            "VALUES ('S', :d, :v) "
            "ON DUPLICATE KEY UPDATE current_val = GREATEST(current_val, :v)"
        ),
        {"d": EPOCH_DATE, "v": max_val},
    )
    session.commit()
    return get_current_seq_val(session) or max_val


def main() -> int:
    """命令行入口。

    Returns:
        0 = 成功；1 = 失败。
    """
    parser = argparse.ArgumentParser(
        prog="init_supplier_seq",
        description="把 sys_no_seq 的 S 键对齐到 pur_supplier 现有最大编码（可重复执行）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印将要执行的操作，不写库",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        print("=" * 66)
        print("sys_no_seq S 键初始化（供应商编码序列）")
        print("=" * 66)

        before = get_current_seq_val(session)
        max_supplier = get_max_supplier_seq(session)
        before_display = before if before is not None else "(不存在)"
        print(f"  初始化前 sys_no_seq.S.current_val : {before_display}")
        print(f"  pur_supplier 现有最大编码流水号   : {max_supplier}")

        if max_supplier == 0:
            print("\n[skip] pur_supplier 表为空或没有 S+数字 格式的编码，无需初始化")
            return 0

        after = align_seq(session, max_supplier, dry_run=args.dry_run)
        mode = "[dry-run] 预期" if args.dry_run else "实际"
        print(f"\n  {mode}对齐后 sys_no_seq.S.current_val : {after}")
        print(f"  下一个新建供应商的编码将是        : S{after + 1:06d}")
        print("=" * 66)
        return 0
    except Exception as e:  # noqa: BLE001
        session.rollback()
        print(f"[init_supplier_seq] 失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
