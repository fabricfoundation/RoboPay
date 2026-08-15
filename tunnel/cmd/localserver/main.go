// localserver is a standalone binary for live end-to-end payment
// verification against the real x402.org facilitator and real Base
// Sepolia network -- bypassing the Fabric WebSocket proxy (which this
// environment has no access to) by listening on a real local HTTP port
// instead. It reuses the exact same router wiring as tunnel/cmd/main.go
// (X402VerifyOnly + PostAction + GetActionStatus + ExecutionWatcher
// subscribed to robot/tunnel/result).
package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	evm "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/handlers"
)

func main() {
	port := flag.String("port", "8402", "local HTTP port to listen on")
	payTo := flag.String("payto", "", "EVM address to receive payment (required)")
	network := flag.String("network", "eip155:84532", "x402 CAIP-2 network id")
	price := flag.String("price", "$0.001", "price per action")
	facilitatorURL := flag.String("facilitator", "https://x402.org/facilitator", "x402 facilitator URL")
	allowedActions := flag.String("allowed-actions", "k1_navigate_avoid_obstacles", "comma-separated allowlist")
	flag.Parse()

	if *payTo == "" {
		log.Fatal("--payto is required")
	}
	os.Setenv("ALLOWED_ACTIONS", *allowedActions)

	logger, _ := zap.NewDevelopment()
	defer logger.Sync()

	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
	if err != nil {
		logger.Fatal("failed to open zenoh session", zap.Error(err))
	}
	defer func() {
		if err := session.Close(nil); err != nil {
			logger.Warn("failed to close zenoh session", zap.Error(err))
		}
	}()

	facilitatorClient := x402http.NewHTTPFacilitatorClient(&x402http.FacilitatorConfig{
		URL: *facilitatorURL,
	})

	routes := x402http.RoutesConfig{
		"POST /action": {
			Accepts: x402http.PaymentOptions{
				{Scheme: "exact", Price: *price, Network: x402.Network(*network), PayTo: *payTo},
			},
			Description: "Booster K1 obstacle-navigation action (live E2E test)",
			MimeType:    "application/json",
		},
	}

	server := x402http.Newx402HTTPResourceServer(routes, x402.WithFacilitatorClient(facilitatorClient))
	server.Register(x402.Network(*network), evm.NewExactEvmScheme())

	initCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := server.Initialize(initCtx); err != nil {
		logger.Fatal("failed to initialize x402 server", zap.Error(err))
	}

	h := handlers.NewHandlers(logger)
	watcher := handlers.NewExecutionWatcher(h.Store, server, logger)

	resultKe, err := zenoh.NewKeyExpr("robot/tunnel/result")
	if err != nil {
		logger.Fatal("failed to create result key expression", zap.Error(err))
	}
	resultSub, err := session.DeclareSubscriber(resultKe, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			logger.Info("received robot/tunnel/result, handing to ExecutionWatcher")
			watcher.HandleResult(sample.Payload().Bytes())
		},
	}, nil)
	if err != nil {
		logger.Fatal("failed to declare result subscriber", zap.Error(err))
	}
	defer func() {
		if err := resultSub.Undeclare(); err != nil {
			logger.Warn("failed to undeclare result subscriber", zap.Error(err))
		}
	}()

	gin.SetMode(gin.DebugMode)
	router := gin.New()
	router.Use(gin.Logger())
	router.Use(handlers.X402VerifyOnly(server, 30*time.Second))
	router.POST("/action", h.PostAction)
	router.GET("/action/:id/status", h.GetActionStatus)

	srv := &http.Server{Addr: ":" + *port, Handler: router}

	go func() {
		logger.Info("localserver listening", zap.String("addr", srv.Addr))
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("server failed", zap.Error(err))
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()

	logger.Info("shutting down...")
	shutdownCtx, cancel2 := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel2()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		logger.Warn("server shutdown error", zap.Error(err))
	}
}
