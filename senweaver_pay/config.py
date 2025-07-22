"""
配置管理模块，用于加载和管理支付配置
"""

import json
import os
from typing import Any, Dict

from .exceptions import InvalidConfigException
from .types import Config


class ConfigManager:
    """配置管理器，提供配置的加载、存储和访问功能"""

    def __init__(self):
        """初始化配置管理器"""
        self._config = Config()
        self._initialized = False

    def load_config(self, config: Dict[str, Any]) -> None:
        """
        加载配置
        :param config: 配置字典
        """
        self._config = Config(**config)
        self._initialized = True

    def load_from_file(self, file_path: str) -> None:
        """
        从文件加载配置
        :param file_path: 配置文件路径
        """
        if not os.path.isfile(file_path):
            raise InvalidConfigException(f"Config file not found: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            self.load_config(config)
        except json.JSONDecodeError as e:
            raise InvalidConfigException(f"Invalid JSON format in config file: {file_path}") from e
        except Exception as e:
            raise InvalidConfigException(f"Failed to load config file: {str(e)}") from e

    def get_config(self) -> Config:
        """
        获取配置对象
        :return: 配置对象
        """
        if not self._initialized:
            raise InvalidConfigException("Config not initialized")
        return self._config

    def get_channel_config(self, channel: str, app: str = "default") -> Dict[str, Any]:
        """
        获取指定渠道和应用的配置
        :param channel: 渠道名称
        :param app: 应用名称
        :return: 渠道配置
        """
        if not self._initialized:
            raise InvalidConfigException("Config not initialized")

        if not hasattr(self._config, channel):
            raise InvalidConfigException(f"Channel not configured: {channel}")

        channel_config = getattr(self._config, channel)
        if app not in channel_config:
            raise InvalidConfigException(f"App not configured: {app} for channel {channel}")

        return channel_config.get(app, {})

    def get_http_config(self) -> Dict[str, Any]:
        """
        获取HTTP配置
        :return: HTTP配置
        """
        if not self._initialized:
            raise InvalidConfigException("Config not initialized")
        return self._config.http.to_dict()

    def get_logger_config(self) -> Dict[str, Any]:
        """
        获取日志配置
        :return: 日志配置
        """
        if not self._initialized:
            raise InvalidConfigException("Config not initialized")
        return self._config.logger.to_dict()

    def has_channel(self, channel: str) -> bool:
        """
        检查是否配置了指定渠道
        :param channel: 渠道名称
        :return: 是否配置了渠道
        """
        if not self._initialized:
            return False
        return hasattr(self._config, channel) and getattr(self._config, channel)

    def is_initialized(self) -> bool:
        """
        检查配置是否已初始化
        :return: 是否已初始化
        """
        return self._initialized


# 全局配置管理器实例
config_manager = ConfigManager()
