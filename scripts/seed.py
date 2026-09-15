"""造数脚本骨架（阶段 1 交付物）。

⚠️ 本阶段只做骨架，不做大规模造数 —— 真正的 BI 量级数据（5 万笔交易 / 3000 SKU /
   min_support=0.01 / ≤60s）属阶段 11 交付。

设计目标
--------
1. 能跑通、能打印计划、能写一批测试商品
2. 命令行参数完整（--products / --members / --trades / --days / --seed / --dry-run / --yes）
3. **默认 --dry-run**：不写库，只打印计划
4. 复用 app.core.database 的 Session，复用 app.core.sequence 生成单号
5. 幂等性说明：本骨架**不做幂等**（重复执行会重复写入），
   正式造数（阶段 11）需要先检查演示数据存在性再决定策略

用法：
    python -m scripts.seed --dry-run                    # 打印计划，不写库（默认）
    python -m scripts.seed --products 50 --yes          # 写 50 个商品，跳过确认
    python -m scripts.seed --seed 42 --products 100 --yes  # 可复现
"""

from __future__ import annotations

import argparse
import contextlib
import random
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.sequence import NoSeqService
from app.models.base_models import BaseUnit
from app.models.prd_models import PrdBarcode, PrdCategory, PrdProduct

# Windows 控制台 GBK：与 check_models.py 一样强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    # 极少数环境 reconfigure 可能失败，不阻断主流程，只是中文可能乱码
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


# 演示商品名池（阶段 11 会扩充到 3000 SKU）
_PRODUCT_NAMES: list[str] = [
    "上海青", "东北珍珠米", "乐事薯片原味", "可口可乐 330ml", "百事可乐 500ml",
    "农夫山泉 550ml", "怡宝矿泉水 555ml", "康师傅红烧牛肉面", "统一老坛酸菜面",
    "双汇火腿肠", "金锣火腿肠", "伊利纯牛奶 250ml", "蒙牛酸奶 100g",
    "光明鲜奶 950ml", "三元牛奶 227ml", "洽洽瓜子 108g", "卫龙辣条",
    "达利园蛋黄派", "徐福记沙琪玛", "奥利奥饼干",
]


@dataclass
class SeedPlan:
    """造数计划。"""

    products: int
    members: int
    trades: int
    days: int
    seed: int
    dry_run: bool


