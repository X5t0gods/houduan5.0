"""商品业务编排（阶段 3 交付物）。

关键设计（spec 4.3-4.7 + 4.13）
--------------------------------
1. **校验顺序**（spec 4.3）：必填 → 分类存在 → 编码/条码重复 → 售价<进价 → 称重/保质期依赖字段
2. **条码 diff 更新**（spec 4.4）：现有 A、提交 B，B-A INSERT / A-B DELETE / A∩B 保持不变
   ⛔ 不全量删了重建（会丢 created_at）
3. **价格历史**（spec 4.4）：三个价（进价/售价/会员价）**哪个变了单独写一条**，三个都没变不写
4. **删除保护**（spec 4.6）：先查 11 张业务流水表，任一有记录 → 2007；否则先删配置子表再删商品
5. **导入独立事务**（spec 4.13）：每行 commit 一次，某行失败不影响其他行；批内重复预先扫描
6. **忽略 init_stock/safe_stock**（spec 1.2 ②）：期初入库属阶段 5，本阶段不落库不写流水
"""

from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.sequence import NoSeqService
from app.repositories import auth_repository, category_repository, unit_repository
from app.repositories import product_repository as repo
from app.schemas.product import (
    IMPORT_COLUMN_ORDER,
    IMPORT_MAX_ROWS,
    ImportResult,
    Product,
    ProductCreate,
    ProductDetail,
    ProductUpdate,
)

logger = get_logger(__name__)


# ---------- 内部工具：ORM row → Pydantic Schema ----------


def _row_to_product(row: tuple) -> Product:
    """把 list_products/get_product_row 返回的 (PrdProduct, category_name, unit_name, stock_qty)
    元组转成 Product Schema。
    """
    p, category_name, unit_name, stock_qty = row
    return Product(
        id=p.id,
        product_code=p.product_code,
        product_name=p.product_name,
        category_id=p.category_id,
        category_name=category_name,
        spu_id=p.spu_id,
        brand=p.brand,
        spec=p.spec,
        unit_id=p.unit_id,
        unit_name=unit_name,
        purchase_unit_id=p.purchase_unit_id,
        shelf_id=p.shelf_id,
        purchase_price=p.purchase_price,
        sale_price=p.sale_price,
        member_price=p.member_price,
        min_price=p.min_price,
        avg_cost=p.avg_cost,
        is_weight=p.is_weight,
        is_perishable=p.is_perishable,
        shelf_life_days=p.shelf_life_days,
        is_allow_return=p.is_allow_return,
        is_allow_discount=p.is_allow_discount,
        is_allow_point=p.is_allow_point,
        image_url=p.image_url,
        stock_qty=stock_qty if stock_qty is not None else Decimal("0"),
        status=p.status,
        remark=p.remark,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _validate_common(
    session: Session,
    *,
    product_name: str | None,
    category_id: int | None,
    unit_id: int | None,
    purchase_price: Decimal | None,
    sale_price: Decimal | None,
    is_weight: int,
    is_perishable: int,
    shelf_life_days: int | None,
    product_code: str | None = None,
    barcodes: list[str] | None = None,
    exclude_id: int | None = None,
) -> None:
    """商品新增/修改共用的校验逻辑（spec 4.3 校验顺序）。

    Raises:
        BusinessError: 2001 / 2002 / 2003 / 2005 之一。
    """
    # 1. 必填
    if not product_name or not str(product_name).strip():
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "商品名称不能为空")
    if category_id is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "分类不能为空")
    if unit_id is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "单位不能为空")
    if purchase_price is None or sale_price is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "进价与售价必填")

    # 2. 分类必须存在
    if category_repository.get_category_by_id(session, category_id) is None:
        raise BusinessError(ErrorCode.CATEGORY_INVALID, f"分类 id={category_id} 不存在")

    # 3. 单位必须存在
    if unit_repository.get_unit_by_id(session, unit_id) is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"单位 id={unit_id} 不存在")

    # 4. 编码/条码唯一性（排除自身）
    if product_code and repo.check_product_code_exists(session, product_code, exclude_id):
        raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, f"商品编码 {product_code} 已存在")
    if barcodes:
        existing = repo.get_existing_barcodes(session, barcodes)
        # 排除自身：若是修改场景，需要看这些条码是不是自己的
        if exclude_id is not None and existing:
            own = set(repo.get_barcodes_by_product_id(session, exclude_id))
            existing = existing - own
        if existing:
            raise BusinessError(
                ErrorCode.PRODUCT_DUPLICATE,
                f"条码 {', '.join(sorted(existing))} 已存在",
            )

    # 5. 售价 < 进价 → 2003（⛔ spec 1.2 ①：前端不会拦，后端必须拦，不发明 force 参数）
    if sale_price < purchase_price:
        raise BusinessError(
            ErrorCode.PRICE_BELOW_COST,
            f"售价 {sale_price} 低于进价 {purchase_price}，请确认后重试",
        )

    # 6. 称重商品必须有单位（本就是必填，顺带确认）
    if is_weight == 1 and unit_id is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "称重商品必须设置单位")

    # 7. 保质期商品必须有 shelf_life_days
    if is_perishable == 1 and (shelf_life_days is None or shelf_life_days <= 0):
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "保质期商品必须填写保质期天数")


