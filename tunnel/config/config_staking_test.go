package config

import "testing"

func TestValidate_RequiresStakingAddress(t *testing.T) {
	cfg := baseConfig()
	cfg.StakingAddress = ""

	// A payee address alone must not satisfy the tunnel gate — there is no fallback.
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error when staking_address is absent")
	}
}

func TestValidate_RejectsMalformedStakingAddress(t *testing.T) {
	cfg := baseConfig()
	cfg.StakingAddress = "not-an-address"

	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for a malformed staking_address")
	}
}

func TestValidate_RejectsMalformedPayeeAddress(t *testing.T) {
	cfg := baseConfig()
	cfg.EVMPayeeAddress = "0x123"

	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for a malformed evm_payee_address")
	}
}

func TestValidate_KeepsStakingAddressIndependentOfPayee(t *testing.T) {
	const payee = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
	const staking = stakingAddr

	cfg := baseConfig()
	cfg.EVMPayeeAddress = payee
	cfg.StakingAddress = staking

	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	if cfg.StakingAddress != staking {
		t.Fatalf("StakingAddress = %q, want %q", cfg.StakingAddress, staking)
	}
	if cfg.EVMPayeeAddress != payee {
		t.Fatalf("EVMPayeeAddress = %q, want %q", cfg.EVMPayeeAddress, payee)
	}
}
