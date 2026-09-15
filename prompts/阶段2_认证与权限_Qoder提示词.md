# Qoder 提示词 · 阶段 2：认证与权限（6 接口 + RBAC）

> **用法**：在 Qoder 里打开 `C:\Users\徐炜城\Desktop\工作空间\houduan`，切到 **Agent / Quest 模式**，把下面 `======` 之间的内容整段粘贴发送。
>
> **前置条件**：阶段 0（工程骨架）与阶段 1（62 表模型 + 单号生成）均已完成并通过验收，数据库已建好（`db/01_schema.sql` + `02_init_data.sql`）。
>
> **建议分两次发**：先发「请只读这三个文件：`db/02_init_data.sql` 第 104–232 行、`db/01_schema.sql` 的 `sys_user`/`sys_permission`/`sys_login_log`/`sys_captcha` 四张表、`qianduan/src/stores/user.ts`，然后回答：① 权限树有几条记录、怎么区分目录与功能权限？② 管理员登录后 `permissions` 应该返回什么？③ `permissions` 与 `roles` 分别给前端做什么用？先不要改任何文件」。
> 等它答对（32 条记录、`perm_type` 1=目录 / 2=功能、管理员返回 `['*']`、permissions 生成菜单、roles 是**显示给人看的角色名**）再发正式提示词。这三条是最容易写错的地方。

---

======

## 你的角色与本次任务

你是一名资深 Python 后端工程师，在 FastAPI + SQLAlchemy 2.0 上做认证与权限模块。

工作区 `houduan` 是「中小型社区超市销售管理系统」的后端工程。阶段 0（骨架）与阶段 1（数据层）已完成，现在做**阶段 2：认证与权限模块**——**6 个接口 + 一套 RBAC 依赖**。

阶段目标（一句话）：**登录能进、菜单正确、越权拦住。**

接口清单（全部来自 `docs/06_后端接口文档.md` 3.1 节）：

| # | 方法 | 路径 | 权限 |
| :--: | --- | --- | --- |
| 1 | GET | `/auth/captcha` | 无需登录 |
| 2 | POST | `/auth/login` | 无需登录 |
| 3 | POST | `/auth/logout` | 登录即可 |
| 4 | POST | `/auth/refresh` | 无需登录（凭 refresh_token） |
| 5 | GET | `/auth/me` | 登录即可 |
| 6 | POST | `/auth/password` | 登录即可 |

---

## 一、开工前必读

| 顺序 | 文件 | 要确认的内容 |
| :--: | --- | --- |
| 1 | `db/02_init_data.sql` 第 **104–232 行** | 5 个角色的 `data_scope`、32 条权限记录、每个角色的权限分配、5 个演示账号与用户-角色关系 |
| 2 | `db/01_schema.sql` 的 `sys_user` / `sys_role` / `sys_permission` / `sys_user_role` / `sys_role_permission` / `sys_captcha` / `sys_login_log` / `sys_operation_log` | 字段名、可空、可用的登录态字段（`login_fail_count`/`locked_until`/`last_login_at`/`last_login_ip`） |
| 3 | `docs/06_后端接口文档.md` **2.2 节**（LoginParams / LoginResult / UserProfile）与 **3.1 节**（6 个接口）、**1.2 节**（认证机制）、**1.4 节**（错误码） | 请求/响应字段与错误码 |
| 4 | `qianduan/src/stores/user.ts`、`qianduan/src/router/guard.ts`、`qianduan/src/components/layout/HeaderBar.vue` 第 141 行、`qianduan/src/api/request.ts` | 前端如何消费登录结果（**这是行为真相源**） |
| 5 | 阶段 0 的 `app/core/security.py`、`app/core/permissions.py`、`app/core/exceptions.py` | 哪些已存在、需要扩展什么 |

### 1.1 实测事实（已逐条核对，作为你的实现基准）

**权限模型（`sys_permission` 共 32 条）**

| perm_type | 含义 | 数量 | perm_code 形态 |
| :--: | --- | :--: | --- |
| `1` | 目录（仅用于权限树层级展示） | 3 | `menu:daily`、`menu:stock`、`menu:analysis` |
| `2` | **功能权限（页面级）** | 29 | `dashboard:view`、`pos:use`、`goods:product:list` … |

