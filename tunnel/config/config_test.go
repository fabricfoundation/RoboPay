package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testPayee = "0x" + "11" + strings.Repeat("1", 38)

func validConfig() Config {
	return Config{
		RobotID:         "test-robot",
		EVMPayeeAddress: testPayee,
		Price:           "$0.002",
		Network:         "eip155:84532",
	}
}

func TestValidate(t *testing.T) {
	tests := []struct {
		name    string
		mutate  func(*Config)
		wantErr bool
	}{
		{"valid config", func(c *Config) {}, false},
		{"empty price defaults to 0.001", func(c *Config) { c.Price = "" }, false},
		{"invalid price rejected", func(c *Config) { c.Price = "abc" }, true},
		{"empty network defaults to base mainnet", func(c *Config) { c.Network = "" }, false},
		{"network without caip scheme rejected", func(c *Config) { c.Network = "not-caip" }, true},
		{"missing payee rejected", func(c *Config) { c.EVMPayeeAddress = "" }, true},
		{"malformed token address rejected", func(c *Config) {
			c.TokenAddress = "nope"
		}, true},
		{"token decimals out of range rejected", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenDecimals = 99
		}, true},
		{"unknown transfer method rejected", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = "magic"
		}, true},
		{"eip2612 without permit2 rejected", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = TransferMethodEIP3009
			c.TokenSupportsEIP2612 = true
		}, true},
		{"eip3009 requires token name", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = TransferMethodEIP3009
		}, true},
		{"permit2 without eip2612 and no name is valid", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = TransferMethodPermit2
		}, false},
		{"permit2 with eip2612 requires token name", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = TransferMethodPermit2
			c.TokenSupportsEIP2612 = true
		}, true},
		{"permit2 with eip2612 and token name is valid", func(c *Config) {
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
			c.TokenTransferMethod = TransferMethodPermit2
			c.TokenSupportsEIP2612 = true
			c.TokenName = "USDC"
		}, false},
		{"token_address requires eip155 network", func(c *Config) {
			c.Network = "solana:mainnet"
			c.TokenAddress = "0x" + strings.Repeat("1", 40)
		}, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := validConfig()
			tt.mutate(&cfg)
			err := cfg.Validate()
			if (err != nil) != tt.wantErr {
				t.Fatalf("Validate() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestChainID(t *testing.T) {
	cfg := Config{Network: "eip155:84532"}
	id, ok := cfg.ChainID()
	if !ok {
		t.Fatal("expected eip155 network to parse")
	}
	if id.String() != "84532" {
		t.Fatalf("ChainID() = %s, want 84532", id.String())
	}

	cfg = Config{Network: "solana:mainnet"}
	if _, ok := cfg.ChainID(); ok {
		t.Fatal("expected non-eip155 network to fail")
	}
}

func TestPriceAmount(t *testing.T) {
	tests := []struct {
		price string
		want  float64
	}{
		{"$0.002", 0.002},
		{"0.001", 0.001},
		{"1", 1},
		{"not-a-number", 0},
		{"", 0},
	}
	for _, tt := range tests {
		cfg := Config{Price: tt.price}
		if got := cfg.PriceAmount(); got != tt.want {
			t.Errorf("PriceAmount(%q) = %v, want %v", tt.price, got, tt.want)
		}
	}
}

func writeConfigFile(t *testing.T, raw string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}
	return path
}

func TestLoadConfigChainPresets(t *testing.T) {
	tests := []struct {
		chain       string
		wantNetwork string
	}{
		{"bsc-testnet", "eip155:97"},
		{"bsc-mainnet", "eip155:56"},
		{"base-sepolia", "eip155:84532"},
		{"base-mainnet", "eip155:8453"},
	}

	for _, tt := range tests {
		t.Run(tt.chain, func(t *testing.T) {
			path := writeConfigFile(t, `{"evm_payee_address":"`+testPayee+`","price":"0.001","network":"eip155:1"}`)
			t.Setenv("CHAIN", tt.chain)
			cfg, err := LoadConfig(path)
			if err != nil {
				t.Fatalf("LoadConfig() error = %v", err)
			}
			if cfg.Network != tt.wantNetwork {
				t.Fatalf("Network = %s, want %s", cfg.Network, tt.wantNetwork)
			}
		})
	}
}

func TestLoadConfigInvalidChainRejected(t *testing.T) {
	path := writeConfigFile(t, `{"evm_payee_address":"`+testPayee+`","price":"0.001"}`)
	t.Setenv("CHAIN", "bogus")
	_, err := LoadConfig(path)
	if err == nil || !strings.Contains(err.Error(), "invalid CHAIN") {
		t.Fatalf("LoadConfig() error = %v, want invalid CHAIN", err)
	}
}

func TestLoadConfigMalformedAIPEnabledRejected(t *testing.T) {
	path := writeConfigFile(t, `{"evm_payee_address":"`+testPayee+`","price":"0.001"}`)
	t.Setenv("AIP_ENABLED", "tru")
	_, err := LoadConfig(path)
	if err == nil || !strings.Contains(err.Error(), "invalid boolean value for AIP_ENABLED") {
		t.Fatalf("LoadConfig() error = %v, want invalid boolean value", err)
	}
}

func TestGetBoolEnv(t *testing.T) {
	t.Setenv("ROBO_TEST_BOOL", "true")
	got, err := getBoolEnv("ROBO_TEST_BOOL", false)
	if err != nil || !got {
		t.Fatalf("getBoolEnv() = %v, %v; want true, nil", got, err)
	}

	t.Setenv("ROBO_TEST_BOOL", "tru")
	if _, err := getBoolEnv("ROBO_TEST_BOOL", false); err == nil {
		t.Fatal("expected error for malformed boolean value")
	}

	t.Setenv("ROBO_TEST_BOOL", "")
	got, err = getBoolEnv("ROBO_TEST_BOOL", true)
	if err != nil || !got {
		t.Fatalf("getBoolEnv() = %v, %v; want true, nil (default)", got, err)
	}
}