def _resolve_barcodes(params_barcodes: list[str] | None, params_barcode: str | None) -> list[str]:
    """从请求参数中解析条码列表（优先 barcodes 数组，退回 barcode 单值）。"""
    if params_barcodes:
        # 去重 + 去空
        seen: set[str] = set()
        result: list[str] = []
        for code in params_barcodes:
            code = (code or "").strip()
            if code and code not in seen:
                seen.add(code)
                result.append(code)
        return result
    if params_barcode and params_barcode.strip():
        return [params_barcode.strip()]
    return []


# ---------- 4.1 GET /products ----------


def list_products(
    session: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    category_id: int | None = None,
    store_id: int = 0,
) -> tuple[list[Product], int]:
    """分页查询商品。

    Returns:
        (products, total) 元组。
    """
    rows, total = repo.list_products(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        category_id=category_id,
        store_id=store_id,
    )
    return [_row_to_product(r) for r in rows], total


# ---------- 4.2 GET /products/{id} ----------


def get_product_detail(session: Session, product_id: int, store_id: int = 0) -> ProductDetail:
    """获取商品详情（含 barcodes 数组）。

    Raises:
        BusinessError(2004): 商品不存在。
    """
    row = repo.get_product_row(session, product_id, store_id)
    if row is None:
        raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品 id={product_id} 不存在")
    base = _row_to_product(row)
    barcodes = repo.get_barcodes_by_product_id(session, product_id)
    return ProductDetail(**base.model_dump(), barcodes=barcodes)


# ---------- 4.3 POST /products ----------