> ⚠️ 全库**没有任何 `perm_type = 3` 的记录**（`docs/06` 与建表脚本注释里写的"3 按钮"实际未被使用）。**`/auth/me` 只返回 `perm_type = 2` 的权限码。**
> 这条依据来自 `02_init_data.sql` 第 117–119 行的权威注释：「`perm_type`: 1 目录 / 2 菜单（页面级权限）……后端返回 `/auth/me` 的 `permissions` 时应只返回 `perm_type = 2` 的权限码」。

**各账号的期望权限（`/auth/me` 的 `permissions` 输出，按此逐条断言）**

| 账号 | 角色 | 数据库 `sys_role_permission` 记录数 | **接口应返回的权限码个数** |
| --- | --- | :--: | :--: |
| `admin` | ADMIN | 32 | **`["*"]`**（通配符，见下） |
| `manager01` | MANAGER | 29 | **26**（29 个功能码 − `sys:user`/`sys:role`/`sys:config`） |
| `cashier01` | CASHIER | 7 | **6** |
| `stocker01` | STOCKER | 11 | **9** |
| `buyer01` | PURCHASER + MANAGER（一人多岗） | 10 + 29 | **26**（两角色取并集；PURCHASER 的 7 个码是 MANAGER 的子集） |

> ⚠️ 注意区分两个数字：`db/README.md` 写的「32 / 29 / 7 / 11 / 10」是**数据库里的关联记录数**（含 3 个目录节点），**不是接口返回值**。接口只返回 `perm_type = 2` 的码，所以是 `['*'] / 26 / 6 / 9 / 26`。别按前者写断言。

**演示账号**：5 个，密码统一 `123456`，哈希是 `$2b$10$...`（bcrypt cost=10），**可直接用 `bcrypt.checkpw` 校验，不要重新生成哈希**。

### 1.2 三处契约冲突（**以这里给出的口径为准，不要按文档字面实现**）

| 冲突点 | `docs/06` 的字面描述 | 前端实际行为（真相源） | **你要实现的口径** |
| --- | --- | --- | --- |
| **① 管理员的 `permissions`** | 只说"权限码数组" | `stores/user.ts`：`isAdmin = permissions.includes('*')`，注释写明「超级管理员拥有全部权限（**后端返回通配符 `*`**）」；Mock 登录与 `/auth/me` 均返回 `['*']` | **`role_code = 'ADMIN'` 时返回 `["*"]`**，不要返回 32 个码 |
| **② `roles` 字段语义** | §2.2 UserProfile 写的是「**角色编码**数组（如 `['ADMIN']`）」 | `HeaderBar.vue` 第 141 行直接展示：`{{ userStore.roles.join(' / ') }}`；Mock 返回 `role_names`（`['系统管理员']`、`['店长', '采购员']`） | **返回角色名称数组**（`sys_role.role_name`，按 `sys_role.sort` 排序）。若返回 `ADMIN`，界面会显示成英文编码 |
| **③ `perm_type` 语义** | §2.2 写「1 目录 / 2 菜单 / **3 按钮**」 | 数据库中只有 1 和 2，从无 3 | 按 **1=目录、2=功能权限** 处理，**只返回 `perm_type = 2`** |

> 交付说明里请把这三条**单独列出来**（文档与实现不一致处），方便后续统一修订接口文档。

### 1.3 系统参数（**不要硬编码这些策略**）

登录锁定等策略已存在 `sys_config` 表中，**运行时读取**，读不到再用兜底默认值：

| config_key | 默认值 | 用途 |
| --- | --- | --- |
| `sys.login_fail_limit` | `5` | 连续登录失败锁定阈值 |
| `sys.login_lock_minutes` | `15` | 锁定时长（分钟） |
| `sys.session_timeout` | `120` | 会话超时（分钟）→ 与 `access_token` 有效期对齐（120 × 60 = **7200** 秒 = Mock 里的 `expires_in`） |

---

## 二、已定技术决策（不得更改）

