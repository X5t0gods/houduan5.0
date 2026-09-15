"""应用配置模块 - 唯一配置入口。

所有配置项从环境变量 / .env 文件读取，禁止硬编码密钥。
使用 pydantic-settings 的 BaseSettings 实现类型安全的配置管理。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置，字段与 .env 一一对应。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ---------- 应用 ----------
    APP_NAME: str = "CSSMS"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "dev"  # dev / test / prod
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ---------- 数据库 ----------
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "community_supermarket"
    DB_CHARSET: str = "utf8mb4"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600
    DB_ECHO: bool = False

    # ---------- 安全（本阶段只读取，JWT 实现留到阶段 2） ----------
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ---------- CORS ----------
    # 逗号分隔字符串，解析为 list[str]
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- 日志 ----------
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"

    # ---------- 时区 ----------
    TZ: str = "Asia/Shanghai"

    @property
    def database_url(self) -> str:
        """拼接 SQLAlchemy 数据库连接 URL。

        Returns:
            格式为 mysql+pymysql://user:password@host:port/dbname?charset=utf8mb4
        """
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?charset={self.DB_CHARSET}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的 CORS_ORIGINS 解析为列表。

        Returns:
            去除空白后的源地址列表。
        """
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """获取配置单例（带缓存，避免重复解析 .env）。

    Returns:
        Settings 实例。
    """
    return Settings()


# 模块级单例，供全局直接 import 使用
settings = get_settings()
