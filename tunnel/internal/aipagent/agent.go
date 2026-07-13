package aipagent

import (
	"context"
	"encoding/json"
	"time"

	"github.com/unibaseio/aip-go-sdk/server"
	"github.com/unibaseio/aip-go-sdk/wrappers"
	"go.uber.org/zap"

	"github.com/fabricfoundation/robot-tunnel-client/config"
)

type PublishFunc func(payload []byte) error

func Build(cfg *config.Config, publish PublishFunc, logger *zap.Logger) *server.Server {
	if !cfg.AIPEnabled {
		return nil
	}

	handler := func(ctx context.Context, input string) (string, error) {
		var payload any
		if json.Valid([]byte(input)) {
			payload = json.RawMessage(input)
		} else {
			payload = input
		}
		event, err := json.Marshal(map[string]any{
			"payload":   payload,
			"source":    "aip",
			"timestamp": time.Now().Format(time.RFC3339),
		})
		if err != nil {
			return "", err
		}
		if err := publish(event); err != nil {
			logger.Warn("failed to publish AIP action event", zap.Error(err))
			return "", err
		}
		return `{"status":"accepted"}`, nil
	}

	endpointURL := cfg.AIPEndpointURL()
	logger.Info("registering robot as AIP agent",
		zap.String("robot_id", cfg.RobotID),
		zap.String("endpoint_url", endpointURL),
	)

	return wrappers.ExposeAsA2A(wrappers.ExposeOptions{
		Name:        cfg.AIPAgentName,
		Handle:      cfg.RobotID,
		UserID:      cfg.AIPUserID,
		PrivyToken:  cfg.AIPPrivyToken,
		AIPEndpoint: cfg.AIPEndpoint,
		GatewayURL:  cfg.AIPGatewayURL,
		EndpointURL: endpointURL,
		ViaGateway:  true,
		ChainID:     cfg.AIPChainID,
		Host:        "127.0.0.1",
		Port:        cfg.AIPLocalPort,
	}, handler, nil)
}