1. 同步 SQLAlchemy 2.0 + PyMySQL，路由用 `def`（延用阶段 0）。
2. **JWT 用 `PyJWT`**（不要用 `python-jose`，也不要用 `passlib`）。
3. **Token 里只放 `sub`（用户 ID）、`username`、`type`（`access`/`refresh`）、`iat`、`exp`、`jti`**。
   ⛔ **不要把 `permissions` 或 `roles` 塞进 token**：权限会随「角色与权限」页面变更而失效，塞进 token 后旧令牌会一直拿着旧权限，越权问题很难排查。权限每次从数据库查（两张关联表 JOIN，走索引，开销可接受）。
4. **业务失败一律 HTTP 200 + 非 0 `code`**；⛔ **登录失败（1001/1002/1003/1004）绝对不能返回 HTTP 401**——前端的 401 分支会清登录态并跳转登录页，把用户"踢"出去。
   HTTP 401 只用于"**需要一个有效登录态但没拿到**"：token 缺失、签名错、已过期、用户已停用。
5. 时段与时区：`Asia/Shanghai`（沿用阶段 0 的序列化约定）。
6. **不修改前端代码、不修改 `docs/` 下的文档、不修改 `db/*.sql`**（表结构阶段 1 已定稿；`sys_captcha`/`sys_login_log`/`sys_operation_log` 字段已够用）。

---

## 三、交付物清单

```
houduan/app/
├── core/
│   ├── security.py              # ← 扩展：JWT 签发与解析（bcrypt 已有）
│   ├── permissions.py           # ← 补充 3 个目录码常量与 perm_type 常量
│   ├── config.py                # ← 确认 JWT 相关配置齐全（阶段 0 已定义）
│   └── sys_config_reader.py     # ← 新增：读取 sys_config 的小工具（带 TTL 缓存）
├── schemas/
│   └── auth.py                  # ← 新增：LoginParams/LoginResult/UserProfile/... 响应模型
├── repositories/
│   └── auth_repository.py       # ← 新增：用户/角色/权限/验证码/日志的数据访问
├── services/
│   └── auth_service.py          # ← 新增：验证码、登录、令牌、改密、权限装配的业务编排
├── api/v1/
│   ├── auth.py                  # ← 新增：6 个接口
│   └── router.py                # ← 注册 auth 路由（tag「认证与权限」）
├── deps.py                      # ← 扩展：get_current_user / require_perm / PageParams（已有）
└── middleware/
    └── request_id.py            # 不动

houduan/tests/
├── test_services/test_auth_service.py     # 单元：锁定策略、权限装配、JWT
├── test_api/test_auth.py                  # 集成：完整登录链路 + 5 个账号权限断言
└── test_api/test_permission_guard.py      # 集成：401 / 403 边界
```

分层要求（沿用阶段 0）：**路由只做参数校验与权限声明 → service 编排业务与事务 → repository 访问数据**。⛔ 路由里不许出现 SQL/ORM 查询，repository 里不许 `commit`。

---

## 四、逐个交付物的要求

### 4.1 `app/core/security.py` 扩展 —— JWT

在阶段 0 已有的 `hash_password` / `verify_password` 基础上追加：

| 函数 | 要求 |
| --- | --- |
| `create_access_token(user_id: int, username: str) -> tuple[str, int]` | 返回 `(token, expires_in_seconds)`，`expires_in` 取 `settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60` |
| `create_refresh_token(user_id: int, username: str) -> str` | 有效期 `settings.REFRESH_TOKEN_EXPIRE_DAYS` 天 |
| `decode_token(token: str, *, expected_type: str) -> TokenPayload` | 校验签名与过期；`type` 不匹配（拿 refresh 当 access 用）**必须拒绝** |

要点：
- ⛔ `jwt.decode` 必须显式传 `algorithms=[settings.JWT_ALGORITHM]`（防算法混淆攻击），不要省略；
- 捕获 `ExpiredSignatureError` 与 `InvalidTokenError`，**统一转成业务异常/401**，不要泄漏底层异常；
- `sub` 在 JWT 标准里是字符串，**写入时 `str(user_id)`、解析时转回 `int`**，避免类型不一致导致查不到用户；
- `jti` 用 `uuid4().hex`（为将来做令牌失效预留）；
- 启动时自检：`DEBUG=False` 且 `JWT_SECRET_KEY` 仍是 `.env.example` 里的占位值时，**直接抛异常拒绝启动**（防止演示环境带着默认密钥上线）。

### 4.2 `GET /auth/captcha` —— 图形验证码

