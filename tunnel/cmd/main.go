package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
	aipauth "github.com/unibaseio/aip-go-sdk/auth"
	aipserver "github.com/unibaseio/aip-go-sdk/server"
	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	ginmw "github.com/x402-foundation/x402/go/http/gin"
	evm "github.com/x402-foundation/x402/go/mechanisms/evm"
	evmexact "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/config"
	"github.com/fabricfoundation/tunnel/internal"
	"github.com/fabricfoundation/tunnel/internal/aipagent"
	"github.com/fabricfoundation/tunnel/internal/handlers"
)

const (
	RobotConfigTopicPrefix = "robot/config/"
)

func main() {
	configPath := flag.String("config", "config.json", "Path to config file")
	flag.Parse()

	logger, _ := zap.NewProduction()
	defer func() {
		if err := logger.Sync(); err != nil {
			logger.Warn("failed to sync logger", zap.Error(err))
		}
	}()

	if err := godotenv.Load(); err != nil {
		logger.Warn("failed to load .env file", zap.Error(err))
	}

	cfg, err := config.LoadConfig(*configPath)
	if err != nil {
		logger.Fatal("configuration error", zap.Error(err))
	}

	// No token in the env? Fall back to the SDK's cached credentials, or walk
	// the user through the browser authorization flow on first run.
	if cfg.AIPEnabled && cfg.AIPPrivyToken == "" {
		token, wallet, err := aipauth.EnsureAuth(context.Background())
		if err != nil {
			logger.Fatal("unibase authorization failed", zap.Error(err))
		}
		cfg.AIPPrivyToken = token
		if cfg.AIPUserID == "" {
			cfg.AIPUserID = wallet
		}
		logger.Info("unibase authorization ready", zap.String("wallet", wallet))
	}

	session, err := handlers.OpenZenohSession()
	if err != nil {
		logger.Fatal("failed to open zenoh session", zap.Error(err))
	}
	defer func() {
		if err := session.Close(nil); err != nil {
			logger.Warn("failed to close zenoh session", zap.Error(err))
		}
	}()

	restartCh := make(chan struct{}, 1)
	subTopic := RobotConfigTopicPrefix + cfg.RobotID
	ke, err := zenoh.NewKeyExpr(subTopic)
	if err != nil {
		logger.Fatal("failed to create key expression", zap.Error(err))
	}
	sub, err := session.DeclareSubscriber(ke, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var partialCfg struct {
				EVMPayeeAddress      *string `json:"evm_payee_address"`
				Price                *string `json:"price"`
				Network              *string `json:"network"`
				TokenAddress         *string `json:"token_address"`
				TokenName            *string `json:"token_name"`
				TokenVersion         *string `json:"token_version"`
				TokenDecimals        *int    `json:"token_decimals"`
				TokenTransferMethod  *string `json:"token_transfer_method"`
				TokenSupportsEIP2612 *bool   `json:"token_supports_eip2612"`
			}
			if err := json.Unmarshal(sample.Payload().Bytes(), &partialCfg); err != nil {
				logger.Warn("failed to parse config update", zap.Error(err))
				return
			}

			candidate := *cfg
			updated := false
			if partialCfg.EVMPayeeAddress != nil && *partialCfg.EVMPayeeAddress != candidate.EVMPayeeAddress {
				candidate.EVMPayeeAddress = *partialCfg.EVMPayeeAddress
				updated = true
			}
			if partialCfg.Price != nil && *partialCfg.Price != candidate.Price {
				candidate.Price = *partialCfg.Price
				updated = true
			}
			if partialCfg.Network != nil && *partialCfg.Network != candidate.Network {
				candidate.Network = *partialCfg.Network
				updated = true
			}
			if partialCfg.TokenAddress != nil && *partialCfg.TokenAddress != candidate.TokenAddress {
				candidate.TokenAddress = *partialCfg.TokenAddress
				updated = true
			}
			if partialCfg.TokenName != nil && *partialCfg.TokenName != candidate.TokenName {
				candidate.TokenName = *partialCfg.TokenName
				updated = true
			}
			if partialCfg.TokenVersion != nil && *partialCfg.TokenVersion != candidate.TokenVersion {
				candidate.TokenVersion = *partialCfg.TokenVersion
				updated = true
			}
			if partialCfg.TokenDecimals != nil && *partialCfg.TokenDecimals != candidate.TokenDecimals {
				candidate.TokenDecimals = *partialCfg.TokenDecimals
				updated = true
			}
			if partialCfg.TokenTransferMethod != nil && *partialCfg.TokenTransferMethod != candidate.TokenTransferMethod {
				candidate.TokenTransferMethod = *partialCfg.TokenTransferMethod
				updated = true
			}
			if partialCfg.TokenSupportsEIP2612 != nil && *partialCfg.TokenSupportsEIP2612 != candidate.TokenSupportsEIP2612 {
				candidate.TokenSupportsEIP2612 = *partialCfg.TokenSupportsEIP2612
				updated = true
			}

			if updated {
				if err := candidate.Validate(); err != nil {
					logger.Warn("rejecting invalid config update", zap.Error(err))
					return
				}
				*cfg = candidate
				logger.Info("config updated via zenoh, signaling restart")
				select {
				case restartCh <- struct{}{}:
				default:
				}
			}
		},
	}, nil)
	if err != nil {
		logger.Fatal("failed to declare config subscriber", zap.Error(err))
	}
	defer func() {
		if err := sub.Undeclare(); err != nil {
			logger.Warn("failed to undeclare zenoh subscriber", zap.Error(err))
		}
	}()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	// AIP job input does not carry the Tunnel-verified x402 context, complete
	// correlation tuple, or durable replay reservation.  It must therefore
	// never publish directly to Zenoh.  Keep discovery/registration available
	// but fail direct job execution closed until the shared gateway can forward
	// a verified paid ActionEvent through the same PostAction contract.
	aipSrv := aipagent.Build(cfg, func(_ []byte) error {
		return fmt.Errorf("direct AIP action execution is disabled; use the paid Tunnel action endpoint")
	}, logger)
	if aipSrv != nil {
		go func() {
			if err := aipSrv.Run(ctx); err != nil {
				logger.Warn("AIP agent server stopped", zap.Error(err))
			}
		}()
	}

	for {
		router := setupRouter(cfg, aipSrv, logger)
		client := internal.NewClient(cfg.ProxyWSURL, cfg.RobotID, router, logger)
		// The current shared protocol supplies the configured robot ID on this
		// outbound connection. A signed robot-to-payee handshake is an upstream
		// Gateway/Tunnel dependency; the simulator bridge never receives a key.

		clientCtx, clientCancel := context.WithCancel(ctx)

		go func() {
			select {
			case <-restartCh:
				logger.Info("restarting internal client to apply new config...")
				clientCancel()
			case <-clientCtx.Done():
			}
		}()

		client.Run(clientCtx)
		clientCancel()

		if ctx.Err() != nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// pristineNetworkConfigs is x402's built-in asset table, captured before any
// deployment override. It makes hot reloads reversible when token_address is
// removed or the selected network changes.
var pristineNetworkConfigs = func() map[string]evm.NetworkConfig {
	snapshot := make(map[string]evm.NetworkConfig, len(evm.NetworkConfigs))
	for network, cfg := range evm.NetworkConfigs {
		snapshot[network] = cfg
	}
	return snapshot
}()

var registeredNetwork string

func restoreNetworkDefault(network string) {
	if original, ok := pristineNetworkConfigs[network]; ok {
		evm.NetworkConfigs[network] = original
		return
	}
	delete(evm.NetworkConfigs, network)
}

// registerTokenAsset preserves the custom-token support from main while
// allowing the execution-gated payment flow below to use the same config.
func registerTokenAsset(cfg *config.Config, logger *zap.Logger) {
	if registeredNetwork != "" && registeredNetwork != cfg.Network {
		restoreNetworkDefault(registeredNetwork)
		registeredNetwork = ""
	}
	if cfg.TokenAddress == "" {
		restoreNetworkDefault(cfg.Network)
		registeredNetwork = ""
		return
	}
	chainID, ok := cfg.ChainID()
	if !ok {
		logger.Warn("skipping token registration for non-eip155 network", zap.String("network", cfg.Network))
		return
	}
	asset := evm.AssetInfo{
		Address:  cfg.TokenAddress,
		Name:     cfg.TokenName,
		Version:  cfg.TokenVersion,
		Decimals: cfg.TokenDecimals,
	}
	if cfg.TokenTransferMethod == config.TransferMethodPermit2 {
		asset.AssetTransferMethod = evm.AssetTransferMethodPermit2
		asset.SupportsEip2612 = cfg.TokenSupportsEIP2612
	}
	evm.NetworkConfigs[cfg.Network] = evm.NetworkConfig{
		ChainID:      chainID,
		DefaultAsset: asset,
	}
	registeredNetwork = cfg.Network
	logger.Info("registered payment token",
		zap.String("network", cfg.Network),
		zap.String("address", cfg.TokenAddress),
		zap.String("name", cfg.TokenName),
		zap.Int("decimals", cfg.TokenDecimals),
		zap.String("transfer_method", cfg.TokenTransferMethod),
		zap.Bool("supports_eip2612", cfg.TokenSupportsEIP2612),
	)
}

func setupRouter(cfg *config.Config, aipSrv *aipserver.Server, logger *zap.Logger) *gin.Engine {
	registerTokenAsset(cfg, logger)
	router := gin.New()
	router.Use(requestRateLimit())

	router.Use(cors.New(cors.Config{
		AllowOrigins: []string{"*"},
		AllowMethods: []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowHeaders: []string{
			"Origin",
			"Content-Type",
			"Authorization",
			"PAYMENT-SIGNATURE",
			"Access-Control-Expose-Headers",
			"payment-signature",
		},
		ExposeHeaders: []string{
			"PAYMENT-REQUIRED",
			"PAYMENT-RESPONSE",
		},
		// Auth is carried by the PAYMENT-SIGNATURE header (x402), never by cookies.
		// With a wildcard origin the CORS spec forbids credentialed requests, and
		// enabling both is silently rejected by browsers — so we keep it disabled.
		AllowCredentials: false,
		MaxAge:           12 * time.Hour,
	}))

	facilitatorClient := x402http.NewHTTPFacilitatorClient(&x402http.FacilitatorConfig{
		URL: cfg.FacilitatorURL,
	})

	routes := x402http.RoutesConfig{
		"POST /action": {
			Accepts: x402http.PaymentOptions{
				{
					Scheme:  "exact",
					Price:   cfg.Price,
					Network: x402.Network(cfg.Network),
					PayTo:   cfg.EVMPayeeAddress,
				},
			},
			Description: "Run a paid robot action",
			MimeType:    "application/json",
		},
	}

	// The stock gin middleware settles as soon as the handler returns < 400,
	// which is incompatible with the immediate accepted/pending contract: a
	// 202 would settle before the simulator ran. The gate below performs the
	// same 402/verify handling synchronously but defers settlement to the
	// handler's execution watcher, which settles only after simulator success.
	paymentServer := x402http.Newx402HTTPResourceServer(routes,
		x402.WithFacilitatorClient(facilitatorClient))
	paymentServer.Register(x402.Network(cfg.Network), evmexact.NewExactEvmScheme())
	{
		initCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		if err := paymentServer.Initialize(initCtx); err != nil {
			logger.Warn("failed to initialize x402 payment server", zap.Error(err))
		}
		cancel()
	}
	router.Use(deferredSettlementGate(paymentServer, logger))

	h := handlers.NewHandlersForRobot(logger, cfg.RobotID)
	catalog, catalogErr := handlers.LoadSkillCatalog(os.Getenv("SKILL_CATALOG_PATH"), cfg.Price)
	if catalogErr != nil {
		logger.Warn("skill catalog unavailable; refusing all paid actions", zap.Error(catalogErr))
	} else {
		h.SkillCatalog = catalog
	}
	rawAllowedSkills, configured := os.LookupEnv("ALLOWED_ACTIONS")
	h.AllowedSkills = allowedSkillsFromEnv(rawAllowedSkills, configured, h.KnownSkillIDs())
	if configured {
		if len(h.AllowedSkills) == 0 {
			logger.Warn("ALLOWED_ACTIONS is empty or contains no registered skills; refusing all actions")
		}
	} else {
		// The public action route must not acquire an implicit capability merely
		// because this binary knows about a profile. Without an explicit
		// deployment allowlist, the handler returns ALLOWLIST_NOT_CONFIGURED.
		logger.Warn("ALLOWED_ACTIONS not set; refusing all actions")
	}
	if raw := os.Getenv("MAX_ACTION_DURATION_SECONDS"); raw != "" {
		if seconds, err := strconv.ParseFloat(raw, 64); err == nil && seconds > 0 {
			h.MaxDurationSeconds = seconds
		}
	}
	RegisterAllRoutes(router, h)

	// Serve the AIP A2A contract (/.well-known/agent-card.json, /invoke, ...)
	// for any path Gin doesn't own. The gateway proxies these to us verbatim.
	if aipSrv != nil {
		router.NoRoute(gin.WrapH(aipSrv.Handler()))
	}

	return router
}

// allowedSkillsFromEnv preserves the distinction between an absent setting and
// an explicitly empty one. Both fail closed, but the nil result makes it clear
// that no deployment allowlist was provided at all.
func allowedSkillsFromEnv(raw string, configured bool, known map[string]struct{}) map[string]struct{} {
	if !configured {
		return nil
	}
	return parseAllowedSkills(raw, known)
}

// parseAllowedSkills turns the deployment registration/allowlist into the
// exact set the handler enforces and advertises. Values not declared by the
// loaded robot-scoped catalog are discarded, so an environment typo cannot
// create a new actuator capability.
func parseAllowedSkills(raw string, known map[string]struct{}) map[string]struct{} {
	allowed := make(map[string]struct{})
	for _, skill := range strings.Split(raw, ",") {
		if skill = strings.TrimSpace(skill); skill != "" {
			if _, registered := known[skill]; !registered {
				continue
			}
			allowed[skill] = struct{}{}
		}
	}
	return allowed
}

type rateLimitEntry struct {
	windowStart time.Time
	count       int
}

var rateLimitState = struct {
	sync.Mutex
	clients   map[string]rateLimitEntry
	lastSweep time.Time
}{clients: make(map[string]rateLimitEntry)}

func requestRateLimit() gin.HandlerFunc {
	limit := 60
	if raw := os.Getenv("ACTION_RATE_LIMIT_RPM"); raw != "" {
		if configured, err := strconv.Atoi(raw); err == nil && configured > 0 {
			limit = configured
		}
	}
	return func(c *gin.Context) {
		client := c.ClientIP()
		now := time.Now()
		rateLimitState.Lock()
		// Evict windows older than one minute at most once per minute so the
		// client map cannot grow unbounded with one-off IPs.
		if now.Sub(rateLimitState.lastSweep) >= time.Minute {
			for ip, e := range rateLimitState.clients {
				if now.Sub(e.windowStart) >= time.Minute {
					delete(rateLimitState.clients, ip)
				}
			}
			rateLimitState.lastSweep = now
		}
		entry := rateLimitState.clients[client]
		if entry.windowStart.IsZero() || now.Sub(entry.windowStart) >= time.Minute {
			entry = rateLimitEntry{windowStart: now}
		}
		entry.count++
		rateLimitState.clients[client] = entry
		allowed := entry.count <= limit
		rateLimitState.Unlock()
		if !allowed {
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"error":      "action rate limit exceeded",
				"error_code": "RATE_LIMITED",
			})
			return
		}
		c.Next()
	}
}

