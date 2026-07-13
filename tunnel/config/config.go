package config

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/google/uuid"
)

const (
	DefaultProxyWSURL       = "ws://localhost:8080/api/core/ws/robot"
	DefaultFacilitatorURL   = "https://x402.org/facilitator"
	DefaultAIPPublicBaseURL = "https://api.fabric.foundation/api/core"
	DefaultAIPChainID       = 97
	DefaultAIPLocalPort     = 8000
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

	// aIP
	AIPEnabled       bool   `json:"-"`
	AIPUserID        string `json:"-"` // wallet address used for registration
	AIPPrivyToken    string `json:"-"` // bearer token for registration
	AIPEndpoint      string `json:"-"` // AIP platform URL
	AIPGatewayURL    string `json:"-"` // AIP gateway URL
	AIPPublicBaseURL string `json:"-"` // public gateway base, e.g. https://api.fabric.foundation/api/core/v1
	AIPAgentName     string `json:"-"`
	AIPChainID       int    `json:"-"`
	AIPLocalPort     int    `json:"-"` // localhost port the SDK binds for its (tunnel-bypassed) listener
}

// AIPEndpointURL is the public URL AIP advertises and calls for this robot:
// the gateway's transparent proxy path. AIP traffic flows
// AIP -> gateway(/robots/<id>/...) -> ws -> tunnel -> AIP handler.
func (c *Config) AIPEndpointURL() string {
	base := strings.TrimRight(c.AIPPublicBaseURL, "/")
	return fmt.Sprintf("%s/robots/%s", base, c.RobotID)
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

	if err := loadAIPConfig(&cfg); err != nil {
		return nil, err
	}

	return &cfg, nil
}

func loadAIPConfig(cfg *Config) error {
	cfg.AIPEnabled = getBoolEnv("AIP_ENABLED", false)

	cfg.AIPUserID = os.Getenv("AIP_USER_ID")
	cfg.AIPPrivyToken = getEnvOrDefault("UNIBASE_PROXY_AUTH", os.Getenv("PRIVY_TOKEN"))
	cfg.AIPEndpoint = os.Getenv("AIP_ENDPOINT")
	cfg.AIPGatewayURL = os.Getenv("GATEWAY_URL")
	cfg.AIPPublicBaseURL = getEnvOrDefault("AIP_PUBLIC_BASE_URL", DefaultAIPPublicBaseURL)
	cfg.AIPAgentName = getEnvOrDefault("AIP_AGENT_NAME", "Robot "+cfg.RobotID)

	cfg.AIPChainID = DefaultAIPChainID
	if v := os.Getenv("AIP_CHAIN_ID"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid AIP_CHAIN_ID: %q", v)
		}
		cfg.AIPChainID = n
	}

	cfg.AIPLocalPort = DefaultAIPLocalPort
	if v := os.Getenv("AIP_LOCAL_PORT"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid AIP_LOCAL_PORT: %q", v)
		}
		cfg.AIPLocalPort = n
	}

	if !cfg.AIPEnabled {
		return nil
	}
	if cfg.AIPUserID == "" {
		return fmt.Errorf("AIP_USER_ID is required when AIP_ENABLED is true")
	}
	return nil
}

func getBoolEnv(key string, defaultVal bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return defaultVal
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return defaultVal
	}
	return b
}
