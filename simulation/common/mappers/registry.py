"""Mapper registry — maps robot types to their CommandMapper."""
from .g1_mapper import G1Mapper
from .go2_mapper import Go2Mapper
from .spot_mapper import SpotMapper
from .atlas_mapper import AtlasMapper
from .generic_mapper import GenericMapper

MAPPER_REGISTRY = {
    "unitree_g1": G1Mapper,
    "g1": G1Mapper,
    "unitree_go2": Go2Mapper,
    "go2": Go2Mapper,
    "spot": SpotMapper,
    "atlas": AtlasMapper,
    "tron1": GenericMapper,
    "tron2": GenericMapper,
    "m20": GenericMapper,
    "m20-pro": GenericMapper,
    "x30": GenericMapper,
    "x30-pro": GenericMapper,
    "cra": GenericMapper,
    "reachy-mini": lambda: GenericMapper(n_actuators=8),
    "booster-k1": GenericMapper,
    "agibot-x2": GenericMapper,
}


def get_mapper(robot_type: str):
    """Get the CommandMapper for a robot type."""
    mapper_cls = MAPPER_REGISTRY.get(robot_type, GenericMapper)
    if callable(mapper_cls) and not isinstance(mapper_cls, type):
        return mapper_cls()
    return mapper_cls()