// RegisterAllRoutes registers all real handlers on the router.
func RegisterAllRoutes(router *gin.Engine, h *handlers.Handlers) {
	router.GET("/robot", h.GetRobotProfile)
	router.GET("/skills", h.GetSkills)
	router.POST("/action", h.PostAction)
	router.GET("/action/:action_id/status", h.GetActionStatus)
}

// deferredSettlementGate is the execution-gated replacement for the stock
// x402 gin middleware. It answers 402 for unpaid requests and verifies paid
// ones synchronously, but instead of settling on response it injects a
// handlers.SettleFunc into the context; the action handler invokes it only
// after the correlated simulator result reports success.
func deferredSettlementGate(server *x402http.HTTPServer, logger *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		reqCtx := x402http.HTTPRequestContext{
			Adapter: ginmw.NewGinAdapter(c),
			Path:    c.Request.URL.Path,
			Method:  c.Request.Method,
		}
		if !server.RequiresPayment(reqCtx) {
			c.Next()
			return
		}

		ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
		defer cancel()
		result := server.ProcessHTTPRequest(ctx, reqCtx, nil)

		switch result.Type {
		case x402http.ResultNoPaymentRequired:
			c.Next()
		case x402http.ResultPaymentError:
			for key, value := range result.Response.Headers {
				c.Header(key, value)
			}
			if result.Response.IsHTML {
				c.Data(result.Response.Status, "text/html; charset=utf-8", []byte(result.Response.Body.(string)))
			} else {
				c.JSON(result.Response.Status, result.Response.Body)
			}
			c.Abort()
		case x402http.ResultPaymentVerified:
			if result.PaymentPayload == nil || result.PaymentRequirements == nil {
				logger.Warn("verified payment missing payload/requirements; refusing")
				c.AbortWithStatusJSON(http.StatusPaymentRequired, gin.H{"error": "payment verification incomplete"})
				return
			}
			c.Set("x402_payload", *result.PaymentPayload)
			c.Set("x402_requirements", *result.PaymentRequirements)
			// Capture verified payment data by value: the settle callback
			// runs after this request context is recycled by gin.
			payload := *result.PaymentPayload
			requirements := *result.PaymentRequirements
			declared := result.DeclaredExtensions
			var settle handlers.SettleFunc = func(settleCtx context.Context) (*handlers.SettlementRecord, error) {
				settleResult := server.ProcessSettlement(settleCtx, payload, requirements, nil, nil, declared)
				if settleResult == nil {
					return nil, fmt.Errorf("settlement returned no result")
				}
				if !settleResult.Success {
					reason := settleResult.ErrorReason
					if reason == "" {
						reason = "settlement failed"
					}
					return nil, fmt.Errorf("%s", reason)
				}
				record := &handlers.SettlementRecord{
					Transaction: settleResult.Transaction,
					Network:     string(settleResult.Network),
					Payer:       settleResult.Payer,
				}
				for key, value := range settleResult.Headers {
					if strings.EqualFold(key, "PAYMENT-RESPONSE") {
						record.PaymentResponse = value
					}
				}
				return record, nil
			}
			c.Set("x402_settle", settle)
			logger.Debug("payment verified; settlement deferred until simulator success")
			c.Next()
		}
	}
}