响应 `data`：`{"captcha_key": "...", "captcha_image": "data:image/png;base64,..."}`

实现要求：
1. 生成 **4 位**验证码，字符集**排除易混淆字符**（去掉 `0 O 1 I l`），统一大写；
2. 用 **Pillow** 生成 PNG（约 120×40）：文字 + 少量干扰线/噪点即可，**不要过度设计**；
   ⚠️ Windows 下不要写死字体文件路径（`arial.ttf` 可能不存在）；优先 `ImageFont.load_default()`，拿不到再尝试系统字体并**捕获异常降级**；
3. 写 `sys_captcha`：`captcha_key`（`uuid4().hex`）、`captcha_code`、`ip`、`used=0`、`expire_at = 当前时间 + 2 分钟`；
4. 返回 Base64 Data URL（`data:image/png;base64,` 前缀不能少，前端直接当 `img.src` 用）；
5. **顺带清理**该表已过期记录（低频执行即可），并在注释里注明：阶段 8 会有定时任务统一接管清理；
6. 接口**无需登录**，也不参与权限校验。

### 4.3 `POST /auth/login` —— 登录（**本阶段最核心**）

**请求**：`LoginParams` = `username`、`password`、`captcha_key`、`captcha_code`（四者皆必填）
**响应**：`LoginResult` = `access_token`、`refresh_token`、`token_type: "Bearer"`、`expires_in`、`user_profile: UserProfile`

**校验顺序（严格按这个顺序，每一步的顺序都有原因）**：

| 步骤 | 条件 | 返回 |
| :--: | --- | --- |
| 1 | 验证码：`captcha_key` 不存在 / `used=1` / 已过期 / 码不匹配（不区分大小写） | `1001` |
| 2 | 用户不存在 | `1002`（**不要提示"用户不存在"**，与密码错误共用文案，防账号枚举） |
| 3 | `locked_until > 当前时间` | `1003` |
| 4 | 密码校验失败 | `login_fail_count += 1`；若达到阈值则同时写入 `locked_until = now + N 分钟` 并返回 `1003`，否则返回 `1002` |
| 5 | `status = 0`（停用） | `1004` |
| 6 | 全部通过 | 登录成功 |

设计说明（请写进代码注释）：
- **验证码放在第 1 步**：先挡住自动化爆破，避免攻击流量打到用户表和失败计数上；
- **停用判断放在密码校验之后**：若先判断停用，任何人都能通过"账号是否报停用"来枚举有效账号；
- **锁定判断放在密码校验之前**：锁定期间的任何尝试都直接 `1003`，不再累加计数。

**登录成功后**（同一事务内）：
1. `login_fail_count = 0`、`locked_until = NULL`、`last_login_at = now`、`last_login_ip = 客户端 IP`；
2. 验证码置 `used = 1`（**一 key 只能用一次**）；
3. 取客户端 IP 时注意代理头（`X-Forwarded-For` 首个值 → 退回 `request.client.host`），长度不要超过字段上限（`VARCHAR(45)`）；
4. 组装 `user_profile`（见 4.4 的装配规则）。

**无论成功失败都要写 `sys_login_log`**：`username`、`user_id`（成功时）、`ip`、`user_agent`（截断到 255）、`result`（1/0）、`fail_reason`（如"验证码错误""密码错误""账号已锁定"）。

**幂等/事务**：登录涉及"用户表更新 + 验证码表更新 + 日志表插入"，**放在同一事务**；但**登录失败也要能写出日志**（验证码校验失败时用户表无需更新），注意别因为提前抛异常把日志一起回滚了——**建议日志走独立事务或在异常处理前先写**，并把这一处理写进注释。

### 4.4 `GET /auth/me` —— 当前用户与权限

响应 `UserProfile`：

| 字段 | 取值规则 |
| --- | --- |
| `id` / `username` / `real_name` / `phone` / `avatar` | 直接取 `sys_user`（`phone` / `avatar` 可为空，前端类型是可选） |
| `store_id` | `sys_user.store_id` |
| `store_name` | JOIN `base_store.store_name`（无门店时为 `null`） |
| `roles` | **角色名称数组**，取 `sys_role.role_name`，按 `sys_role.sort` 升序（`buyer01` → `['店长', '采购员']`） |
| `permissions` | **`role_code = 'ADMIN'` → `["*"]`**；否则取该用户全部角色的权限码**并集**，且**只含 `perm_type = 2`** 的记录，去重 |

