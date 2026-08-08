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
	DefaultAIPEndpoint      = "https://api.aip.unibase.com"
	DefaultAIPGatewayURL    = "https://gateway.aip.unibase.com"
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
	LocalHTTPAddr   string `json:"-"` // optional direct listener for local integration/evidence runs

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

// PriceAmount returns the numeric value of the configured price ("$0.002" → 0.002).
func (c *Config) PriceAmount() float64 {
	v, err := strconv.ParseFloat(strings.TrimPrefix(c.Price, "$"), 64)
	if err != nil {
		return 0
	}
	return v
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

// chainPresets are the networks selectable via the CHAIN env var. A preset
// drives both the x402 payment network (CAIP-2) and the AIP registration
// chain ID.
var chainPresets = map[string]struct {
	Network string
	ChainID int
}{
	"bsc-testnet":  {"eip155:97", 97},
	"bsc-mainnet":  {"eip155:56", 56},
	"base-sepolia": {"eip155:84532", 84532},
	"base-mainnet": {"eip155:8453", 8453},
}

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
	cfg.LocalHTTPAddr = os.Getenv("LOCAL_HTTP_ADDR")

	if cfg.RobotID == "" {
		cfg.RobotID = uuid.NewString()
	}

	if cfg.Price == "" {
		cfg.Price = "$0.001"
	}
	if !priceRegex.MatchString(cfg.Price) {
		return nil, fmt.Errorf("invalid price format: %q, expected format like $0.001", cfg.Price)
	}

	defaultChainID := DefaultAIPChainID
	if chain := os.Getenv("CHAIN"); chain != "" {
		preset, ok := chainPresets[strings.ToLower(chain)]
		if !ok {
			return nil, fmt.Errorf("invalid CHAIN %q: valid values are bsc-testnet, bsc-mainnet, base-sepolia, base-mainnet", chain)
		}
		cfg.Network = preset.Network
		defaultChainID = preset.ChainID
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

	if err := loadAIPConfig(&cfg, defaultChainID); err != nil {
		return nil, err
	}

	return &cfg, nil
}

func loadAIPConfig(cfg *Config, defaultChainID int) error {
	cfg.AIPEnabled = getBoolEnv("AIP_ENABLED", false)

	cfg.AIPUserID = os.Getenv("AIP_USER_ID")
	cfg.AIPPrivyToken = getEnvOrDefault("UNIBASE_PROXY_AUTH", os.Getenv("PRIVY_TOKEN"))
	cfg.AIPEndpoint = getEnvOrDefault("AIP_ENDPOINT", DefaultAIPEndpoint)
	cfg.AIPGatewayURL = getEnvOrDefault("GATEWAY_URL", DefaultAIPGatewayURL)
	cfg.AIPPublicBaseURL = getEnvOrDefault("AIP_PUBLIC_BASE_URL", DefaultAIPPublicBaseURL)
	cfg.AIPAgentName = getEnvOrDefault("AIP_AGENT_NAME", "Robot "+cfg.RobotID)

	cfg.AIPChainID = defaultChainID

	cfg.AIPLocalPort = DefaultAIPLocalPort
	if v := os.Getenv("AIP_LOCAL_PORT"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid AIP_LOCAL_PORT: %q", v)
		}
		cfg.AIPLocalPort = n
	}

	// No credential check here: when AIP is enabled and no token is set, the
	// tunnel runs the SDK's interactive authorization flow at startup.
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
