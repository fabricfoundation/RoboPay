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

	"github.com/fabricfoundation/robot-tunnel-client/tunnel"
	"github.com/fabricfoundation/robot-tunnel-client/tunnel/handlers"
	"github.com/fabricfoundation/robot-tunnel-client/tunnel/middleware"
)

func main() {
	robotID := flag.String("id", "", "Robot ID (required)")
	flag.Parse()

	logger, _ := zap.NewProduction()
	defer logger.Sync()

	if err := godotenv.Load(); err != nil {
		logger.Warn("failed to load .env file", zap.Error(err))
	}

	// CLI flag takes priority; fall back to env var
	if *robotID == "" {
		*robotID = os.Getenv("ROBOT_ID")
	}
	if *robotID == "" {
		logger.Fatal("robot ID is required: pass -id flag or set ROBOT_ID env var")
	}

	proxyWSURL := os.Getenv("PROXY_WS_URL")
	if proxyWSURL == "" {
		logger.Fatal("PROXY_WS_URL is required, e.g. wss://proxy.example.com/ws/robot")
	}

	// Initialize Zenoh session
	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
	if err != nil {
		logger.Fatal("failed to open zenoh session", zap.Error(err))
	}
	defer session.Close(nil)

	zenohPub := middleware.NewZenohSessionPublisher(session)
	zenohEvents := middleware.ZenohPublishMiddleware(zenohPub, "robot/tunnel/events", logger)

	router := tunnel.NewRouter()
	tunnel.RegisterDemoHandlers(router, zenohEvents)
	handlers.RegisterAll(router, *robotID, zenohEvents)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	client := tunnel.NewClient(proxyWSURL, *robotID, router, logger)
	client.Run(ctx)
}