- 该接口"登录即可"，**不加任何权限码校验**；
- 页面刷新时前端会调它恢复登录态，所以它必须是**权限的唯一权威来源**：角色或权限被改动后，刷新页面即可生效（这也是不要把权限放进 token 的原因）。

### 4.5 `POST /auth/refresh`

请求 `{"refresh_token": "..."}` → 响应 `{"access_token": "...", "expires_in": 7200}`

- 只接受 `type = "refresh"` 的令牌；用 access token 来刷新必须失败；
- 刷新时重新查一次用户：**已停用/已删除的用户不允许刷新**（否则停用形同虚设）；
- 返回值**只有两个字段**，不要顺手把 `refresh_token` 或 `user_profile` 也塞进去（前端类型里没有）。

### 4.6 `POST /auth/logout`

- 响应 `data: null`；
- 需求文档 1.2 节写的是"**服务端可将令牌加入黑名单**"（可选，非强制）。本项目**不引入 Redis**，且没有令牌黑名单表，因此一期实现为：**返回成功，由前端清除本地令牌**；
- 请在代码注释与交付说明中明确写出这个取舍（JWT 无状态设计的固有代价），**不要为了"看起来更强"去偷偷加内存黑名单**——多进程部署下它会失效，反而制造虚假安全感；
- 若确实要实现服务端失效，只能走"新增令牌黑名单表 / 引入 Redis"两条路，**本期都不做**。

### 4.7 `POST /auth/password`

请求 `{"old_password": "...", "new_password": "..."}` → 响应 `data: null`

- 校验原密码，错误时返回 `1002`，**并在交付说明里标注：文档未定义"原密码错误"的错误码，本次复用 `1002`（语义最近的"密码错误"），属补充约定**；
- 新密码规则：**不少于 8 位**（`docs/06` 3.1.6 明确要求），不满足返回 `2001`，`message` 说明原因；
  ℹ️ 注意别被演示账号带偏：演示账号初始密码是 6 位的 `123456`，这是**初始密码**，不受"新密码 ≥ 8 位"约束（改密时才校验）；
- 新密码不得与原密码相同（返回 `2001`）；
- 更新 `password_hash`（重新 `bcrypt.hashpw`，cost=10，与既有哈希一致）；
- **写 `sys_operation_log`**：`module='认证'`、`action='修改密码'`、`target_type='sys_user'`、`target_id=用户ID`、`user_id`/`user_name` 取当前登录用户、`result=1`。
  ⛔ `before_value` / `after_value` **绝对不要写入密码哈希**（写 `null` 或 `{"password": "***"}`），这是审计表，别把凭据留在里面；
- 一期不支持"改密后令旧令牌立即失效"（无黑名单/无 `token_version` 字段），请在交付说明中列为已知限制。

### 4.8 `app/deps.py` 扩展 —— 认证与权限依赖

| 依赖 | 行为 |
| --- | --- |
| `get_current_user()` | 解析 `Authorization: Bearer <token>`；缺失/格式错/签名错/过期 → **HTTP 401**；`type` 必须是 `access`；用户不存在或 `status = 0` → **HTTP 401**；成功则返回一个用户上下文对象（`user_id`、`username`、`real_name`、`store_id`、`role_codes`、`permissions`、`data_scope`） |
| `require_perm(*codes: str)` | 依赖工厂。用户权限含 `"*"` 直接放行；否则命中任一 `code` 放行；都不命中 → **HTTP 403**（响应体仍用统一结构，`code` 填 `1040`） |

要点：
- 401 与 403 的响应体**也要带 `code` / `message` / `request_id`**（阶段 0 的异常处理器已支持，确保走同一条通道）；
- 权限查询抽成 `auth_repository.get_user_permissions(user_id)` 一个方法，`/auth/me` 与 `require_perm` **共用它**（避免两处逻辑漂移）；
- `data_scope` 取该用户**所有角色中范围最大的那个**（优先级 `ALL > STORE > DEPT > SELF`），理由：一人多岗时权限取并集；
- 可选优化：给权限查询加 60 秒进程内 TTL 缓存——**若加，必须注释说明多 worker 下不一致**（阶段 0 决策 10）。不加也可以，**默认不加**。

