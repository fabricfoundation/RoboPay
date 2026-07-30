package config

import (
	"encoding/json"
	"fmt"
	"math/big"
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

	EIP155Prefix         = "eip155:"
	DefaultTokenVersion  = "1"
	DefaultTokenDecimals = 6

	TransferMethodEIP3009 = "eip3009"
	TransferMethodPermit2 = "permit2"
)

func getEnvOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

type Config struct {
	RobotID              string `json:"robot_id"`
	EVMPayeeAddress      string `json:"evm_payee_address"`
	Price                string `json:"price"`
	Network              string `json:"network"`
	TokenAddress         string `json:"token_address"`
	TokenName            string `json:"token_name"`
	TokenVersion         string `json:"token_version"`
	TokenDecimals        int    `json:"token_decimals"`
	TokenTransferMethod  string `json:"token_transfer_method"`
	TokenSupportsEIP2612 bool   `json:"token_supports_eip2612"`
	ProxyWSURL           string `json:"-"`
	FacilitatorURL       string `json:"-"`

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
	priceRegex   = regexp.MustCompile(`^\$?\d+(\.\d+)?$`)
	networkRegex = regexp.MustCompile(`^[a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$`)
	addressRegex = regexp.MustCompile(`^0x[0-9a-fA-F]{40}$`)
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

// ChainID returns the EIP-155 chain ID of the configured network.
// The second return value is false when the network is not an eip155 CAIP-2 ID.
func (c *Config) ChainID() (*big.Int, bool) {
	if !strings.HasPrefix(c.Network, EIP155Prefix) {
		return nil, false
	}
	return new(big.Int).SetString(strings.TrimPrefix(c.Network, EIP155Prefix), 10)
}

// Validate checks the user-supplied fields and fills in defaults. It is safe to call on a
// candidate copy of a Config to vet a hot-reload update before committing it.
func (c *Config) Validate() error {
	if c.RobotID == "" {
		c.RobotID = uuid.NewString()
	}

	if c.Price == "" {
		c.Price = "0.001"
	}
	if !priceRegex.MatchString(c.Price) {
		return fmt.Errorf("invalid price format: %q, expected a decimal amount like 0.001 or $0.001", c.Price)
	}

	if c.Network == "" {
		c.Network = "eip155:8453"
	}
	if !networkRegex.MatchString(c.Network) {
		return fmt.Errorf("invalid network format: %q, expected format like eip155:8453", c.Network)
	}

	if c.EVMPayeeAddress == "" {
		return fmt.Errorf("evm_payee_address is required")
	}

	return c.validateToken()
}

// validateToken checks the optional token fields. They are only meaningful together: an empty
// token_address means "use whatever default asset x402 knows for this network".
func (c *Config) validateToken() error {
	if c.TokenAddress == "" {
		return nil
	}

	if !addressRegex.MatchString(c.TokenAddress) {
		return fmt.Errorf("invalid token_address format: %q, expected a 0x-prefixed 20-byte hex address", c.TokenAddress)
	}
	if _, ok := c.ChainID(); !ok {
		return fmt.Errorf("token_address requires an eip155 network, got %q", c.Network)
	}
	if c.TokenDecimals < 0 || c.TokenDecimals > 36 {
		return fmt.Errorf("invalid token_decimals: %d, expected 0-36", c.TokenDecimals)
	}

	switch c.TokenTransferMethod {
	case "":
		c.TokenTransferMethod = TransferMethodEIP3009
	case TransferMethodEIP3009, TransferMethodPermit2:
	default:
		return fmt.Errorf("invalid token_transfer_method: %q, expected %q or %q",
			c.TokenTransferMethod, TransferMethodEIP3009, TransferMethodPermit2)
	}

	if c.TokenSupportsEIP2612 && c.TokenTransferMethod != TransferMethodPermit2 {
		return fmt.Errorf("token_supports_eip2612 only applies when token_transfer_method is %q", TransferMethodPermit2)
	}

	if c.NeedsEIP712Domain() && c.TokenName == "" {
		return fmt.Errorf("token_name is required for %s transfers (it forms the EIP-712 domain the payer signs)",
			c.TokenTransferMethod)
	}

	if c.TokenVersion == "" {
		c.TokenVersion = DefaultTokenVersion
	}
	if c.TokenDecimals == 0 {
		c.TokenDecimals = DefaultTokenDecimals
	}

	return nil
}

// NeedsEIP712Domain reports whether the payer will sign against the token's own EIP-712 domain,
// which is what makes token_name and token_version load-bearing.
func (c *Config) NeedsEIP712Domain() bool {
	return c.TokenTransferMethod != TransferMethodPermit2 || c.TokenSupportsEIP2612
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

	// CHAIN overrides the configured network, so it has to be applied before validation.
	defaultChainID := DefaultAIPChainID
	if chain := os.Getenv("CHAIN"); chain != "" {
		preset, ok := chainPresets[strings.ToLower(chain)]
		if !ok {
			return nil, fmt.Errorf("invalid CHAIN %q: valid values are bsc-testnet, bsc-mainnet, base-sepolia, base-mainnet", chain)
		}
		cfg.Network = preset.Network
		defaultChainID = preset.ChainID
	}

	if err := cfg.Validate(); err != nil {
		return nil, err
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
