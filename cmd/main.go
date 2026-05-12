package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/joho/godotenv"
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

	router := internal.NewRouter()
	internal.RegisterDemoHandlers(router, zenohEvents)
	RegisterAllRoutes(router, *robotID, zenohEvents)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	client := internal.NewClient(proxyWSURL, *robotID, router, logger)
	client.Run(ctx)
}

// RegisterAllRoutes registers all real handlers on the router.
func RegisterAllRoutes(router *internal.Router, robotID string, middlewares ...internal.Middleware) {
	router.Register("GET", "/id", handlers.RobotID(robotID), middlewares...)
}
