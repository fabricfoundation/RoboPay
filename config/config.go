package config

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"

	"github.com/google/uuid"
)

const (
	DefaultProxyWSURL     = "ws://localhost:8080/api/core/ws/robot"
	DefaultFacilitatorURL = "https://x402.org/facilitator"
)

func getEnvOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

type Config struct {
	RobotID         string `json:"robot_id"`
	EVMPayeeAddress string `json:"evm_payee_address"`
	Price           string `json:"price"`
	Network         string `json:"network"`
	ProxyWSURL      string `json:"-"`
	FacilitatorURL  string `json:"-"`
}

var (
	priceRegex   = regexp.MustCompile(`^\$\d+(\.\d+)?$`)
	networkRegex = regexp.MustCompile(`^[a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$`)
)

func LoadConfig(path string) (*Config, error) {
	file, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	var cfg Config
	if err := json.Unmarshal(file, &cfg); err != nil {
		return nil, fmt.Errorf("failed to unmarshal config: %w", err)
	}

	cfg.ProxyWSURL = getEnvOrDefault("PROXY_WS_URL", DefaultProxyWSURL)
	cfg.FacilitatorURL = getEnvOrDefault("FACILITATOR_URL", DefaultFacilitatorURL)

	if cfg.RobotID == "" {
		cfg.RobotID = uuid.NewString()
	}

	if cfg.Price == "" {
		cfg.Price = "$0.001"
	}
	if !priceRegex.MatchString(cfg.Price) {
		return nil, fmt.Errorf("invalid price format: %q, expected format like $0.001", cfg.Price)
	}

	if cfg.Network == "" {
		cfg.Network = "eip155:8453" // Base mainnet CAIP-2 ID
	}
	if !networkRegex.MatchString(cfg.Network) {
		return nil, fmt.Errorf("invalid network format: %q, expected format like eip155:8453", cfg.Network)
	}

	if cfg.EVMPayeeAddress == "" {
		return nil, fmt.Errorf("evm_payee_address is required")
	}

	return &cfg, nil
}