### 4.9 数据范围骨架（只搭架子，不接业务查询）

提供一个纯函数（放 `app/core/permissions.py` 或 `app/deps.py`）：

```
resolve_store_filter(user_ctx) -> int | None
```

- `data_scope = 'ALL'` → `None`（不加门店条件）
- `data_scope = 'STORE'` / `'DEPT'` → 返回 `user_ctx.store_id`
- `data_scope = 'SELF'` → 返回 `user_ctx.store_id`，并额外用 `user_id` 过滤（由调用方传入用户维度字段）

本阶段**只在单元测试里验证这个函数的映射关系**，不要接入任何业务查询（阶段 3 起由各 repository 调用）。

### 4.10 `app/core/permissions.py` 补充

- 保留阶段 0 已定义的 29 个功能权限码常量；
- **新增 3 个目录码常量**：`menu:daily`、`menu:stock`、`menu:analysis`（并注明"仅用于权限树层级展示，不进入 `/auth/me` 的 permissions"）；
- 新增 `perm_type` 常量：`PERM_TYPE_DIRECTORY = 1`、`PERM_TYPE_FUNCTION = 2`，并在注释里写明"数据库中不存在 3（按钮），文档描述与实现不一致"。

### 4.11 测试

| 文件 | 覆盖内容 |
| --- | --- |
| `tests/test_services/test_auth_service.py` | ① 验证码：过期 / 已使用 / 大小写不敏感 / 错误码 `1001`；② 锁定策略：连续 4 次失败返回 `1002`、第 5 次返回 `1003` 且 `locked_until` 已写入、锁定期间直接 `1003`、成功后计数清零；③ 校验顺序：停用账号 + 错误密码 → `1002`（不泄漏停用状态）、停用账号 + 正确密码 → `1004`；④ 权限装配：5 个账号的 `permissions` 与 `roles`（见 1.1 表）逐条断言；⑤ 目录码不进入 permissions |
| `tests/test_api/test_auth.py` | 真实数据库的完整链路：`GET /auth/captcha` → **从 `sys_captcha` 表读出明文验证码**（仅测试用）→ `POST /auth/login` → 用返回的 token 调 `GET /auth/me`；断言 `token_type="Bearer"`、`expires_in=7200`、`store_name="朝阳路店"`；`POST /auth/refresh` 换新 token；`POST /auth/password` 改密后能用新密码登录、旧密码失败（**测试用 fixture 事务回滚，不要污染演示数据**） |
| `tests/test_api/test_permission_guard.py` | 无 token → `401`；伪造/过期 token → `401`；用 `cashier01` 的 token 访问需要 `sys:user` 的**调试路由** → `403`（`code=1040`）；`admin` 访问同一路由 → 通过 |

**为了能测 401/403，请在 `DEBUG=true` 时注册一个仅供调试的受保护路由**（与阶段 0 的 `_dev` 路由同一套路）：

`GET /api/v1/_dev/authorized`，声明 `dependencies=[Depends(require_perm("sys:user"))]`，返回 `{"ok": true}`。
`DEBUG=false` 时该路由**必须不存在**。

✅ 这个调试路由是"用最小代价验证权限依赖真的生效"的手段，比编造一个业务接口更干净。阶段 3 起会删除它。

---

## 五、编码规范（沿用阶段 0，不放宽）

1. 完整类型注解；公开函数 `Args` / `Returns` / `Raises` docstring；
2. 注释解释**为什么**——尤其把 1.2 节的三处契约冲突、4.3 节的校验顺序、4.6 节的登出取舍写成代码注释，防止后续被"顺手改掉"；
3. 不裸 `print`、不吞异常；异常信息**不要回显给前端**（避免把 SQL/堆栈暴露出去）；
4. 日志里**不要打印密码、验证码明文、token 全文**；
5. 中文注释、英文标识符；`ruff check .` 零告警。

---

## 六、明确不做的事（越界即返工）

