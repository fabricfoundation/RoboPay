package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"github.com/gin-contrib/cors"
	"github.com/joho/godotenv"
	x402http "github.com/x402-foundation/x402/go/http"
	ginmw "github.com/x402-foundation/x402/go/http/gin"
	evm "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
	"go.uber.org/zap"

	"github.com/fabricfoundation/robot-tunnel-client/internal"
	"github.com/fabricfoundation/robot-tunnel-client/internal/handlers"
	"github.com/fabricfoundation/robot-tunnel-client/internal/middleware"
)

func main() {
	robotID := flag.String("id", "", "Robot ID (required)")
	proxyWSURLFlag := flag.String("proxy-ws-url", "", "Proxy WebSocket URL (required)")
	flag.Parse()

	logger, _ := zap.NewProduction()
	defer logger.Sync()

	if err := godotenv.Load(); err != nil {
		logger.Warn("failed to load .env file", zap.Error(err))
	}

	if *robotID == "" {
		*robotID = os.Getenv("ROBOT_ID")
	}
	if *robotID == "" {
		logger.Fatal("robot ID is required: pass -id flag or set ROBOT_ID env var")
	}

	proxyWSURL := *proxyWSURLFlag
	if proxyWSURL == "" {
		proxyWSURL = os.Getenv("PROXY_WS_URL")
	}
	if proxyWSURL == "" {
		logger.Fatal("proxy ws url is required: pass -proxy-ws-url flag or set PROXY_WS_URL env var")
	}

	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
	if err != nil {
		logger.Fatal("failed to open zenoh session", zap.Error(err))
	}
	defer session.Close(nil)

	zenohPub := middleware.NewZenohSessionPublisher(session)
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


	evmAddress := os.Getenv("EVM_PAYEE_ADDRESS")
	if evmAddress == "" {
		logger.Fatal("EVM_PAYEE_ADDRESS environment variable is required")
	}

	facilitatorURL := os.Getenv("FACILITATOR_URL")
	if facilitatorURL == "" {
		logger.Fatal("FACILITATOR_URL environment variable is required")
	}

	facilitatorClient := x402http.NewHTTPFacilitatorClient(&x402http.FacilitatorConfig{
		URL: facilitatorURL,
	})

	routes := x402http.RoutesConfig{
		"GET /weather": {
			Accepts: x402http.PaymentOptions{
				{
					Scheme:  "exact",
					Price:   "$0.001",
					Network: "eip155:84532",
					PayTo:   evmAddress,
				},
			},
			Description: "Get weather data for a city",
			MimeType:    "application/json",
		},
	}

	router.Use(ginmw.X402Payment(ginmw.Config{
		Routes:      routes,
		Facilitator: facilitatorClient,
		Schemes: []ginmw.SchemeConfig{
			{Network: "eip155:84532", Server: evm.NewExactEvmScheme()},
		},
		Timeout: 30 * time.Second,
	}))

	RegisterAllRoutes(router, *robotID)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	client := internal.NewClient(proxyWSURL, *robotID, router, logger)
	client.Run(ctx)
}

// RegisterAllRoutes registers all real handlers on the router.
func RegisterAllRoutes(router *gin.Engine, robotID string) {
	router.GET("/weather", handlers.GetWeather)
}
