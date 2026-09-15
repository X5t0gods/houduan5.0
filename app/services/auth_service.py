"""认证与权限的业务编排层（阶段 2 交付物）。

⚠️ 事务边界（决策 8）：commit/rollback 只出现在本层；repository 只 flush。

⚠️ 登录失败也要能写出日志（spec 4.3）：
   业务事务回滚会把 log 一起回滚 → **失败路径用独立 Session 写日志**（`_fail_login`），
   成功路径用主 Session 写日志（同一事务提交）。

⚠️ 三处契约冲突（spec 1.2，以前端为准，代码里逐条注明）：
   1. ADMIN 用户的 permissions 返回 ["*"]，不是 32 个码
   2. roles 返回中文角色名（按 sort 升序），不是编码
   3. perm_type 只有 1（目录）和 2（功能权限），文档说的 3 从未存在
"""

from __future__ import annotations

import base64
import io
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.error_codes import ErrorCode
from app.core.exceptions import BusinessError
from app.core.logging import get_logger
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.sys_config_reader import get_login_fail_limit, get_login_lock_minutes
from app.repositories import auth_repository as repo
from app.schemas.auth import (
    CaptchaResult,
    ChangePasswordParams,
    LoginParams,
    LoginResult,
    RefreshResult,
    UserProfile,
)

logger = get_logger(__name__)


# ---------- 验证码常量 ----------

# 4 位字符集：排除易混淆字符（0/O/1/I/l），统一大写
CAPTCHA_CHARSET: Final[str] = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CAPTCHA_LENGTH: Final[int] = 4
CAPTCHA_EXPIRE_MINUTES: Final[int] = 2  # 与 spec 4.2 一致：签发后 2 分钟过期
CAPTCHA_IMAGE_WIDTH: Final[int] = 120
CAPTCHA_IMAGE_HEIGHT: Final[int] = 40


def _now() -> datetime:
    """当前时间（Asia/Shanghai）。"""
    return datetime.now(ZoneInfo(settings.TZ))


def _generate_captcha_code() -> str:
    """生成 4 位随机验证码（secrets 而非 random，密码学安全）。"""
    return "".join(secrets.choice(CAPTCHA_CHARSET) for _ in range(CAPTCHA_LENGTH))


