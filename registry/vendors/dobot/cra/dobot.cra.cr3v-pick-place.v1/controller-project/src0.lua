-- Fabric Foundation x DOBOT
-- Custom T3 two-cycle pick-and-place behavior.
-- P1..P6 must be taught and safety-reviewed for each workcell.

SpeedFactor(20)
VelJ(20)
AccJ(20)

-- Cycle 1: home -> pick 1 -> lift -> transfer -> place 1 -> home.
MovJ(P1)
MovJ(P2)
DO(9,1)
Wait(500)
RelMovLUser({0,0,20,0,0,0})
MovJ(P3)
MovJ(P4)
DO(9,0)
Wait(1500)
MovJ(P1)

-- Cycle 2: home -> pick 2 -> lift -> transfer -> place 2 -> home.
MovJ(P1)
MovJ(P5)
DO(9,1)
Wait(500)
RelMovLUser({0,0,20,0,0,0})
MovJ(P3)
MovJ(P6)
DO(9,0)
Wait(1500)
MovJ(P1)