def print_plan(plan: SeedPlan) -> None:
    """打印造数计划（不写库）。

    Args:
        plan: 已解析的造数参数。
    """
    print("=" * 60)
    print("造数计划（阶段 1 骨架）")
    print("=" * 60)
    print(f"  商品数  ：{plan.products}")
    print(f"  会员数  ：{plan.members}   （本骨架未实现，阶段 7 补）")
    print(f"  交易数  ：{plan.trades}    （本骨架未实现，阶段 4 补）")
    print(f"  日期跨度：{plan.days} 天   （本骨架未实现，阶段 11 补）")
    print(f"  随机种子：{plan.seed}（可复现）")
    print(f"  模式    ：{'DRY-RUN（不写库）' if plan.dry_run else '实际写入'}")
    print(f"  数据库  ：{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    print("=" * 60)


def _fetch_base_units(session: Session) -> list[int]:
    """获取可用的单位 ID 列表（依赖 02_init_data.sql 已灌入）。"""
    result = session.execute(select(BaseUnit.id).where(BaseUnit.status == 1))
    return [row[0] for row in result.fetchall()]


def _fetch_leaf_categories(session: Session) -> list[int]:
    """获取可用的末级分类 ID 列表（level=3 或无子节点）。"""
    result = session.execute(
        select(PrdCategory.id).where(
            PrdCategory.status == 1,
            PrdCategory.level == 3,
        )
    )
    ids = [row[0] for row in result.fetchall()]
    # 如果没有三级分类，退化为所有启用的分类
    if not ids:
        result = session.execute(select(PrdCategory.id).where(PrdCategory.status == 1))
        ids = [row[0] for row in result.fetchall()]
    return ids


def _align_seq_with_existing(session: Session) -> None:
    """将 sys_no_seq 的 P 键计数器对齐到 prd_product 现有最大 product_code。

    为什么需要：02_init_data.sql 硬编码写入了 P000001-P000020，但没有同步
    sys_no_seq 计数器，导致下一次 next_no("P") 会从 P000001 开始，撑唱
    prd_product.uk_product_code 唯一索引。

    ⚠️ 这里用 SELECT MAX 仅为"初始化对齐"，不是取号手段（取号仍由
    NoSeqService.next_no() 用 LAST_INSERT_ID 原子自增完成），8.2 节的禁令不矛盾。

    采用 GREATEST(current_val, max_val) 避免回退已有计数器。
    """
    result = session.execute(
        text("SELECT MAX(CAST(SUBSTRING(product_code, 2) AS UNSIGNED)) FROM prd_product "
             "WHERE product_code REGEXP '^P[0-9]+$'")
    ).scalar()
    max_val = int(result or 0)
    if max_val <= 0:
        return
    # 将 P 键（全局作用域，seq_date=1970-01-01）对齐到 max_val
    session.execute(
        text("INSERT INTO sys_no_seq (seq_key, seq_date, current_val) "
             "VALUES ('P', '1970-01-01', :v) "
             "ON DUPLICATE KEY UPDATE current_val = GREATEST(current_val, :v)"),
        {"v": max_val},
    )
    session.commit()
    print(f"[seed] sys_no_seq.P 已对齐到现有最大商品编码（current_val = {max_val}）")


def seed_products(session: Session, count: int, rng: random.Random) -> int:
    """写入 count 个测试商品，返回实际写入数。

    使用 NoSeqService 生成 product_code，为每个商品配一个条码。

    Args:
        session: 数据库会话。
        count: 期望写入的商品数。
        rng: 已 seed 的随机数生成器（可复现）。

    Returns:
        实际写入的商品数（可能小于 count，如基础数据缺失）。
    """
    unit_ids = _fetch_base_units(session)
    category_ids = _fetch_leaf_categories(session)
    if not unit_ids or not category_ids:
        print("[seed] 基础数据缺失（单位或分类为空），请先跑 db/02_init_data.sql")
        return 0

    # ⭐ 先对齐 sys_no_seq 计数器，避免撞 02_init_data.sql 硬编码的 P000001-P000020
    _align_seq_with_existing(session)

    seq = NoSeqService(session)
    written = 0

    for i in range(count):
        product_code = seq.next_no("P")  # 全局递增，跨日不重置
        name = rng.choice(_PRODUCT_NAMES) + f" #{i + 1}"
        purchase_price = Decimal(str(rng.uniform(1.0, 50.0))).quantize(Decimal("0.01"))
        sale_price = (purchase_price * Decimal("1.3")).quantize(Decimal("0.01"))

        product = PrdProduct(
            product_code=product_code,
            product_name=name,
            category_id=rng.choice(category_ids),
            unit_id=rng.choice(unit_ids),
            purchase_price=purchase_price,
            sale_price=sale_price,
            avg_cost=purchase_price,
            status=1,
        )
        session.add(product)
        session.flush()  # 拿到 product.id 用于关联条码

        # 为商品配一个条码：69 前缀 + 11 位随机数（模拟 EAN-13）
        barcode = f"69{rng.randint(0, 99999999999):011d}"
        session.add(PrdBarcode(
            product_id=product.id,
            barcode=barcode,
            barcode_type=1,
            status=1,
        ))
        written += 1

    return written


def confirm_or_abort(plan: SeedPlan, assume_yes: bool) -> bool:
    """交互式确认；非交互环境（无 --yes）时给出明确提示。

    Args:
        plan: 造数计划。
        assume_yes: --yes 参数，跳过确认。

    Returns:
        True 继续执行，False 中止。
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("[seed] 非交互环境且未传 --yes，中止（避免 CI 卡住）")
        return False
    try:
        ans = input("确认写入以上数据？输入 yes 继续，其他任意键中止：").strip().lower()
        return ans == "yes"
    except (EOFError, KeyboardInterrupt):
        print("\n[seed] 用户取消")
        return False


def parse_args(argv: list[str] | None = None) -> SeedPlan & dict[str, Any]:  # type: ignore[valid-type]
    """解析命令行参数。

    Returns:
        (plan, extra_opts) 元组。
    """
    parser = argparse.ArgumentParser(
        prog="seed",
        description="造数脚本骨架（阶段 1，只实现商品；会员/交易属阶段 4/7/11）",
    )
    parser.add_argument("--products", type=int, default=20, help="生成商品数（默认 20）")
    parser.add_argument("--members", type=int, default=0, help="生成会员数（骨架未实现）")
    parser.add_argument("--trades", type=int, default=0, help="生成交易数（骨架未实现）")
    parser.add_argument("--days", type=int, default=1, help="交易日期跨度（骨架未实现）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，可复现）")
    # ⚠️ 默认 dry-run：必须显式 --no-dry-run 才写库，防误操作
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只打印计划不写库（默认开启）")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="关闭 dry-run，实际写库")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="跳过交互式确认（非交互环境必须传）")
    args = parser.parse_args(argv)

    plan = SeedPlan(
        products=args.products,
        members=args.members,
        trades=args.trades,
        days=args.days,
        seed=args.seed,
        dry_run=args.dry_run,
    )
    return plan, {"yes": args.yes}


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Returns:
        0 = 成功；1 = 用户取消或前置检查失败。
    """
    plan, opts = parse_args(argv)
    print_plan(plan)

    if plan.dry_run:
        print("\n[dry-run] 未写库。加 --no-dry-run --yes 才实际执行。")
        return 0

    if not confirm_or_abort(plan, opts["yes"]):
        return 1

    rng = random.Random(plan.seed)
    session = SessionLocal()
    try:
        # 前置检查：如果 prd_product 已有数据，提示但不阻断
        existing = session.execute(
            select(func.count()).select_from(PrdProduct)
        ).scalar_one()
        if existing > 0:
            print(f"[seed] 注意：prd_product 已有 {existing} 条记录，本次会追加（不幂等）")

        written_products = seed_products(session, plan.products, rng)
        session.commit()

        print()
        print("=" * 60)
        print(f"完成：商品 +{written_products} 条（会员/交易本骨架未实现）")
        print("=" * 60)
        return 0
    except Exception as e:  # noqa: BLE001
        session.rollback()
        print(f"[seed] 失败并回滚：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
