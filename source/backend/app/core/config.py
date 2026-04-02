from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    app_env: str = Field(default='dev', alias='APP_ENV')
    app_port: int = Field(default=8000, alias='APP_PORT')
    app_name: str = 'BetterPPT API'
    api_prefix: str = '/api/v1'
    auto_create_tables: bool = True

    mysql_host: str = Field(default='127.0.0.1', alias='MYSQL_HOST')
    mysql_port: int = Field(default=3306, alias='MYSQL_PORT')
    mysql_user: str = Field(default='root', alias='MYSQL_USER')
    mysql_password: str = Field(default='change_me', alias='MYSQL_PASSWORD')
    mysql_database: str = Field(default='betterppt', alias='MYSQL_DATABASE')
    database_url: str | None = None

    redis_host: str = Field(default='127.0.0.1', alias='REDIS_HOST')
    redis_port: int = Field(default=6379, alias='REDIS_PORT')
    redis_password: str = Field(default='', alias='REDIS_PASSWORD')
    redis_db: int = Field(default=0, alias='REDIS_DB')

    llm_api_base: str = Field(default='https://api.openai.com/v1', alias='LLM_API_BASE')
    llm_api_key: str = Field(default='', alias='LLM_API_KEY')
    llm_model: str = Field(default='gpt-4.1-mini', alias='LLM_MODEL')

    storage_provider: str = Field(default='local', alias='STORAGE_PROVIDER')
    local_storage_root: str = 'storage'
    upload_subdir: str = 'uploads'
    result_subdir: str = 'results'

    auth_default_user_id: int = 1

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            'mysql+pymysql://'
            f'{self.mysql_user}:{self.mysql_password}'
            f'@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4'
        )

    @property
    def redis_url(self) -> str:
        auth = f':{self.redis_password}@' if self.redis_password else ''
        return f'redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}'

    @property
    def storage_root_path(self) -> Path:
        return Path(self.local_storage_root).resolve()

    @property
    def upload_root_path(self) -> Path:
        return self.storage_root_path / self.upload_subdir

    @property
    def result_root_path(self) -> Path:
        return self.storage_root_path / self.result_subdir


@lru_cache
def get_settings() -> Settings:
    return Settings()
