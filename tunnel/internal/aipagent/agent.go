package aipagent

import (
	"context"
	"encoding/json"
	"time"

	"github.com/unibaseio/aip-go-sdk/server"
	"github.com/unibaseio/aip-go-sdk/types"
	"github.com/unibaseio/aip-go-sdk/wrappers"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/config"
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

	// AIP registration remains useful for discovery, but direct AIP job input
	// is not a Tunnel-verified x402 ActionEvent.  Keep this offering inactive
	// until the shared gateway supplies that verified envelope; otherwise a
	// marketplace job could bypass the paid-action contract and publish to
	// Zenoh without allowlist, correlation, or durable replay protection.
	price := cfg.PriceAmount()
	jobOfferings := []types.AgentJobOffering{{
		ID:          "robot_action",
		Name:        "robot_action",
		Description: "Reserved for Tunnel-verified paid actions. Direct AIP action execution is disabled until the shared gateway forwards the verified action envelope.",
		Type:        "JOB",
		Price:       price,
		PriceV2:     map[string]any{"type": "fixed", "amount": price, "currency": "USDC"},
		JobInput:    `A Tunnel-verified paid action envelope (not currently accepted directly by AIP).`,
		JobOutput:   `{"status":"error","error":"use paid Tunnel action endpoint"}`,
		Requirement: map[string]any{
			"type":     "object",
			"required": []string{"action"},
			"properties": map[string]any{
				"action": map[string]any{"type": "string", "description": "action name, e.g. move / rotate / stop"},
			},
		},
		Deliverable: map[string]any{
			"type":     "object",
			"required": []string{"status"},
			"properties": map[string]any{
				"status": map[string]any{"type": "string", "description": "acceptance status from the robot's command bus"},
			},
		},
		SLAMinutes: 1,
		Active:     false,
	}}

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
		Skills: []types.AgentSkillCard{{
			ID:          cfg.RobotID + "_robot_action",
			Name:        "robot_action",
			Description: "Robot discovery; execution is available only through the paid Tunnel action endpoint",
			InputModes:  []string{"text/plain", "application/json"},
			OutputModes: []string{"application/json"},
		}},
		CostModel:    &types.CostModel{BaseCallFee: &price},
		JobOfferings: jobOfferings,
	}, handler, nil)
}
