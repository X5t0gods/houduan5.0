"""初始化 sys_no_seq 的 P 键（商品编码序列），使其对齐现有 prd_product 的最大编码。

⚠️ 为什么需要这个脚本（spec 1.4）
--------------------------------
- 商品编码格式 `P` + 6 位流水（如 P000001），走 `sys_no_seq`（seq_key='P'，全局作用域）
- **坑**：现有 `prd_product` 里已有 P000001-P000020（阶段 1 seed.py 又追加到 P000025），
  而 `sys_no_seq` 是新表（阶段 1 补的）、初始为空
- 若不初始化就直接取号，第一个新商品会拿到 `P000001`，**撞 uk_product_code 唯一键**

⚠️ 关于 `SELECT MAX(...)+1` 的取舍
----------------------------------
docs/03 8.2 节明令**禁止**用 `SELECT MAX(...)+1` 做**取号手段**（并发不安全）。
本脚本用 MAX 是**一次性初始化对齐**，只在部署/迁移时执行，**不参与运行时取号**，
不违反禁令。运行时取号仍由 `app/core/sequence.py::NoSeqService.next_no()`
用 `INSERT ... ON DUPLICATE KEY UPDATE current_val = LAST_INSERT_ID(current_val + 1)` 完成。

⚠️ 幂等性
--------
本脚本**可重复执行**：使用 `GREATEST(current_val, max_val)` 保证不回退已有计数器
（若已经取号到 P000030 而表里最大编码是 P000025，不会把计数器降回 25）。

用法：
    python -m scripts.init_product_seq
    python -m scripts.init_product_seq --dry-run   # 只打印不写库
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


# P 键的 seq_date 固定用 EPOCH_DATE（全局作用域，跨日不重置）
EPOCH_DATE = date(1970, 1, 1)


def get_max_product_seq(session) -> int:
    """查询 prd_product 里 `P` + 数字格式编码的最大流水号。

    Returns:
        最大流水号（如 25 表示现有 P000025）；无任何符合格式的编码时返 0。
    """
    result = session.execute(
        text(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(product_code, 2) AS UNSIGNED)), 0) "
            "FROM prd_product WHERE product_code REGEXP '^P[0-9]+$'"
        )
    ).scalar_one()
    return int(result or 0)


def get_current_seq_val(session) -> int | None:
    """查询 sys_no_seq 里 P 键的 current_val；不存在时返 None。"""
    row = session.execute(
        text(
            "SELECT current_val FROM sys_no_seq "
            "WHERE seq_key = 'P' AND seq_date = :d"
        ),
        {"d": EPOCH_DATE},
    ).fetchone()
    return int(row[0]) if row else None


def align_seq(session, max_val: int, *, dry_run: bool = False) -> int:
    """把 sys_no_seq 的 P 键对齐到 max_val（用 GREATEST 不回退）。

    Args:
        session: 数据库会话。
        max_val: 目标值（一般是现有 prd_product 的最大编码流水号）。
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
            "VALUES ('P', :d, :v) "
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
        prog="init_product_seq",
        description="把 sys_no_seq 的 P 键对齐到 prd_product 现有最大编码（可重复执行）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印将要执行的操作，不写库",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        print("=" * 66)
        print("sys_no_seq P 键初始化（商品编码序列）")
        print("=" * 66)

        before = get_current_seq_val(session)
        max_product = get_max_product_seq(session)
        before_display = before if before is not None else "(不存在)"
        print(f"  初始化前 sys_no_seq.P.current_val : {before_display}")
        print(f"  prd_product 现有最大编码流水号    : {max_product}")

        if max_product == 0:
            print("\n[skip] prd_product 表为空或没有 P+数字 格式的编码，无需初始化")
            return 0

        after = align_seq(session, max_product, dry_run=args.dry_run)
        mode = "[dry-run] 预期" if args.dry_run else "实际"
        print(f"\n  {mode}对齐后 sys_no_seq.P.current_val : {after}")
        print(f"  下一个新建商品的编码将是         : P{after + 1:06d}")
        print("=" * 66)
        return 0
    except Exception as e:  # noqa: BLE001
        session.rollback()
        print(f"[init_product_seq] 失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
