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

	// The job offering is what makes the robot purchasable on the BitAgent
	// marketplace: without it the agent is discoverable but no job can be
	// created against it. Jobs arrive through the gateway job queue
	// (ViaGateway) and land in the handler above.
	price := cfg.PriceAmount()
	jobOfferings := []types.AgentJobOffering{{
		ID:          "robot_action",
		Name:        "robot_action",
		Description: "Execute a single action on the robot (e.g. a motion command). The command is forwarded through the Fabric RoboPay tunnel to the robot's onboard controller; the robot-side safety layer always has the final say.",
		Type:        "JOB",
		Price:       price,
		PriceV2:     map[string]any{"type": "fixed", "amount": price, "currency": "USDC"},
		JobInput:    `JSON action command, e.g. {"action":"move","direction":"forward","distance_m":1.0}`,
		JobOutput:   `{"status":"accepted"} once the action is on the robot's command bus`,
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
		Active:     true,
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
			Description: "Execute motion commands on the physical robot",
			InputModes:  []string{"text/plain", "application/json"},
			OutputModes: []string{"application/json"},
		}},
		CostModel:    &types.CostModel{BaseCallFee: &price},
		JobOfferings: jobOfferings,
	}, handler, nil)
}