def _render_captcha_image(code: str) -> bytes:
    """用 Pillow 渲染验证码 PNG 图片（约 120×40）。

    ⚠️ Windows 下不要写死字体文件路径（arial.ttf 可能不存在）：
       优先 ImageFont.load_default()，拿不到再尝试系统字体并捕获异常降级。
    ⚠️ 不要过度设计：文字 + 少量干扰线/噪点即可（spec 4.2）。
    """
    img = Image.new("RGB", (CAPTCHA_IMAGE_WIDTH, CAPTCHA_IMAGE_HEIGHT), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    # 字体加载：优先默认，失败则降级到系统常见字体
    font = None
    try:
        font = ImageFont.load_default(size=24)  # Pillow 10+ 支持 size 参数
    except TypeError:
        # 老版本 Pillow 的 load_default 不接受 size
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        # 极少数环境连默认字体都没有，尝试系统字体
        for font_name in ("arial.ttf", "DejaVuSans.ttf", "msyh.ttc"):
            try:
                font = ImageFont.truetype(font_name, 24)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()

    # 绘制字符（每个字符位置略有抖动，防止过于规整被 OCR 秒杀）
    rng = secrets.SystemRandom()
    for i, ch in enumerate(code):
        x = 12 + i * 24 + rng.randint(-2, 2)
        y = 8 + rng.randint(-2, 4)
        color = (rng.randint(20, 100), rng.randint(20, 100), rng.randint(80, 180))
        draw.text((x, y), ch, fill=color, font=font)

    # 干扰线（3 条随机斜线）
    for _ in range(3):
        x1, y1 = rng.randint(0, CAPTCHA_IMAGE_WIDTH), rng.randint(0, CAPTCHA_IMAGE_HEIGHT)
        x2, y2 = rng.randint(0, CAPTCHA_IMAGE_WIDTH), rng.randint(0, CAPTCHA_IMAGE_HEIGHT)
        draw.line([(x1, y1), (x2, y2)], fill=(160, 160, 160), width=1)

    # 噪点（20 个随机像素）
    for _ in range(20):
        x, y = rng.randint(0, CAPTCHA_IMAGE_WIDTH - 1), rng.randint(0, CAPTCHA_IMAGE_HEIGHT - 1)
        draw.point((x, y), fill=(100, 100, 100))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _extract_client_ip(x_forwarded_for: str | None, remote_addr: str | None) -> str:
    """提取客户端真实 IP。

    优先级：X-Forwarded-For 首个值 → request.client.host → "unknown"
    截断到 45 字符（sys_login_log.ip 与 sys_user.last_login_ip 都是 VARCHAR(45)，
    45 = IPv6 完整长度）。
    """
    if x_forwarded_for:
        # XFF 可能是 "client, proxy1, proxy2" 格式，取第一个
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            return first[:45]
    return (remote_addr or "unknown")[:45]


class AuthService:
    """认证服务（业务编排 + 事务控制）。

    使用方式：
        service = AuthService(session)
        result = service.login(params, ip, user_agent)  # 内部会 commit / rollback
    """

    def __init__(self, session: Session) -> None:
        """初始化。

        Args:
            session: 调用方的 SQLAlchemy Session（一般来自 Depends(get_db)）。
        """
        self.session = session

    # ---------- 4.2 GET /auth/captcha ----------

    def generate_captcha(self, client_ip: str | None) -> CaptchaResult:
        """生成图形验证码：4 位字符 + PNG 图片 + 落库。

        ⚠️ 顺带清理该表已过期记录（低频执行即可，阶段 8 会有定时任务接管）。

        Args:
            client_ip: 客户端 IP（防刷，可为空）。

        Returns:
            CaptchaResult(captcha_key=UUID, captcha_image=data:image/png;base64,...)
        """
        # 顺带清理过期验证码（每次生成时执行，频率与用户请求成正比，成本可接受）
        # 阶段 8 起 APScheduler 定时任务会接管，本处保留兜底
        cleaned = repo.cleanup_expired_captchas(self.session)
        if cleaned > 0:
            logger.debug(f"清理过期验证码 {cleaned} 条")

        code = _generate_captcha_code()
        captcha_key = uuid.uuid4().hex
        expire_at = _now().replace(microsecond=0) + timedelta(minutes=CAPTCHA_EXPIRE_MINUTES)

        repo.create_captcha(
            self.session,
            captcha_key=captcha_key,
            captcha_code=code,
            ip=client_ip,
            expire_at=expire_at,
        )
        self.session.commit()

        # 渲染图片 → Base64 Data URL（前端直接当 img.src 用）
        png_bytes = _render_captcha_image(code)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        # ⚠️ 日志绝不打印验证码明文（决策：不用 logger.info(code)）
        logger.info(f"生成验证码：key={captcha_key[:8]}..., ip={client_ip}")
        return CaptchaResult(captcha_key=captcha_key, captcha_image=data_url)

    # ---------- 4.3 POST /auth/login ----------

    def login(
        self,
        params: LoginParams,
        client_ip: str,
        user_agent: str | None,
    ) -> LoginResult:
        """登录（严格按 spec 4.3 的 6 步顺序校验）。

        校验顺序（**每一步的顺序都有原因，不要打乱**）：
        | 步 | 条件 | 返回 |
        | 1 | 验证码错/过期/已用/不匹配 | 1001（先挡自动化爆破） |
        | 2 | 用户不存在 | 1002（与密码错共用文案，防账号枚举） |
        | 3 | locked_until > 当前时间 | 1003（锁定期间不再累加计数） |
        | 4 | 密码校验失败 | 计数+1，达阈值同时锁定；返回 1002 或 1003 |
        | 5 | status = 0（停用） | 1004（放密码之后，防"账号是否停用"枚举） |
        | 6 | 全部通过 | 登录成功 |

        Args:
            params: 登录参数（username/password/captcha_key/captcha_code）。
            client_ip: 客户端 IP（已由路由层从 X-Forwarded-For 提取）。
            user_agent: 客户端 UA（截断到 255 由 repository 处理）。

        Returns:
            LoginResult(access_token, refresh_token, token_type, expires_in, user_profile)

        Raises:
            BusinessError: 各步骤校验失败时抛出对应错误码（1001/1002/1003/1004）。
        """
        start = time.perf_counter()

        # ---------- Step 1: 验证码校验 ----------
        captcha = repo.get_captcha_by_key(self.session, params.captcha_key)
        if captcha is None:
            self._fail_login(params.username, None, client_ip, user_agent, "验证码错误或已失效")
            raise BusinessError(ErrorCode.CAPTCHA_INVALID)
        if captcha.used == 1:
            # 一 key 只能用一次（spec 4.3）
            self._fail_login(params.username, None, client_ip, user_agent, "验证码已使用")
            raise BusinessError(ErrorCode.CAPTCHA_INVALID)
        if captcha.expire_at.replace(tzinfo=ZoneInfo(settings.TZ)) < _now():
            self._fail_login(params.username, None, client_ip, user_agent, "验证码已过期")
            raise BusinessError(ErrorCode.CAPTCHA_INVALID)
        # 大小写不敏感比较
        if captcha.captcha_code.upper() != params.captcha_code.strip().upper():
            self._fail_login(params.username, None, client_ip, user_agent, "验证码错误")
            raise BusinessError(ErrorCode.CAPTCHA_INVALID)

        # ---------- Step 2: 用户不存在 → 1002（不泄漏"用户不存在"） ----------
        user = repo.get_user_by_username(self.session, params.username)
        if user is None:
            self._fail_login(params.username, None, client_ip, user_agent, "用户名或密码错误")
            raise BusinessError(ErrorCode.LOGIN_FAILED)

        # ---------- Step 3: 已锁定 → 1003（锁定期间不再累加计数） ----------
        if user.locked_until is not None:
            locked_until_aware = user.locked_until.replace(tzinfo=ZoneInfo(settings.TZ))
            if locked_until_aware > _now():
                self._fail_login(params.username, user.id, client_ip, user_agent, "账号已被锁定")
                raise BusinessError(ErrorCode.ACCOUNT_LOCKED)

        # ---------- Step 4: 密码校验 ----------
        if not verify_password(params.password, user.password_hash):
            fail_limit = get_login_fail_limit(self.session)
            lock_minutes = get_login_lock_minutes(self.session)
            new_count = user.login_fail_count + 1
            will_lock = new_count >= fail_limit

            # ⚠️ 关键顺序：**先写日志（独立 session）再更新用户表**。
            # 反过来会死锁：
            #   - 主 session UPDATE sys_user 持有 X-lock on user row
            #   - 独立 session INSERT sys_login_log 需 S-lock on user row
            #     （验证 fk_loginlog_user: sys_login_log.user_id → sys_user.id）
            #   - 主 session 同步等 _fail_login 返回，独立 session 等主 session 释锁 → 循环等待
            # 先写日志时主 session 还未拿到 X-lock，独立 session 的 S-lock 能顺利拿到并释放。
            reason = (
                f"密码错误次数达 {fail_limit} 次，账号已锁定 {lock_minutes} 分钟"
                if will_lock else "用户名或密码错误"
            )
            self._fail_login(params.username, user.id, client_ip, user_agent, reason)

            # 现在更新用户表（独立 session 已提交并释放 S-lock）
            repo.update_login_failure(
                self.session,
                user_id=user.id,
                current_fail_count=user.login_fail_count,
                fail_limit=fail_limit,
                lock_minutes=lock_minutes,
            )
            self.session.commit()  # 提交失败计数与锁定状态更新

            if will_lock:
                raise BusinessError(ErrorCode.ACCOUNT_LOCKED)
            raise BusinessError(ErrorCode.LOGIN_FAILED)

        # ---------- Step 5: 停用 ----------
        if user.status == 0:
            self._fail_login(params.username, user.id, client_ip, user_agent, "账号已停用")
            raise BusinessError(ErrorCode.ACCOUNT_DISABLED)

        # ---------- Step 6: 成功 ----------
        # 6.1 更新用户状态：清零失败计数、清除锁定、记录最后登录
        repo.update_login_success(self.session, user.id, client_ip)
        # 6.2 验证码置为已使用（一 key 只能用一次）
        repo.mark_captcha_used(self.session, captcha.id)
        # 6.3 装配 user_profile
        profile = self._build_user_profile(user)
        # 6.4 签发令牌
        access_token, expires_in = create_access_token(user.id, user.username)
        refresh_token = create_refresh_token(user.id, user.username)
        # 6.5 写登录成功日志（与主事务同一提交）
        repo.write_login_log(
            self.session,
            username=user.username,
            user_id=user.id,
            ip=client_ip,
            user_agent=user_agent,
            result=1,
            fail_reason=None,
        )
        self.session.commit()

        cost_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            f"登录成功：user={user.username}(id={user.id}), "
            f"ip={client_ip}, cost={cost_ms}ms"
        )

        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=expires_in,
            user_profile=profile,
        )

    def _fail_login(
        self,
        username: str,
        user_id: int | None,
        client_ip: str,
        user_agent: str | None,
        fail_reason: str,
    ) -> None:
        """登录失败时用**独立 Session** 写日志。

        ⚠️ 为什么独立：主 Session 后续可能因抛异常回滚，日志会一起消失；
        用独立 Session commit 后，即使主事务回滚，失败日志也能保留。
        spec 4.3 明确要求"登录失败也要能写出日志"。
        """
        log_session = SessionLocal()
        try:
            repo.write_login_log(
                log_session,
                username=username,
                user_id=user_id,
                ip=client_ip,
                user_agent=user_agent,
                result=0,
                fail_reason=fail_reason,
            )
            log_session.commit()
        except Exception:  # noqa: BLE001
            log_session.rollback()
            logger.exception("写登录失败日志时出错（不阻断主流程）")
        finally:
            log_session.close()
        logger.warning(f"登录失败：user={username}, ip={client_ip}, reason={fail_reason}")

    # ---------- 4.4 GET /auth/me ----------

    def get_user_profile(self, user_id: int) -> UserProfile:
        """装配 UserProfile（/auth/me 与 login 共用）。

        ⚠️ 关键规则（spec 1.2 + 4.4）：
        1. roles 是**中文角色名**数组（按 sys_role.sort 升序）—— 前端 HeaderBar 直接展示
        2. permissions：ADMIN 用户返回 ["*"] 通配符；其他用户返回 perm_type=2 的并集
        3. store_name 通过 JOIN base_store 取；无门店时为 None
        """
        user = repo.get_user_by_id(self.session, user_id)
        if user is None:
            # 理论上不会走到这里（get_current_user 已校验过），但兜底防御
            raise BusinessError(ErrorCode.LOGIN_FAILED, "用户不存在或已删除")

        return self._build_user_profile(user)

    def _build_user_profile(self, user) -> UserProfile:  # type: ignore[no-untyped-def]
        """从 SysUser 对象装配 UserProfile（内部工具，避免 get_user_profile 与 login 重复代码）。"""
        # roles：中文角色名，按 sort 升序（spec 1.2 冲突 ②）
        roles = repo.get_user_roles(self.session, user.id)
        role_names = [r.role_name for r in roles]

        # permissions：ADMIN 通配符特判（spec 1.2 冲突 ①）
        if any(r.role_code == repo.ADMIN_ROLE_CODE for r in roles):
            permissions = ["*"]
        else:
            # 只取 perm_type=2 的功能权限码，去重（repository 已 DISTINCT）
            permissions = repo.get_user_permissions(self.session, user.id)

        store_name = repo.get_store_name(self.session, user.store_id)

        return UserProfile(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            phone=user.phone,
            avatar=user.avatar,
            store_id=user.store_id,
            store_name=store_name,
            roles=role_names,
            permissions=permissions,
        )

    # ---------- 4.5 POST /auth/refresh ----------

    def refresh(self, refresh_token_str: str) -> RefreshResult:
        """刷新 access token。

        ⚠️ 关键规则（spec 4.5）：
        1. 只接受 type="refresh" 的令牌；用 access token 来刷新必须失败
        2. 重新查用户：已停用/已删除的用户不允许刷新（否则停用形同虚设）
        3. 返回值**只有两个字段**（access_token / expires_in），不塞 refresh_token 或 profile

        Raises:
            BusinessError(1004): 用户已停用
            TokenError: 令牌无效/过期/类型不符（由路由层转 401）
        """
        # decode_token 内部已校验 type=refresh，若传 access 会抛 TokenError
        payload = decode_token(refresh_token_str, expected_type=TOKEN_TYPE_REFRESH)

        user = repo.get_user_by_id(self.session, payload.sub)
        if user is None:
            # 用户已被删除 —— 抛 TokenError 让上层转 401
            raise TokenError("用户不存在或已删除")
        if user.status == 0:
            # 停用用户不允许刷新（spec 4.5）
            raise BusinessError(ErrorCode.ACCOUNT_DISABLED)

        access_token, expires_in = create_access_token(user.id, user.username)
        logger.info(f"刷新令牌：user={user.username}(id={user.id})")
        return RefreshResult(access_token=access_token, expires_in=expires_in)

    # ---------- 4.7 POST /auth/password ----------

    def change_password(
        self,
        user_id: int,
        params: ChangePasswordParams,
        client_ip: str,
        user_agent: str | None,
    ) -> None:
        """修改密码（登录用户改自己的密码）。

        ⚠️ 补充约定（spec 4.7，文档未定义）：
        - **原密码错误 → 复用 1002**（语义最近的"密码错误"）
        - **新密码少于 8 位 → 2001**（由 ChangePasswordParams 的 Pydantic 校验兜住）
        - **新旧密码相同 → 2001**
        - **一期不支持"改密后令旧令牌立即失效"**（无黑名单/无 token_version 字段）

        ⛔ sys_operation_log 的 before_value/after_value **绝对不写密码哈希**
           （审计表是给人和论文看的，写入哈希等于把凭据留在日志表）。

        Raises:
            BusinessError(1002): 原密码错误
            BusinessError(2001): 新旧密码相同
        """
        user = repo.get_user_by_id(self.session, user_id)
        if user is None:
            raise BusinessError(ErrorCode.LOGIN_FAILED, "用户不存在")

        # 校验原密码
        if not verify_password(params.old_password, user.password_hash):
            raise BusinessError(ErrorCode.LOGIN_FAILED, "原密码错误")

        # 新旧密码不能相同（防止"改了个寂寞"）
        if params.old_password == params.new_password:
            raise BusinessError(ErrorCode.REQUIRED_MISSING, "新密码不能与原密码相同")

        # 生成新哈希（bcrypt cost=10，与既有哈希一致）
        new_hash = hash_password(params.new_password)
        repo.update_password(self.session, user_id, new_hash)

        # 写审计日志 —— ⛔ before/after 都不写哈希
        repo.write_operation_log(
            self.session,
            user_id=user.id,
            user_name=user.real_name or user.username,
            module="认证",
            action="修改密码",
            target_type="sys_user",
            target_id=user.id,
            before_value={"password": "***"},  # 明确脱敏，不写哈希
            after_value={"password": "***"},
            ip=client_ip,
            user_agent=user_agent,
            result=1,
        )
        self.session.commit()
        logger.info(f"密码修改成功：user={user.username}(id={user.id}), ip={client_ip}")

    # ---------- 4.6 POST /auth/logout ----------

    def logout(self, user_id: int, username: str) -> None:
        """登出（一期实现为**空操作**，由前端清除本地令牌）。

        ⚠️ 取舍说明（spec 4.6，务必写清）：
        - docs/06 §1.2 说"服务端可将令牌加入黑名单"（**可选**，非强制）
        - 本项目**不引入 Redis**（决策 10），也没有令牌黑名单表
        - 一期实现：返回成功，由前端 clearAuth() 清除本地令牌
        - ⛔ **不要偷偷加内存黑名单**：多进程部署下它会失效，反而制造虚假安全感
        - 若确实需要服务端失效，只能走"新增令牌黑名单表 / 引入 Redis"两条路，本期都不做

        这是 JWT 无状态设计的**固有代价**，不是缺陷。
        """
        # 仅记一条日志，不做任何数据库变更
        logger.info(f"登出：user={username}(id={user_id})（前端清除本地令牌）")