def create_product(
    session: Session, params: ProductCreate, operator_id: int,
) -> ProductDetail:
    """新增商品。

    ⚠️ 事务：prd_product + prd_barcode（可能多条）在同一事务，最后一次 commit。
    ⚠️ init_stock / safe_stock 已被 Pydantic `extra='ignore'` 忽略（spec 1.2 ②）。

    Raises:
        BusinessError: 2001/2002/2003/2005。
    """
    barcodes = _resolve_barcodes(params.barcodes, params.barcode)

    # product_code：不传则自动生成（走 sys_no_seq P 键）
    product_code = params.product_code
    if not product_code:
        seq = NoSeqService(session)
        product_code = seq.next_no("P")

    # 完整校验
    _validate_common(
        session,
        product_name=params.product_name,
        category_id=params.category_id,
        unit_id=params.unit_id,
        purchase_price=params.purchase_price,
        sale_price=params.sale_price,
        is_weight=params.is_weight,
        is_perishable=params.is_perishable,
        shelf_life_days=params.shelf_life_days,
        product_code=product_code,
        barcodes=barcodes,
    )

    # 插入商品
    try:
        product = repo.insert_product(
            session,
            product_code=product_code,
            product_name=params.product_name,
            category_id=params.category_id,
            spu_id=params.spu_id,
            brand=params.brand,
            spec=params.spec,
            unit_id=params.unit_id,
            purchase_unit_id=params.purchase_unit_id,
            purchase_price=params.purchase_price,
            sale_price=params.sale_price,
            member_price=params.member_price,
            min_price=params.min_price,
            # avg_cost 初始 = purchase_price（阶段 6 收货时会按移动加权平均重算）
            avg_cost=params.purchase_price,
            is_weight=params.is_weight,
            is_perishable=params.is_perishable,
            shelf_life_days=params.shelf_life_days,
            is_allow_return=params.is_allow_return,
            is_allow_discount=params.is_allow_discount,
            is_allow_point=params.is_allow_point,
            image_url=params.image_url,
            shelf_id=params.shelf_id,
            status=1,
            remark=params.remark,
        )
        # 插入条码
        if barcodes:
            repo.insert_barcodes(session, product.id, barcodes)
    except IntegrityError as e:
        session.rollback()
        # 并发场景兜底（唯一索引冲突）
        raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, "商品编码或条码已存在（并发冲突）") from e

    # 写审计日志
    auth_repository.write_operation_log(
        session,
        user_id=operator_id,
        user_name=None,
        module="商品",
        action="新增商品",
        target_type="prd_product",
        target_id=product.id,
        after_value={
            "product_code": product.product_code,
            "product_name": product.product_name,
            "sale_price": str(product.sale_price),
        },
        result=1,
    )
    session.commit()
    logger.info(
        f"新增商品：id={product.id}, code={product.product_code}, "
        f"name={product.product_name}"
    )

    return get_product_detail(session, product.id)


# ---------- 4.4 PUT /products/{id} ----------


