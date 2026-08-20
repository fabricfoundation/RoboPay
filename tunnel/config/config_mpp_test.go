package config

import (
	"os"
	"testing"
)

const validSecret = "test-secret-key-that-is-long-enough-for-hmac"

func baseConfig() *Config {
	return &Config{
		RobotID:         "test-robot",
		EVMPayeeAddress: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
		Price:           "$0.002",
		Network:         "eip155:84532",
	}
}

// A robot that only speaks x402 must not have to configure anything for MPP.
func TestValidateMPP_DisabledIgnoresEverything(t *testing.T) {
	cfg := baseConfig()
	cfg.MPPNetwork = "not-a-network"
	cfg.MPPPayeeAddress = "nonsense"

	if err := cfg.Validate(); err != nil {
		t.Fatalf("expected disabled MPP to skip validation, got %v", err)
	}
}

func TestValidateMPP_Defaults(t *testing.T) {
	cfg := baseConfig()
	cfg.MPPEnabled = true
	cfg.MPPSecretKey = validSecret

	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}

	if cfg.MPPNetwork != DefaultMPPNetwork {
		t.Errorf("expected default network %q, got %q", DefaultMPPNetwork, cfg.MPPNetwork)
	}
	// Tempo addresses are EVM-shaped, so one payee serves both protocols.
	if cfg.MPPPayeeAddress != cfg.EVMPayeeAddress {
		t.Errorf("expected the MPP payee to default to the x402 payee, got %q", cfg.MPPPayeeAddress)
	}
	if cfg.MPPDecimals != DefaultMPPDecimals {
		t.Errorf("expected default decimals %d, got %d", DefaultMPPDecimals, cfg.MPPDecimals)
	}
	if cfg.MPPRealm != cfg.RobotID {
		t.Errorf("expected the realm to default to the robot id, got %q", cfg.MPPRealm)
	}
	// An empty currency lets mpp-go pick the chain's default stablecoin.
	if cfg.MPPCurrency != "" {
		t.Errorf("expected no default currency, got %q", cfg.MPPCurrency)
	}
}

func TestMPPChainID(t *testing.T) {
	tests := []struct {
		network string
		want    int64
		ok      bool
	}{
		{DefaultMPPNetwork, 4217, true},
		{MPPNetworkModerato, 42431, true},
		{"solana:mainnet", 0, false},
		{"eip155:abc", 0, false},
		{"", 0, false},
	}

	for _, tc := range tests {
		cfg := &Config{MPPNetwork: tc.network}
		got, ok := cfg.MPPChainID()
		if ok != tc.ok || got != tc.want {
			t.Errorf("MPPChainID(%q) = (%d, %v), want (%d, %v)", tc.network, got, ok, tc.want, tc.ok)
		}
	}
}

func TestValidateMPP_Rejections(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*Config)
	}{
		{"missing secret key", func(c *Config) { c.MPPSecretKey = "" }},
		{"short secret key", func(c *Config) { c.MPPSecretKey = "too-short" }},
		{"malformed network", func(c *Config) { c.MPPNetwork = "tempo-mainnet" }},
		{"non-eip155 network", func(c *Config) { c.MPPNetwork = "solana:mainnet" }},
		{"malformed payee", func(c *Config) { c.MPPPayeeAddress = "0xnope" }},
		{"malformed currency", func(c *Config) { c.MPPCurrency = "USDC" }},
		{"out-of-range decimals", func(c *Config) { c.MPPDecimals = 64 }},
		// Only the built-in Tempo chains come with an RPC endpoint.
		{"unknown chain without an RPC url", func(c *Config) { c.MPPNetwork = "eip155:8453" }},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cfg := baseConfig()
			cfg.MPPEnabled = true
			cfg.MPPSecretKey = validSecret
			tc.mutate(cfg)

			if err := cfg.Validate(); err == nil {
				t.Fatal("expected an error, got nil")
			}
		})
	}
}

// A non-Tempo chain is allowed as long as it names an endpoint to verify against.
func TestValidateMPP_UnknownChainWithRPCURL(t *testing.T) {
	cfg := baseConfig()
	cfg.MPPEnabled = true
	cfg.MPPSecretKey = validSecret
	cfg.MPPNetwork = "eip155:8453"
	cfg.MPPRPCURL = "https://rpc.example.com"

	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
}

func TestValidateMPP_ModeratoTestnet(t *testing.T) {
	cfg := baseConfig()
	cfg.MPPEnabled = true
	cfg.MPPSecretKey = validSecret
	cfg.MPPNetwork = MPPNetworkModerato

	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	if chainID, ok := cfg.MPPChainID(); !ok || chainID != 42431 {
		t.Fatalf("expected Moderato chain id 42431, got (%d, %v)", chainID, ok)
	}
}

// The MPP fields are drivable from the environment so a deployment can carry
// its whole payment setup in .env, the way CHAIN already does for x402.
func TestLoadConfig_MPPEnvOverrides(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/config.json"
	if err := os.WriteFile(path, []byte(`{
		"robot_id": "env-robot",
		"evm_payee_address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
		"price": "$0.002",
		"network": "eip155:84532"
	}`), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("MPP_ENABLED", "true")
	t.Setenv("MPP_SECRET_KEY", validSecret)
	t.Setenv("MPP_NETWORK", MPPNetworkModerato)
	t.Setenv("MPP_PAYEE_ADDRESS", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
	t.Setenv("MPP_CURRENCY", "0x20c0000000000000000000000000000000000000")
	t.Setenv("MPP_DECIMALS", "18")
	t.Setenv("MPP_REALM", "robots.example.com")

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if !cfg.MPPEnabled {
		t.Error("expected MPP_ENABLED to turn MPP on")
	}
	if cfg.MPPNetwork != MPPNetworkModerato {
		t.Errorf("expected network %q, got %q", MPPNetworkModerato, cfg.MPPNetwork)
	}
	// An explicit payee must win over the evm_payee_address fallback.
	if cfg.MPPPayeeAddress != "0x70997970C51812dc3A010C7d01b50e0d17dc79C8" {
		t.Errorf("unexpected payee %q", cfg.MPPPayeeAddress)
	}
	if cfg.MPPCurrency != "0x20c0000000000000000000000000000000000000" {
		t.Errorf("unexpected currency %q", cfg.MPPCurrency)
	}
	if cfg.MPPDecimals != 18 {
		t.Errorf("expected decimals 18, got %d", cfg.MPPDecimals)
	}
	if cfg.MPPRealm != "robots.example.com" {
		t.Errorf("unexpected realm %q", cfg.MPPRealm)
	}
}

func TestLoadConfig_InvalidMPPDecimals(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/config.json"
	if err := os.WriteFile(path, []byte(`{"evm_payee_address":"0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"}`), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("MPP_DECIMALS", "six")

	if _, err := LoadConfig(path); err == nil {
		t.Fatal("expected an error for a non-numeric MPP_DECIMALS")
	}
}
