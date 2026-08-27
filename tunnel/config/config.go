package config

import (
	"crypto/ecdsa"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/google/uuid"
	tempotx "github.com/tempoxyz/tempo-go/pkg/transaction"
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

	DefaultMPPNetwork    = "eip155:4217"
	MPPNetworkModerato   = "eip155:42431"
	DefaultMPPDecimals   = 6
	MPPMinSecretKeyBytes = 32
)

func getEnvOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

type Config struct {
	RobotID string `json:"robot_id"`
	EVMPayeeAddress string `json:"evm_payee_address"`
	StakingAddress string `json:"staking_address"`
	StakingPrivateKey    string `json:"-"`
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

	MPPEnabled      bool   `json:"mpp_enabled"`
	MPPNetwork      string `json:"mpp_network"`
	MPPPayeeAddress string `json:"mpp_payee_address"`
	MPPCurrency     string `json:"mpp_currency"`
	MPPDecimals     int    `json:"mpp_decimals"`
	MPPRealm        string `json:"mpp_realm"`
	MPPRPCURL       string `json:"-"`
	MPPSecretKey    string `json:"-"`

	// OperatorSigningKey attests the payment requirements this robot advertises,
	// so a payer can confirm the recipient it signs for came from the operator
	// and not from anything relaying the 402. Environment only — it is
	// deliberately not settable over the robot config topic, which is
	// unauthenticated.
	OperatorSigningKey string `json:"-"`

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
	if !addressRegex.MatchString(c.EVMPayeeAddress) {
		return fmt.Errorf("invalid evm_payee_address format: %q, expected a 0x-prefixed 20-byte hex address", c.EVMPayeeAddress)
	}

	if c.StakingAddress == "" {
		return fmt.Errorf("staking_address is required")
	}
	if !addressRegex.MatchString(c.StakingAddress) {
		return fmt.Errorf("invalid staking_address format: %q, expected a 0x-prefixed 20-byte hex address", c.StakingAddress)
	}
	if err := c.validateStakingKey(); err != nil {
		return err
	}

	if err := c.validateToken(); err != nil {
		return err
	}

	return c.validateMPP()
}

// StakingSigner parses StakingPrivateKey into a usable key.
func (c *Config) StakingSigner() (*ecdsa.PrivateKey, error) {
	hexKey := strings.TrimPrefix(strings.TrimSpace(c.StakingPrivateKey), "0x")
	if hexKey == "" {
		return nil, fmt.Errorf("STAKING_PRIVATE_KEY is not set")
	}
	key, err := crypto.HexToECDSA(hexKey)
	if err != nil {
		return nil, fmt.Errorf("failed to parse STAKING_PRIVATE_KEY: %w", err)
	}
	return key, nil
}

// validateStakingKey requires a key whose address is the declared staking_address.
// Checking the pair here turns a silent authorization failure into a startup error.
func (c *Config) validateStakingKey() error {
	key, err := c.StakingSigner()
	if err != nil {
		return fmt.Errorf("%w (it signs the proxy handshake that proves you control staking_address %s)",
			err, c.StakingAddress)
	}

	derived := crypto.PubkeyToAddress(key.PublicKey)
	if derived != common.HexToAddress(c.StakingAddress) {
		return fmt.Errorf("STAKING_PRIVATE_KEY belongs to %s but staking_address is %s: the proxy gates on the address recovered from the signature, so the declared one must match",
			derived.Hex(), common.HexToAddress(c.StakingAddress).Hex())
	}
	return nil
}

// MPPChainID returns the EIP-155 chain ID of the configured MPP network.
func (c *Config) MPPChainID() (int64, bool) {
	if !strings.HasPrefix(c.MPPNetwork, EIP155Prefix) {
		return 0, false
	}
	id, err := strconv.ParseInt(strings.TrimPrefix(c.MPPNetwork, EIP155Prefix), 10, 64)
	if err != nil {
		return 0, false
	}
	return id, true
}

// validateMPP checks the MPP fields and fills in defaults.
func (c *Config) validateMPP() error {
	if !c.MPPEnabled {
		return nil
	}

	if c.MPPNetwork == "" {
		c.MPPNetwork = DefaultMPPNetwork
	}
	if !networkRegex.MatchString(c.MPPNetwork) {
		return fmt.Errorf("invalid mpp_network format: %q, expected format like %s", c.MPPNetwork, DefaultMPPNetwork)
	}
	chainID, ok := c.MPPChainID()
	if !ok {
		return fmt.Errorf("mpp_network must be an eip155 CAIP-2 id, got %q", c.MPPNetwork)
	}

	if c.MPPRPCURL == "" && !isKnownTempoChain(chainID) {
		return fmt.Errorf("MPP_RPC_URL is required for mpp_network %q: only Tempo mainnet (%s) and Moderato (%s) have a built-in endpoint",
			c.MPPNetwork, DefaultMPPNetwork, MPPNetworkModerato)
	}

	if c.MPPPayeeAddress == "" {
		c.MPPPayeeAddress = c.EVMPayeeAddress
	}
	if !addressRegex.MatchString(c.MPPPayeeAddress) {
		return fmt.Errorf("invalid mpp_payee_address format: %q, expected a 0x-prefixed 20-byte hex address", c.MPPPayeeAddress)
	}

	if c.MPPCurrency != "" && !addressRegex.MatchString(c.MPPCurrency) {
		return fmt.Errorf("invalid mpp_currency format: %q, expected a 0x-prefixed 20-byte hex address", c.MPPCurrency)
	}

	if c.MPPDecimals == 0 {
		c.MPPDecimals = DefaultMPPDecimals
	}
	if c.MPPDecimals < 0 || c.MPPDecimals > 36 {
		return fmt.Errorf("invalid mpp_decimals: %d, expected 0-36", c.MPPDecimals)
	}

	if c.MPPRealm == "" {
		c.MPPRealm = c.RobotID
	}

	if len(c.MPPSecretKey) < MPPMinSecretKeyBytes {
		return fmt.Errorf("MPP_SECRET_KEY must be at least %d bytes when mpp_enabled is true (generate one with: openssl rand -base64 32)",
			MPPMinSecretKeyBytes)
	}

	return nil
}

// isKnownTempoChain reports whether mpp-go ships an RPC endpoint for the chain.
func isKnownTempoChain(chainID int64) bool {
	return chainID == tempotx.ChainIdMainnet || chainID == tempotx.ChainIdModerato
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

	cfg.MPPSecretKey = os.Getenv("MPP_SECRET_KEY")
	cfg.MPPRPCURL = os.Getenv("MPP_RPC_URL")
	cfg.OperatorSigningKey = os.Getenv("OPERATOR_SIGNING_KEY")
	cfg.StakingPrivateKey = os.Getenv("STAKING_PRIVATE_KEY")

	if os.Getenv("MPP_ENABLED") != "" {
		cfg.MPPEnabled = getBoolEnv("MPP_ENABLED", cfg.MPPEnabled)
	}
	cfg.MPPNetwork = getEnvOrDefault("MPP_NETWORK", cfg.MPPNetwork)
	cfg.MPPPayeeAddress = getEnvOrDefault("MPP_PAYEE_ADDRESS", cfg.MPPPayeeAddress)
	cfg.MPPCurrency = getEnvOrDefault("MPP_CURRENCY", cfg.MPPCurrency)
	cfg.MPPRealm = getEnvOrDefault("MPP_REALM", cfg.MPPRealm)
	if v := os.Getenv("MPP_DECIMALS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return nil, fmt.Errorf("invalid MPP_DECIMALS: %q", v)
		}
		cfg.MPPDecimals = n
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