def update_product(
    session: Session, product_id: int, params: ProductUpdate, operator_id: int,
) -> ProductDetail:
    """修改商品（含条码 diff + 价格历史）。

    ⚠️ 价格历史规则（spec 4.4）：三个价（进价/售价/会员价）哪个变了单独写一条；
       三个都没变**不写**（否则改个备注也刷一条垃圾记录）。
    """
    # 先取现有商品
    existing_row = repo.get_product_row(session, product_id, store_id=0)
    if existing_row is None:
        raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品 id={product_id} 不存在")
    existing = existing_row[0]  # PrdProduct 对象

    # 合并字段（None 表示不改）
    merged_name = params.product_name if params.product_name is not None else existing.product_name
    merged_category = params.category_id if params.category_id is not None else existing.category_id
    merged_unit = params.unit_id if params.unit_id is not None else existing.unit_id
    merged_purchase = (
        params.purchase_price if params.purchase_price is not None else existing.purchase_price
    )
    merged_sale = params.sale_price if params.sale_price is not None else existing.sale_price
    merged_weight = params.is_weight if params.is_weight is not None else existing.is_weight
    merged_perishable = (
        params.is_perishable if params.is_perishable is not None else existing.is_perishable
    )
    merged_shelf_life = (
        params.shelf_life_days if params.shelf_life_days is not None else existing.shelf_life_days
    )

    # 条码：None 表示不改，[] 表示清空，非空表示 diff 更新
    barcodes_to_apply: list[str] | None = None
    if params.barcodes is not None:
        barcodes_to_apply = _resolve_barcodes(params.barcodes, None)
    elif params.barcode is not None:
        barcodes_to_apply = _resolve_barcodes(None, params.barcode)

    # 校验（排除自身）
    _validate_common(
        session,
        product_name=merged_name,
        category_id=merged_category,
        unit_id=merged_unit,
        purchase_price=merged_purchase,
        sale_price=merged_sale,
        is_weight=merged_weight,
        is_perishable=merged_perishable,
        shelf_life_days=merged_shelf_life,
        product_code=params.product_code,  # 只有传了才校验唯一
        barcodes=barcodes_to_apply,
        exclude_id=product_id,
    )

    # 组装 update 值（只包含实际变更的字段）
    values: dict[str, Any] = {}
    for field_name, new_val in [
        ("product_name", params.product_name),
        ("category_id", params.category_id),
        ("unit_id", params.unit_id),
        ("spu_id", params.spu_id),
        ("brand", params.brand),
        ("spec", params.spec),
        ("purchase_unit_id", params.purchase_unit_id),
        ("shelf_id", params.shelf_id),
        ("purchase_price", params.purchase_price),
        ("sale_price", params.sale_price),
        ("member_price", params.member_price),
        ("min_price", params.min_price),
        ("is_weight", params.is_weight),
        ("is_perishable", params.is_perishable),
        ("shelf_life_days", params.shelf_life_days),
        ("is_allow_return", params.is_allow_return),
        ("is_allow_discount", params.is_allow_discount),
        ("is_allow_point", params.is_allow_point),
        ("image_url", params.image_url),
        ("remark", params.remark),
        ("product_code", params.product_code),
    ]:
        if new_val is not None:
            values[field_name] = new_val

    # 价格历史：三个价哪个变了单独写一条（spec 4.4）
    price_changes: list[tuple[str, Decimal, Decimal]] = []
    if params.purchase_price is not None and params.purchase_price != existing.purchase_price:
        price_changes.append(("进价", existing.purchase_price, params.purchase_price))
    if params.sale_price is not None and params.sale_price != existing.sale_price:
        price_changes.append(("售价", existing.sale_price, params.sale_price))
    if (
        params.member_price is not None
        and existing.member_price is not None
        and params.member_price != existing.member_price
    ):
        price_changes.append(("会员价", existing.member_price, params.member_price))
    elif params.member_price is not None and existing.member_price is None:
        # 从无到有也算变更
        price_changes.append(("会员价", Decimal("0"), params.member_price))

    try:
        if values:
            repo.update_product_fields(session, product_id, values)
        # 条码 diff（在 update 之后，避免唯一键冲突时商品数据已改）
        if barcodes_to_apply is not None:
            repo.diff_barcodes(session, product_id, barcodes_to_apply)
        # 价格历史
        for change_type, old_price, new_price in price_changes:
            repo.write_price_history(
                session,
                product_id=product_id,
                change_type=change_type,
                old_purchase=old_price if change_type == "进价" else None,
                new_purchase=new_price if change_type == "进价" else None,
                old_sale=old_price if change_type == "售价" else None,
                new_sale=new_price if change_type == "售价" else None,
                changed_by=operator_id,
            )
    except IntegrityError as e:
        session.rollback()
        raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, "商品编码或条码已存在（并发冲突）") from e

    auth_repository.write_operation_log(
        session,
        user_id=operator_id,
        user_name=None,
        module="商品",
        action="修改商品",
        target_type="prd_product",
        target_id=product_id,
        before_value={"changed_fields": list(values.keys())},
        after_value={"price_changes": [c[0] for c in price_changes]},
        result=1,
    )
    session.commit()
    logger.info(
        f"修改商品：id={product_id}, 变更字段={list(values.keys())}, "
        f"价格变更={[c[0] for c in price_changes]}"
    )
    return get_product_detail(session, product_id)


# ---------- 4.5 PUT /products/{id}/status ----------


def update_status(
    session: Session, product_id: int, status: int, operator_id: int,
) -> None:
    """停用/启用商品（只改状态位，不做其他业务判断）。

    ⚠️ 停用后收银台扫码返回 6002 是**阶段 4** 的事，本阶段不实现。
    """
    existing_row = repo.get_product_row(session, product_id, store_id=0)
    if existing_row is None:
        raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品 id={product_id} 不存在")

    repo.update_product_fields(session, product_id, {"status": status})
    action = "启用商品" if status == 1 else "停用商品"
    auth_repository.write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="商品", action=action,
        target_type="prd_product", target_id=product_id,
        after_value={"status": status}, result=1,
    )
    session.commit()
    logger.info(f"{action}：id={product_id}")


