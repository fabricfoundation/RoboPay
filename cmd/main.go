package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	ginmw "github.com/x402-foundation/x402/go/http/gin"
	evm "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
	"go.uber.org/zap"

	"github.com/fabricfoundation/robot-tunnel-client/config"
	"github.com/fabricfoundation/robot-tunnel-client/internal"
	"github.com/fabricfoundation/robot-tunnel-client/internal/handlers"
	"github.com/fabricfoundation/robot-tunnel-client/internal/middleware"
)

const (
	ProxyWSURL     = "ws://localhost:8080/api/core/ws/robot"
	FacilitatorURL = "https://x402.org/facilitator"
)


func main() {
	configPath := flag.String("config", "config.json", "Path to config file")
	flag.Parse()

	logger, _ := zap.NewProduction()
	defer logger.Sync()

	if err := godotenv.Load(); err != nil {
		logger.Warn("failed to load .env file", zap.Error(err))
	}

	cfg, err := config.LoadConfig(*configPath)
	if err != nil {
		logger.Fatal("configuration error", zap.Error(err))
	}

	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
	if err != nil {
		logger.Fatal("failed to open zenoh session", zap.Error(err))
	}
	defer session.Close(nil)

	zenohPub := middleware.NewZenohSessionPublisher(session)

	restartCh := make(chan struct{}, 1)
	subTopic := "robot/config/" + cfg.RobotID
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
	defer sub.Undeclare()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	for {
		router := setupRouter(cfg, zenohPub, logger)
		client := internal.NewClient(ProxyWSURL, cfg.RobotID, router, logger)

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

func setupRouter(cfg *config.Config, zenohPub *middleware.ZenohSessionPublisher, logger *zap.Logger) *gin.Engine {
	zenohEvents := middleware.ZenohPublishMiddleware(zenohPub, "robot/tunnel/events", logger)

	router := gin.New()
	router.Use(zenohEvents)

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
		URL: FacilitatorURL,
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

	router.Use(ginmw.X402Payment(ginmw.Config{
		Routes:      routes,
		Facilitator: facilitatorClient,
		Schemes: []ginmw.SchemeConfig{
			{Network: x402.Network(cfg.Network), Server: evm.NewExactEvmScheme()},
		},
		Timeout: 30 * time.Second,
	}))

	h := handlers.NewHandlers(zenohPub, logger)
	RegisterAllRoutes(router, h)

	return router
}

// RegisterAllRoutes registers all real handlers on the router.
func RegisterAllRoutes(router *gin.Engine, h *handlers.Handlers) {
	router.POST("/action", h.PostAction)
}
