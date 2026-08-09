package config

import "testing"

func TestValidateRequiresStableRobotIdentityAndPayee(t *testing.T) {
	missingRobot := Config{
		EVMPayeeAddress: "0x1111111111111111111111111111111111111111",
		Price:           "0.001",
		Network:         "eip155:84532",
	}
	if err := missingRobot.Validate(); err == nil {
		t.Fatal("expected missing robot_id to fail closed")
	}

	zeroPayee := Config{
		RobotID:         "robot-a",
		EVMPayeeAddress: zeroEVMAddress,
		Price:           "0.001",
		Network:         "eip155:84532",
	}
	if err := zeroPayee.Validate(); err == nil {
		t.Fatal("expected zero payee address to fail closed")
	}

	valid := Config{
		RobotID:         "robot-a",
		EVMPayeeAddress: "0x1111111111111111111111111111111111111111",
		Price:           "0.001",
		Network:         "eip155:84532",
	}
	if err := valid.Validate(); err != nil {
		t.Fatalf("expected explicit deployment identity to validate: %v", err)
	}
}
