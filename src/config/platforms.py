from dataclasses import dataclass

from src.core.config_manager import get_env


@dataclass
class ShopifyConfig:
    api_key: str
    password: str
    shop_name: str


@dataclass
class RakutenConfig:
    api_key: str


@dataclass
class MetaAdsConfig:
    access_token: str
    account_id: str


@dataclass
class FeishuConfig:
    bot_token: str


def load_shopify_config() -> ShopifyConfig:
    return ShopifyConfig(
        api_key=get_env("SHOPIFY_API_KEY", "") or "",
        password=get_env("SHOPIFY_PASSWORD", "") or "",
        shop_name=get_env("SHOPIFY_SHOP_NAME", "") or "",
    )


def load_rakuten_config() -> RakutenConfig:
    return RakutenConfig(api_key=get_env("RAKUTEN_API_KEY", "") or "")


def load_meta_ads_config() -> MetaAdsConfig:
    return MetaAdsConfig(
        access_token=get_env("META_ACCESS_TOKEN", "") or "",
        account_id=get_env("META_AD_ACCOUNT_ID", "") or "",
    )


def load_feishu_config() -> FeishuConfig:
    return FeishuConfig(bot_token=get_env("FEISHU_BOT_TOKEN", "") or "")

