package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	"github.com/joho/godotenv"
	"go.uber.org/zap"

	"robot-autonomous-payment/tunnel"
	"robot-autonomous-payment/tunnel/handlers"
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

	router := tunnel.NewRouter()
	tunnel.RegisterDemoHandlers(router)
	handlers.RegisterAll(router, *robotID)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	client := tunnel.NewClient(proxyWSURL, *robotID, router, logger)
	client.Run(ctx)
}
