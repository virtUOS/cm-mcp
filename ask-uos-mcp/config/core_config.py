from typing import ClassVar, Literal, Optional, Tuple, Type

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from log_conf.logger_setup import get_logger
logger =get_logger()

from .models import (
    CrawlSettings,
    AppConfig,
    Model,
)


class Settings(BaseSettings):
    """
    Settings class for application configuration.

    This class is a singleton that holds various configuration settings for the application.
    It inherits from `BaseSettings` and uses Pydantic for data validation and settings management.

    """

    _instance: ClassVar[Optional["Settings"]] = None

    models: list[Model]
    app: AppConfig
    crawl_settings: CrawlSettings

    model_config = SettingsConfigDict(yaml_file="/cm-mcp/ask-uos-mcp/config/ask_mcp_config.yml")
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(Settings, cls).__new__(cls)
        return cls._instance

    def __init__(self, **data):
        if not self.__dict__:
            super().__init__(**data)
            logger.debug(f"Settings initialized: {self.model_dump_json()}")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (YamlConfigSettingsSource(settings_cls),)


settings = Settings()