1. ⛔ 不实现 128 个接口里的其他接口（`/system/users`、`/system/roles`、`/system/permissions` 属阶段 10）；
2. ⛔ 不写角色/权限的**增删改**（那是阶段 10 的用户管理页）；
3. ⛔ 不改前端代码（尤其不要动 `request.ts` 的 401 处理与 `guard.ts` 的菜单过滤）；
4. ⛔ 不改 `docs/` 下文档、不改 `db/*.sql`（含不要给 `sys_user` 加 `token_version` 字段）；
5. ⛔ 不引入 Redis / Celery / OAuth / SSO / 短信验证码 / 登录图形滑块；
6. ⛔ 不做令牌黑名单（见 4.6）；
7. ⛔ 不要把权限写进 JWT；
8. ⛔ 不装 pandas / mlxtend / openpyxl / apscheduler（验证码清理暂用低频顺带清理）；
9. ⛔ 不做多余抽象（不要为 6 个接口造通用认证框架），清晰可读优先。

---

## 七、实施顺序

| 步 | 动作 | 完成判据 |
| :--: | --- | --- |
| 1 | `app/schemas/auth.py` + `app/core/security.py` JWT 扩展 | JWT 单元测试通过（签发/解析/过期/类型不符/签名错） |
| 2 | `app/repositories/auth_repository.py` | 能查出用户、角色、权限码、门店名；能读写验证码与两类日志 |
| 3 | `app/services/auth_service.py` + `sys_config_reader.py` | 验证码、登录、令牌、改密的单元测试全绿 |
| 4 | `app/deps.py` 的 `get_current_user` / `require_perm` | 401 / 403 边界测试通过 |
| 5 | `app/api/v1/auth.py` + 注册路由 | 6 个接口在 `/docs` 里可见且带中文说明 |
| 6 | 调试路由 + 权限数据范围骨架 + 集成测试 | 5 个账号权限断言全绿 |

---

## 八、验收标准（逐条可验证）

| # | 验收项 | 判定方式 |
| :--: | --- | --- |
| 1 | 验证码可用 | `GET /api/v1/auth/captcha` 返回 `captcha_key` 与 `data:image/png;base64,...`；把 base64 存成 png 能打开并看清 4 位字符 |
| 2 | **验证码一次性** | 同一个 `captcha_key` 登录两次，第二次返回 `1001`（且是 **HTTP 200**） |
| 3 | 5 个账号都能登录 | `admin` / `manager01` / `cashier01` / `stocker01` / `buyer01` 用 `123456` 全部登录成功，贴出各自的 `expires_in` 与 `token_type` |
| 4 | **管理员通配符** | `admin` 的 `user_profile.permissions` 等于 `["*"]`（**不是** 32 个码） |
| 5 | **权限码数量正确** | `manager01`=26、`cashier01`=6、`stocker01`=9、`buyer01`=26（贴出每个账号的完整 `permissions` 数组） |
| 6 | **roles 是角色名** | `buyer01` 的 `roles` 为 `["店长", "采购员"]`（中文名、按 sort 排序，**不是** `PURCHASER`） |
| 7 | 目录码不泄漏 | 任何账号的 `permissions` 里都不含 `menu:daily` / `menu:stock` / `menu:analysis` |
| 8 | **登录失败是 200** | 错误密码用 `curl -i` 验证：**HTTP/1.1 200** + `code=1002`；账号停用返回 200 + `1004` |
| 9 | 失败锁定生效 | 连续 5 次错误密码：第 4 次 `1002`、第 5 次 `1003`；查库确认 `login_fail_count=5`、`locked_until` 已写入；第 6 次直接 `1003` |
| 10 | 登录日志完整 | `sys_login_log` 里成功与失败都有记录，`fail_reason` 可读，`ip` 不为空 |
| 11 | `/auth/me` 恢复登录态 | 用登录返回的 token 调 `/auth/me`，字段与登录时的 `user_profile` 一致（含中文 `store_name`） |
| 12 | 令牌边界 | 无 token → 401；把 token 末尾改一个字符 → 401；用 `refresh_token` 当 access 用 → 401；`POST /auth/refresh` 只接受 refresh token |
| 13 | **越权拦住** | `cashier01` 的 token 访问 `/api/v1/_dev/authorized`（需 `sys:user`）→ **403 + code 1040**；`admin` 访问同一路由 → 200 |
| 14 | 调试路由不泄漏 | `DEBUG=false` 启动时 `/api/v1/_dev/authorized` 返回 404 |
| 15 | 改密闭环 | 改密成功后新密码可登录、旧密码 `1002`；`sys_operation_log` 有记录且 **JSON 里不含密码哈希** |
| 16 | 前端真联调 | 前端 `.env.development` 设 `VITE_USE_MOCK=false` + `VITE_API_BASE=http://127.0.0.1:8000`，用 5 个账号分别登录：左侧菜单按角色显示、**页头显示中文角色名**、URL 直达无权限的页面为 404 |
| 17 | 测试与质量 | `pytest -q` 全绿；`ruff check .` 零告警 |

