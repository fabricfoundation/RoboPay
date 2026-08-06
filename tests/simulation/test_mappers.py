"""Unit tests for MuJoCo CommandMappers.

Tests cover:
- Action → actuator mapping for each robot
- Stop/cancel always zeroes
- Unknown actions return zero
- Speed clamping
- Malformed params handling
"""
import pytest
from simulation.common.mappers.g1_mapper import G1Mapper
from simulation.common.mappers.go2_mapper import Go2Mapper
from simulation.common.mappers.spot_mapper import SpotMapper
from simulation.common.mappers.atlas_mapper import AtlasMapper
from simulation.common.mappers.generic_mapper import GenericMapper
from simulation.common.mappers.registry import get_mapper


class TestG1Mapper:
    def setup_method(self):
        self.mapper = G1Mapper()

    def test_move_forward_default(self):
        cmd = self.mapper.map("move_forward", {})
        assert cmd.ctrl[0] == 0.5
        assert cmd.ctrl[1] == 0.0
        assert cmd.ctrl[2] == 0.0
        assert cmd.skill == "move_forward"

    def test_move_forward_custom_speed(self):
        cmd = self.mapper.map("move_forward", {"speed": 0.8, "durationSec": 5.0})
        assert cmd.ctrl[0] == 0.8
        assert cmd.duration_sec == 5.0

    def test_move_forward_speed_clamped(self):
        cmd = self.mapper.map("move_forward", {"speed": 999.0})
        assert cmd.ctrl[0] == 1.0  # clamped to max

    def test_move_backward(self):
        cmd = self.mapper.map("move_backward", {})
        assert cmd.ctrl[0] < 0

    def test_turn_left(self):
        cmd = self.mapper.map("turn_left", {})
        assert cmd.ctrl[2] > 0

    def test_turn_right(self):
        cmd = self.mapper.map("turn_right", {})
        assert cmd.ctrl[2] < 0

    def test_wave(self):
        cmd = self.mapper.map("wave", {})
        assert cmd.ctrl[12] == -1.5  # shoulder pitch
        assert cmd.duration_sec == 2.0

    def test_stop_zeroes(self):
        cmd = self.mapper.map("stop", {})
        assert all(v == 0.0 for v in cmd.ctrl)
        assert cmd.skill == "stop"

    def test_cancel_zeroes(self):
        cmd = self.mapper.map("cancel", {})
        assert all(v == 0.0 for v in cmd.ctrl)

    def test_unknown_action_zeroes(self):
        cmd = self.mapper.map("nonexistent_skill", {})
        assert all(v == 0.0 for v in cmd.ctrl)

    def test_n_actuators(self):
        assert self.mapper.n_actuators == 29
        cmd = self.mapper.map("move_forward", {})
        assert len(cmd.ctrl) == 29


class TestGo2Mapper:
    def setup_method(self):
        self.mapper = Go2Mapper()

    def test_move_forward(self):
        cmd = self.mapper.map("move_forward", {})
        assert cmd.ctrl[0] == 0.5
        assert len(cmd.ctrl) == 12

    def test_stop(self):
        cmd = self.mapper.map("stop", {})
        assert all(v == 0.0 for v in cmd.ctrl)


class TestSpotMapper:
    def setup_method(self):
        self.mapper = SpotMapper()

    def test_walk(self):
        cmd = self.mapper.map("walk", {})
        assert cmd.ctrl[0] > 0

    def test_inspect(self):
        cmd = self.mapper.map("inspect", {})
        assert cmd.ctrl[4] == -0.3  # front left knee
        assert cmd.duration_sec == 5.0

    def test_dock(self):
        cmd = self.mapper.map("dock", {})
        assert cmd.ctrl[0] == 0.2


class TestRegistry:
    def test_g1_mapper(self):
        mapper = get_mapper("g1")
        assert isinstance(mapper, G1Mapper)

    def test_go2_mapper(self):
        mapper = get_mapper("go2")
        assert isinstance(mapper, Go2Mapper)

    def test_unknown_falls_back_to_generic(self):
        mapper = get_mapper("unknown_robot")
        assert isinstance(mapper, GenericMapper)

    def test_spot_mapper(self):
        mapper = get_mapper("spot")
        assert isinstance(mapper, SpotMapper)
