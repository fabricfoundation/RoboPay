from types import SimpleNamespace
from x2.mapper import X2Mapper

def test_safe_unknown_action_is_zero():
    msg = X2Mapper().map(SimpleNamespace(action="unknown", params={}))
    assert msg.linear.x == 0 and msg.angular.z == 0

def test_forward_is_clamped():
    msg = X2Mapper().map(SimpleNamespace(action="move_forward", params={"speed": 9}))
    assert msg.linear.x == 0.5
