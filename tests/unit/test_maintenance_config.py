from paw.providers.config import (
    EMBEDDING_KEY,
    MAINTENANCE_KEY,
    EmbeddingConfig,
    MaintenanceConfig,
)


def test_maintenance_config_defaults():
    cfg = MaintenanceConfig()
    assert cfg.enabled_ops == ["lint", "fix", "format", "reindex"]
    assert cfg.reindex_batch_size == 128
    assert cfg.stale_days == 180
    assert MAINTENANCE_KEY == "maintenance"


def test_maintenance_config_override_validates():
    cfg = MaintenanceConfig.model_validate({"enabled_ops": ["lint"], "stale_days": 30})
    assert cfg.enabled_ops == ["lint"]
    assert cfg.stale_days == 30
    assert cfg.reindex_batch_size == 128  # untouched default


def test_embedding_config_default_version():
    assert EmbeddingConfig().version == 1
    assert EMBEDDING_KEY == "embedding"
