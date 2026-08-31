"""Synthetic subscription generator and the sealed stochastic world model."""

from reclaimos.generator.generate import (
    GENERATOR_VERSION,
    Manifest,
    build_dataset,
    generate,
    read_manifest,
    read_records,
    read_world,
    split_records,
    write_records,
    write_world,
)
from reclaimos.generator.outcome_model import (
    MAX_SLOTS,
    RECOVERY_WINDOW_HOURS,
    WorldRecord,
    oracle_recovers,
    resolve,
    success_probability,
)

__all__ = [
    "GENERATOR_VERSION",
    "MAX_SLOTS",
    "RECOVERY_WINDOW_HOURS",
    "Manifest",
    "WorldRecord",
    "build_dataset",
    "generate",
    "oracle_recovers",
    "read_manifest",
    "read_records",
    "read_world",
    "resolve",
    "split_records",
    "success_probability",
    "write_records",
    "write_world",
]