# ---------- 4.6 DELETE /products/{id} ----------


def delete_product(session: Session, product_id: int, operator_id: int) -> None:
    """删除商品（仅当无业务流水时允许）。

    ⚠️ spec 4.6 三个必须注意的点：
    1. 判定范围：11 张业务流水表任一有记录即拒绝
    2. **必须先删子表**（18 张表 FK 全 RESTRICT，不先删子表会 500）
    3. 20 个演示商品**一个都删不掉**（全部有 inv_stock + inv_stock_flow），这是预期行为
    """
    existing_row = repo.get_product_row(session, product_id, store_id=0)
    if existing_row is None:
        raise BusinessError(ErrorCode.PRODUCT_NOT_FOUND, f"商品 id={product_id} 不存在")

    # 检查业务流水
    ref_table = repo.find_business_flow_reference(session, product_id)
    if ref_table is not None:
        raise BusinessError(
            ErrorCode.PRODUCT_HAS_BUSINESS_FLOW,
            f"该商品已产生业务流水（{ref_table}），不可删除；如需下架请用停用",
        )

    code = existing_row[0].product_code
    name = existing_row[0].product_name

    # 级联删除（先子表后主表）
    repo.delete_product_cascade(session, product_id)

    auth_repository.write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="商品", action="删除商品",
        target_type="prd_product", target_id=product_id,
        before_value={"product_code": code, "product_name": name},
        result=1,
    )
    session.commit()
    logger.info(f"删除商品：id={product_id}, code={code}")


# ---------- 4.7 GET /products/barcode/{barcode} ----------


def get_by_barcode(
    session: Session, barcode: str, store_id: int = 0,
) -> Product | None:
    """按条码查商品（建档查重用）。

    ⚠️ spec 4.7 + 1.2 ④：
    - 找不到时返回 None（路由层转 code=0 + data=null），**不是 6001**
    - 6001 留给阶段 4 的 /sales/cart/resolve
    - 商品停用（status=0）时**照样返回**（查重场景，不是收银）
    - 支持主条码与附加条码
    """
    row = repo.get_product_by_barcode_row(session, barcode, store_id)
    if row is None:
        return None
    return _row_to_product(row)


# ---------- 4.13 POST /products/import ----------


def _parse_excel_row(row_values: tuple) -> dict[str, Any]:
    """把 Excel 一行数据解析成 dict（按 IMPORT_COLUMN_ORDER 顺序）。"""
    result: dict[str, Any] = {}
    for idx, field_name in enumerate(IMPORT_COLUMN_ORDER):
        val = row_values[idx] if idx < len(row_values) else None
        if val is None:
            result[field_name] = None
        elif isinstance(val, str):
            result[field_name] = val.strip() or None
        else:
            result[field_name] = val
    return result


def _parse_barcodes_string(raw: str | None) -> list[str]:
    """解析条码字符串：支持 , 或 、 分隔（一品多码）。"""
    if not raw:
        return []
    # 统一分隔符
    normalized = raw.replace("、", ",").replace("，", ",")
    return [c.strip() for c in normalized.split(",") if c.strip()]