> 第 16 条是本阶段的**最终验收**：菜单能不能出来、角色名显示对不对，只能靠真前端验证，接口自测通过不代表联调通过。

---

## 九、交付时请附上

1. **自检报告**：第八节逐项「验收项 → 实际结果 → 验证命令 + 真实输出」，**不要只写"已完成"**；
2. **5 个账号的完整 `permissions` 数组**（贴真实返回值，注明元素个数）；
3. **锁定策略的实测输出**（连续 5 次失败的 `code` 序列 + 数据库里 `login_fail_count` / `locked_until` 的值）；
4. **三处契约冲突的处理说明**（1.2 节表格），以及你选择的实现口径；
5. **补充约定清单**：文档未定义、你自行决定的规则（至少含：原密码错误的错误码、新密码规则、登出无服务端失效、改密后旧令牌不失效）；
6. **前端联调结论**：5 个账号分别登录后的菜单渲染结果与页头角色名截图/文字记录；
7. **踩坑记录**：实际遇到的问题与解决办法。

======

---

## 附：这份提示词埋的"防幻觉"点

| 位置 | 埋的点 | 为什么 |
| --- | --- | --- |
| 1.1 节 | 明确「`perm_type` 只有 1 和 2，从无 3」 | 建表脚本注释与接口文档都写着"1 目录 2 菜单 3 按钮"，只有 `02_init_data.sql` 的实际数据与注释是对的。不点明，它一定会写出 `filter(perm_type.in_([2,3]))` 之类的代码 |
| 1.1 节 | 给出 `['*'] / 26 / 6 / 9 / 26` 这组数字，并解释与 `db/README` 的 `32/29/7/11/10` 的差别 | 这是两个不同的口径（数据库记录数 vs 接口返回数）。不区分，验收时必然"对不上数"而反复返工 |
| 1.2 节 | `roles` 返回角色名而非编码 | `docs/06` 白纸黑字写"角色编码"，只有 `HeaderBar.vue` 第 141 行的实际使用和 Mock 才暴露出真相。照文档做，页头会显示 "ADMIN / MANAGER" |
| 4.3 节 | 强制写清校验顺序，并解释每步顺序的原因 | 顺序错了会出现两类问题：停用状态可被枚举、"锁定"账号仍能继续撞库。这是安全逻辑，不能靠 AI 随机排列 |
| 4.3 节 | 提醒"登录失败也要能写出日志" | 新手（和 AI）常把日志写在同一个事务里，一抛异常日志一起回滚，结果失败记录全丢 |
| 4.6 节 | 明确禁止"偷偷加内存黑名单" | AI 倾向于给自己加戏。多进程下内存黑名单会失效，制造"登出已生效"的假象 |
| 4.7 节 | 禁止把密码哈希写进 `before_value`/`after_value` | 审计表是给人和论文看的，写入哈希等于把凭据留在了日志表 |
| 4.8 节 | 提示 401 与 403 的响应体也要统一结构 | 阶段 0 定的统一响应体容易被绕过，前端拿不到 `request_id` 就没法定位问题 |
| 验收 8 | 要求 `curl -i` 亲眼看 HTTP 状态码是 200 | **这是本阶段最容易翻车的点**：登录失败返回 401，前端会把用户踢回登录页。必须用命令证据证明，不能靠嘴说 |
| 验收 13/14 | 用 DEBUG 调试路由验证 401/403，并要求生产环境 404 | 不发明业务接口就能验证权限依赖；同时防止调试后门被带上线 |
| 验收 16 | 强制真前端联调 | 菜单与角色名只有真前端能验证，接口自测全绿不等于联调通过 |
