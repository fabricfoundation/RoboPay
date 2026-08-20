package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"net/http"
	"os"
	"os/signal"
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
	evm "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
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

	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
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
				EVMPayeeAddress *string `json:"evm_payee_address"`
				Price           *string `json:"price"`
				Network         *string `json:"network"`
			}
			if err := json.Unmarshal(sample.Payload().Bytes(), &partialCfg); err != nil {
				logger.Warn("failed to parse config update", zap.Error(err))
				return
			}

			updated := false
			if partialCfg.EVMPayeeAddress != nil && *partialCfg.EVMPayeeAddress != cfg.EVMPayeeAddress {
				cfg.EVMPayeeAddress = *partialCfg.EVMPayeeAddress
				updated = true
			}
			if partialCfg.Price != nil && *partialCfg.Price != cfg.Price {
				cfg.Price = *partialCfg.Price
				updated = true
			}
			if partialCfg.Network != nil && *partialCfg.Network != cfg.Network {
				cfg.Network = *partialCfg.Network
				updated = true
			}

			if updated {
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

	aipSrv := aipagent.Build(cfg, handlers.PublishRobotAction, logger)
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

func setupRouter(cfg *config.Config, aipSrv *aipserver.Server, logger *zap.Logger) *gin.Engine {
	router := gin.New()

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
		AllowCredentials: true,
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

	// The stock gin middleware settles as soon as the handler returns anything
	// under 400. The tunnel contract answers 202 the moment an action is
	// accepted, long before the robot has finished, so that middleware would
	// charge the payer for work that may still fail. This gate does the same
	// 402/verify half synchronously and hands the settlement to the handler,
	// which runs it only once the correlated result reports success.
	paymentServer := x402http.Newx402HTTPResourceServer(
		routes, x402.WithFacilitatorClient(facilitatorClient),
	)
	paymentServer.Register(x402.Network(cfg.Network), evm.NewExactEvmScheme())
	{
		initCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		if err := paymentServer.Initialize(initCtx); err != nil {
			logger.Warn("failed to initialise the x402 payment server", zap.Error(err))
		}
		cancel()
	}
	router.Use(deferredSettlementGate(paymentServer, logger))

	h := handlers.NewHandlers(logger)
	h.RobotID = cfg.RobotID
	h.Network = cfg.Network
	h.PayTo = cfg.EVMPayeeAddress
	h.ProfileID = os.Getenv("ROBOT_PROFILE_ID")
	h.SkillCatalogPath = os.Getenv("SKILL_CATALOG_PATH")
	// Results are recorded from Zenoh so the status endpoint answers from real
	// execution. Without it the tunnel could only ever report "pending".
	if err := h.StartResultSubscriber(); err != nil {
		logger.Warn("action status will stay pending: result subscriber failed",
			zap.Error(err))
	}
	RegisterAllRoutes(router, h)

	// Serve the AIP A2A contract (/.well-known/agent-card.json, /invoke, ...)
	// for any path Gin doesn't own. The gateway proxies these to us verbatim.
	if aipSrv != nil {
		router.NoRoute(gin.WrapH(aipSrv.Handler()))
	}

	return router
}

// deferredSettlementGate replaces the stock x402 middleware for one reason: the
// stock one settles on response, and this tunnel answers 202 before the robot
// has run. It performs the same work up to and including verification — an
// unpaid request still gets 402 with the advertised requirements, and a payment
// the facilitator rejects still never reaches the robot — but instead of
// settling it puts a handlers.SettleFunc in the request context. The action
// handler calls that function only after the simulator reports success, so a
// failed or timed-out episode leaves the authorization signed and unspent.
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
				body, _ := result.Response.Body.(string)
				c.Data(result.Response.Status, "text/html; charset=utf-8", []byte(body))
			} else {
				c.JSON(result.Response.Status, result.Response.Body)
			}
			c.Abort()
		case x402http.ResultPaymentVerified:
			if result.PaymentPayload == nil || result.PaymentRequirements == nil {
				logger.Warn("verified payment carried no payload or requirements; refusing")
				c.AbortWithStatusJSON(http.StatusPaymentRequired, gin.H{
					"error": "payment verification incomplete",
				})
				return
			}
			c.Set("x402_payload", *result.PaymentPayload)
			c.Set("x402_requirements", *result.PaymentRequirements)
			// Copied by value: the settle callback outlives this request, and
			// gin recycles the context as soon as the 202 goes out.
			payload := *result.PaymentPayload
			requirements := *result.PaymentRequirements
			declared := result.DeclaredExtensions
			c.Set("x402_settle", handlers.SettleFunc(
				func(settleCtx context.Context) (*handlers.SettlementRecord, error) {
					settlement := server.ProcessSettlement(
						settleCtx, payload, requirements, nil, nil, declared,
					)
					if settlement == nil {
						return nil, errors.New("settlement returned no result")
					}
					if !settlement.Success {
						reason := settlement.ErrorReason
						if reason == "" {
							reason = "settlement failed"
						}
						return nil, errors.New(reason)
					}
					return &handlers.SettlementRecord{
						Transaction: settlement.Transaction,
						Network:     string(settlement.Network),
						Payer:       settlement.Payer,
					}, nil
				}))
			c.Next()
		default:
			c.Next()
		}
	}
}

// RegisterAllRoutes registers all real handlers on the router.
func RegisterAllRoutes(router *gin.Engine, h *handlers.Handlers) {
	// Discovery and status are read-only; POST /action is unchanged.
	router.GET("/robot", h.GetRobotProfile)
	router.GET("/skills", h.GetSkills)
	router.POST("/action", h.PostAction)
	router.GET("/action/:action_id/status", h.GetActionStatus)
}