def _to_decimal(val: Any) -> Decimal | None:
    """把 Excel 单元格的值转成 Decimal（int/float/str 都要能处理）。"""
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    """把 Excel 单元格的值转成 int。"""
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def import_products(
    session: Session,
    *,
    file_bytes: bytes,
    filename: str,
    operator_id: int,
) -> ImportResult:
    """Excel 批量导入商品（spec 4.13）。

    ⚠️ 关键约束：
    1. **只支持 .xlsx**（用 openpyxl 的 read_only 模式）；.xls 是老格式，
       xlrd 2.x 已不支持 xlsx、openpyxl 反向也不兼容 → 收到 .xls 直接返回 2001
    2. **逐行独立提交**，某行失败不影响其他行（用 session.commit() per row）
       ⛔ 不整批回滚（文档明确要求"部分成功即可用"）
    3. **批内重复预扫描**：第 3 行和第 7 行同编码 → 两行都失败（不能一行成功一行撞库）
    4. errors 格式：`第 {行号} 行：{原因}`，行号从表头之后第 1 行数据开始（即 Excel 第 2 行）
    5. ≤ 2000 行，超出返回 2001

    Args:
        session: 数据库会话。
        file_bytes: 上传文件的字节内容。
        filename: 原始文件名（用于扩展名校验）。
        operator_id: 当前登录用户 ID。

    Returns:
        ImportResult(total, success, failed, errors)。

    Raises:
        BusinessError(2001): 文件格式不支持 / 行数超限 / 文件解析失败。
    """
    # 1. 扩展名校验（只支持 .xlsx）
    lower_name = filename.lower()
    if not lower_name.endswith(".xlsx"):
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            "请上传 .xlsx 格式文件（不支持 .xls 老格式，也不支持 .csv/.txt）",
        )

    # 2. 打开工作簿
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except (InvalidFileException, Exception) as e:  # noqa: BLE001
        raise BusinessError(
            ErrorCode.REQUIRED_MISSING,
            f"无法解析 Excel 文件：{type(e).__name__}",
        ) from e

    ws = wb.active
    if ws is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "Excel 文件没有活动工作表")

    # 3. 读全部数据行（跳过表头）
    all_rows: list[tuple] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # 表头
        # 完全空行跳过
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            continue
        all_rows.append(row)
        if len(all_rows) > IMPORT_MAX_ROWS:
            wb.close()
            raise BusinessError(
                ErrorCode.REQUIRED_MISSING,
                f"导入行数超过上限 {IMPORT_MAX_ROWS} 行，请分批导入",
            )
    wb.close()

    total = len(all_rows)
    if total == 0:
        return ImportResult(total=0, success=0, failed=0, errors=[])

    # 4. 解析每行到 dict
    parsed_rows = [_parse_excel_row(r) for r in all_rows]

    # 5. 批内重复预扫描（编码 + 条码）
    code_counts: dict[str, list[int]] = {}
    barcode_counts: dict[str, list[int]] = {}
    for idx, row in enumerate(parsed_rows):
        code = row.get("product_code")
        if code:
            code_counts.setdefault(str(code), []).append(idx)
        for bc in _parse_barcodes_string(row.get("barcodes")):
            barcode_counts.setdefault(bc, []).append(idx)

    batch_dup_rows: set[int] = set()
    batch_dup_reasons: dict[int, str] = {}
    for code, idxs in code_counts.items():
        if len(idxs) > 1:
            for i in idxs:
                batch_dup_rows.add(i)
                batch_dup_reasons[i] = f"商品编码 {code} 在本批次内重复（第 {idxs} 行）"
    for bc, idxs in barcode_counts.items():
        if len(idxs) > 1:
            for i in idxs:
                if i not in batch_dup_rows:
                    batch_dup_rows.add(i)
                    batch_dup_reasons[i] = f"条码 {bc} 在本批次内重复"

    # 6. 逐行处理
    success_count = 0
    errors: list[str] = []
    seq = NoSeqService(session)

    for idx, row in enumerate(parsed_rows):
        line_no = idx + 1  # 行号从 1 开始（表头之后）

        # 6.1 批内重复预失败
        if idx in batch_dup_rows:
            errors.append(f"第 {line_no} 行：{batch_dup_reasons[idx]}")
            continue

        try:
            _import_one_row(session, row, seq, operator_id)
            session.commit()
            success_count += 1
        except BusinessError as e:
            session.rollback()
            errors.append(f"第 {line_no} 行：{e.message}")
        except IntegrityError as e:
            session.rollback()
            # 撞唯一键（编码或条码已存在于 DB）
            errors.append(f"第 {line_no} 行：商品编码或条码已存在")
            logger.warning(f"导入第 {line_no} 行 IntegrityError: {e}")
        except Exception as e:  # noqa: BLE001
            session.rollback()
            errors.append(f"第 {line_no} 行：{type(e).__name__}")
            logger.exception(f"导入第 {line_no} 行未预期异常")

    # 7. 写导入总日志
    auth_repository.write_operation_log(
        session, user_id=operator_id, user_name=None,
        module="商品", action="导入商品",
        target_type="prd_product", target_id=None,
        after_value={"total": total, "success": success_count, "failed": len(errors)},
        result=1 if success_count > 0 else 0,
    )
    session.commit()

    logger.info(f"导入商品完成：total={total}, success={success_count}, failed={len(errors)}")
    return ImportResult(
        total=total,
        success=success_count,
        failed=len(errors),
        errors=errors,
    )


