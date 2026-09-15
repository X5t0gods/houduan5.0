"""单号生成器测试（阶段 1 交付物）。

分两类：
- TestKeySpecs：静态测试（不需要 DB）
- TestSequenceGeneration：集成测试（需要真实 MySQL，用 pytest.mark.integration 标记，
  数据库不可用时 db_session fixture 会自动 skip）
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.exceptions import BusinessError
from app.core.sequence import EPOCH_DATE, KEY_SPECS, NoSeqService


class TestKeySpecs:
    """静态测试：编号规则表本身。"""

    def test_16_keys_defined(self) -> None:
        """KEY_SPECS 必须恰好定义 16 类单号（含 P/S/M/BATCH + 12 单据类）。"""
        expected_keys = {
            "P", "S", "M", "BATCH",
            "CG", "SH", "CT", "FK",
            "XS", "TH", "PD", "BS", "DB", "JB", "CZ", "GD",
        }
        assert set(KEY_SPECS.keys()) == expected_keys
        assert len(KEY_SPECS) == 16

    def test_global_keys_are_p_and_s(self) -> None:
        """只有 P 和 S 是全局键（跨日不重置），其余按日。"""
        global_keys = {k for k, spec in KEY_SPECS.items() if not spec.daily}
        assert global_keys == {"P", "S"}

    def test_only_batch_needs_scope(self) -> None:
        """只有 BATCH 键需要 scope 参数（商品编码隔离）。"""
        scope_keys = {k for k, spec in KEY_SPECS.items() if spec.needs_scope}
        assert scope_keys == {"BATCH"}

    def test_width_matches_expected(self) -> None:
        """流水号宽度：P/S=6, M=4, BATCH=2, 其余单据类=4。"""
        assert KEY_SPECS["P"].width == 6
        assert KEY_SPECS["S"].width == 6
        assert KEY_SPECS["M"].width == 4
        assert KEY_SPECS["BATCH"].width == 2
        for k in ("CG", "SH", "CT", "FK", "XS", "TH", "PD", "BS", "DB", "JB", "CZ", "GD"):
            assert KEY_SPECS[k].width == 4, f"{k} 宽度应 4，实际 {KEY_SPECS[k].width}"

    def test_epoch_date_is_1970(self) -> None:
        """全局键的 seq_date 必须是 1970-01-01（与按日键物理隔离）。"""
        assert date(1970, 1, 1) == EPOCH_DATE


@pytest.mark.integration
class TestSequenceGeneration:
    """集成测试：真实数据库上的取号行为。"""

    def _clean_seq(self, session: Session) -> None:
        """清空 sys_no_seq 表，保证测试独立。"""
        session.execute(text("DELETE FROM sys_no_seq"))
        session.commit()

    def test_product_code_format(self, db_session: Session) -> None:
        """P 键：P + 6 位流水（如 P000001）。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        no = seq.next_no("P", at=date(2026, 9, 15))
        assert re.fullmatch(r"P\d{6}", no), f"P 单号格式错：{no}"
        assert no == "P000001"
        db_session.rollback()

    def test_supplier_code_format(self, db_session: Session) -> None:
        """S 键：S + 6 位流水。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        assert seq.next_no("S", at=date(2026, 9, 15)) == "S000001"
        db_session.rollback()

    def test_member_no_format(self, db_session: Session) -> None:
        """M 键：M + yyyyMMdd + 4 位流水（如 M202609150001）。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        no = seq.next_no("M", at=date(2026, 9, 15))
        assert no == "M202609150001"
        db_session.rollback()

    def test_all_daily_document_keys_format(self, db_session: Session) -> None:
        """12 个单据类 key 全部按 前缀+yyyyMMdd+4位 生成。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        for key in ("CG", "SH", "CT", "FK", "XS", "TH", "PD", "BS", "DB", "JB", "CZ", "GD"):
            no = seq.next_no(key, at=date(2026, 9, 15))
            expected = f"{key}202609150001"
            assert no == expected, f"{key} 期望 {expected}，实际 {no}"
        db_session.rollback()

    def test_batch_format(self, db_session: Session) -> None:
        """BATCH 键：{product_code}-{yyyyMMdd}-{NN}（如 P000123-20260915-01）。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        no = seq.next_no("BATCH", scope="P000123", at=date(2026, 9, 15))
        assert no == "P000123-20260915-01"
        db_session.rollback()

    def test_same_key_increments(self, db_session: Session) -> None:
        """同一 key 连续取 20 次必须递增且无重复。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        results = [seq.next_no("XS", at=date(2026, 9, 15)) for _ in range(20)]
        assert len(set(results)) == 20, "20 次取号必须互不相同"
        # 严格递增（后 4 位是流水号）
        seq_nums = [int(r[-4:]) for r in results]
        assert seq_nums == list(range(1, 21))
        db_session.rollback()

    def test_cross_day_daily_key_resets(self, db_session: Session) -> None:
        """跨日：按日 key 从 1 重新开始。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        d1, d2 = date(2026, 9, 15), date(2026, 9, 16)
        assert seq.next_no("XS", at=d1) == "XS202609150001"
        assert seq.next_no("XS", at=d1) == "XS202609150002"
        # 换日
        assert seq.next_no("XS", at=d2) == "XS202609160001"
        # 回到 d1 仍从 3 继续
        assert seq.next_no("XS", at=d1) == "XS202609150003"
        db_session.rollback()

    def test_cross_day_global_key_continues(self, db_session: Session) -> None:
        """跨日：全局 key（P/S）继续递增，不重置。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        d1, d2 = date(2026, 9, 15), date(2026, 9, 16)
        assert seq.next_no("P", at=d1) == "P000001"
        assert seq.next_no("P", at=d2) == "P000002"  # 跨日不重置
        assert seq.next_no("P", at=d1) == "P000003"
        assert seq.next_no("S", at=d2) == "S000001"  # 不同 key 独立
        db_session.rollback()

    def test_batch_isolates_by_product_code(self, db_session: Session) -> None:
        """BATCH 键：不同商品各自计数（scope 隔离）。"""
        self._clean_seq(db_session)
        seq = NoSeqService(db_session)
        d = date(2026, 9, 15)
        assert seq.next_no("BATCH", scope="P000001", at=d) == "P000001-20260915-01"
        assert seq.next_no("BATCH", scope="P000002", at=d) == "P000002-20260915-01"
        assert seq.next_no("BATCH", scope="P000001", at=d) == "P000001-20260915-02"
        assert seq.next_no("BATCH", scope="P000002", at=d) == "P000002-20260915-02"
        db_session.rollback()

    def test_invalid_key_raises_business_error(self, db_session: Session) -> None:
        """未定义的 key 必须抛 BusinessError（不静默通过）。"""
        seq = NoSeqService(db_session)
        with pytest.raises(BusinessError) as exc_info:
            seq.next_no("UNKNOWN_KEY")
        assert "未定义的编号规则键" in str(exc_info.value.message)

    def test_batch_missing_scope_raises(self, db_session: Session) -> None:
        """BATCH 键不传 scope 必须抛 BusinessError。"""
        seq = NoSeqService(db_session)
        with pytest.raises(BusinessError) as exc_info:
            seq.next_no("BATCH")
        assert "必须传 scope" in str(exc_info.value.message)

    def test_non_batch_with_scope_raises(self, db_session: Session) -> None:
        """非 BATCH 键传了 scope 必须抛 BusinessError（防止调用方混淆）。"""
        seq = NoSeqService(db_session)
        with pytest.raises(BusinessError) as exc_info:
            seq.next_no("XS", scope="SHOULD_NOT_BE_HERE")
        assert "不接受 scope" in str(exc_info.value.message)

    def test_overflow_raises_business_error(self, db_session: Session) -> None:
        """流水号超出宽度上限必须抛 BusinessError（不静默截断产生重号）。

        构造方法：手工把 sys_no_seq.current_val 塞到 9999（XS 宽度 4，上限 9999），
        下一次 next_no 会拿到 10000 > 9999，必须抛错。
        """
        self._clean_seq(db_session)
        # 手工塞入接近上限的值
        db_session.execute(
            text("INSERT INTO sys_no_seq (seq_key, seq_date, current_val) "
                 "VALUES ('XS', '2026-09-15', 9999)")
        )
        db_session.commit()

        seq = NoSeqService(db_session)
        with pytest.raises(BusinessError) as exc_info:
            seq.next_no("XS", at=date(2026, 9, 15))
        assert "超出" in str(exc_info.value.message) or "上限" in str(exc_info.value.message)
        db_session.rollback()

    def test_concurrent_100_no_duplicates(self, db_session: Session) -> None:
        """并发 100 线程取同一 key，得到 100 个互不相同的单号。

        ⚠️ 关键实现细节：每个线程用**独立 Session**（LAST_INSERT_ID 是会话级值，
        共享 Session 会串号）。同时验证 MySQL 的行锁在 ON DUPLICATE KEY UPDATE
        下能正确串行化 —— 这是 LAST_INSERT_ID 惯用法的核心保障。
        """
        self._clean_seq(db_session)
        db_session.close()  # 主会话先关掉，让并发线程各自开新会话

        results: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()
        target_date = date(2026, 9, 15)

        def worker() -> None:
            session = SessionLocal()
            try:
                seq = NoSeqService(session)
                no = seq.next_no("XS", at=target_date)
                session.commit()
                with lock:
                    results.append(no)
            except Exception as e:  # noqa: BLE001
                session.rollback()
                with lock:
                    errors.append(e)
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=100) as pool:
            futures = [pool.submit(worker) for _ in range(100)]
            for f in as_completed(futures):
                f.result()  # 触发异常抛出

        assert not errors, f"并发取号出现异常：{errors[:3]}"
        assert len(results) == 100, f"应产出 100 个单号，实际 {len(results)}"
        unique = set(results)
        assert len(unique) == 100, f"100 个单号应互不相同，实际去重后 {len(unique)}"
        # 流水号应恰好覆盖 1-100
        seq_nums = sorted(int(r[-4:]) for r in results)
        assert seq_nums == list(range(1, 101)), "流水号应连续覆盖 1-100，无空洞"