def _import_one_row(
    session: Session, row: dict[str, Any], seq: NoSeqService, operator_id: int,
) -> None:
    """导入单行（内部工具）—— 校验 + 插入商品 + 插入条码。

    Raises:
        BusinessError: 校验失败（会被外层捕获转成 errors[] 条目）。
    """
    # 必填校验
    product_name = row.get("product_name")
    if not product_name:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "商品名称不能为空")

    category_name = row.get("category_name")
    if not category_name:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "分类名称不能为空")
    category = category_repository.get_category_by_name(session, str(category_name))
    if category is None:
        raise BusinessError(ErrorCode.CATEGORY_INVALID, f"分类 '{category_name}' 不存在")

    unit_name = row.get("unit_name")
    if not unit_name:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "单位名称不能为空")
    unit = unit_repository.get_unit_by_name(session, str(unit_name))
    if unit is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, f"单位 '{unit_name}' 不存在")

    purchase_price = _to_decimal(row.get("purchase_price"))
    sale_price = _to_decimal(row.get("sale_price"))
    if purchase_price is None or sale_price is None:
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "进价与售价必填且必须是数字")

    member_price = _to_decimal(row.get("member_price"))
    is_weight = _to_int(row.get("is_weight")) or 0
    shelf_life_days = _to_int(row.get("shelf_life_days"))
    is_perishable = 1 if shelf_life_days and shelf_life_days > 0 else 0

    # 编码：不填自动生成
    product_code = row.get("product_code")
    if not product_code:
        product_code = seq.next_no("P")
    else:
        product_code = str(product_code).strip()
        if repo.check_product_code_exists(session, product_code):
            raise BusinessError(ErrorCode.PRODUCT_DUPLICATE, f"商品编码 {product_code} 已存在")

    # 条码解析
    barcodes = _parse_barcodes_string(row.get("barcodes"))
    if barcodes:
        existing_bc = repo.get_existing_barcodes(session, barcodes)
        if existing_bc:
            raise BusinessError(
                ErrorCode.PRODUCT_DUPLICATE,
                f"条码 {', '.join(sorted(existing_bc))} 已存在",
            )

    # 售价 < 进价
    if sale_price < purchase_price:
        raise BusinessError(
            ErrorCode.PRICE_BELOW_COST,
            f"售价 {sale_price} 低于进价 {purchase_price}",
        )

    # 保质期商品必须有天数
    if is_perishable == 1 and (shelf_life_days is None or shelf_life_days <= 0):
        raise BusinessError(ErrorCode.REQUIRED_MISSING, "保质期商品必须填写保质期天数")

    # 插入商品 + 条码
    product = repo.insert_product(
        session,
        product_code=product_code,
        product_name=str(product_name),
        category_id=category.id,
        unit_id=unit.id,
        purchase_price=purchase_price,
        sale_price=sale_price,
        member_price=member_price,
        avg_cost=purchase_price,
        spec=row.get("spec"),
        is_weight=is_weight,
        is_perishable=is_perishable,
        shelf_life_days=shelf_life_days,
        is_allow_return=1,
        is_allow_discount=1,
        is_allow_point=1,
        remark=row.get("remark"),
        status=1,
    )
    if barcodes:
        repo.insert_barcodes(session, product.id, barcodes)
