package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/mppay"
)

const (
	RobotActionTopic = "robot/tunnel/action"

	ProtocolX402 = "x402"
	ProtocolMPP  = "mpp"
)

type zenohPublisher interface {
	Publish(keyExpr string, payload []byte) error
}

type zenohSessionPublisher struct {
	session zenoh.Session
}

func (z *zenohSessionPublisher) Publish(keyExpr string, payload []byte) error {
	ke, err := zenoh.NewKeyExpr(keyExpr)
	if err != nil {
		return err
	}
	return z.session.Put(ke, zenoh.NewZBytes(payload), nil)
}

var (
	zenohOnce      sync.Once
	zenohPub       zenohPublisher
	zenohInitError error
)

func getZenohPublisher() (zenohPublisher, error) {
	zenohOnce.Do(func() {
		session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
		if err != nil {
			zenohInitError = err
			return
		}
		zenohPub = &zenohSessionPublisher{session: session}
	})

	if zenohInitError != nil {
		return nil, zenohInitError
	}

	return zenohPub, nil
}

func PublishRobotAction(payload []byte) error {
	pub, err := getZenohPublisher()
	if err != nil {
		return err
	}
	return pub.Publish(RobotActionTopic, payload)
}

type Handlers struct {
	Logger         *zap.Logger
	SettlementMgr  *settlement.SettlementManager
	Publisher      zenohPublisher
	zenohSession   zenoh.Session
	resultSub      zenoh.Subscriber
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return &Handlers{
		Logger:        logger,
		SettlementMgr: settlement.NewSettlementManager(),
	}
}

// InitZenoh initializes the Zenoh session and subscribes to robot/tunnel/result.
// Must be called after creation, before handling requests.
func (h *Handlers) InitZenoh() error {
	session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
	if err != nil {
		return err
	}
	h.zenohSession = session

	// Subscribe to terminal results from the robot
	ke, err := zenoh.NewKeyExpr(RobotResultTopic)
	if err != nil {
		return err
	}

	h.resultSub, err = session.DeclareSubscriber(ke, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var result settlement.ResultEnvelope
			if err := json.Unmarshal(sample.Payload().Bytes(), &result); err != nil {
				h.Logger.Warn("failed to unmarshal result", zap.Error(err))
				return
			}
			h.Logger.Info("received terminal result",
				zap.String("actionId", result.ActionID),
				zap.String("status", result.Status))
			h.SettlementMgr.ProcessResult(result)
		},
	}, nil)

	if err != nil {
		return err
	}

	h.Logger.Info("subscribed to robot results", zap.String("topic", RobotResultTopic))
	return nil
}

func (h *Handlers) Close() {
	if h.resultSub != nil {
		h.resultSub.Undeclare()
	}
	if h.zenohSession != nil {
		h.zenohSession.Close(nil)
	}
}

func (h *Handlers) PostAction(c *gin.Context) {
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "failed to read request body"})
		return
	}

	if len(body) > 0 && !json.Valid(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body must be valid JSON"})
		return
	}

	var payload map[string]interface{}
	if len(body) > 0 {
		if err := json.Unmarshal(body, &payload); err != nil {
			payload = map[string]interface{}{"raw": string(body)}
		}
	}

	var paymentPayload interface{}
	if value, ok := c.Get("x402_payload"); ok {
		paymentPayload = value
	}

	var paymentRequirements interface{}
	if value, ok := c.Get("x402_requirements"); ok {
		paymentRequirements = value
	}

	transactionDetails := gin.H{
		"protocol":             ProtocolX402,
		"payment_payload":      paymentPayload,
		"payment_requirements": paymentRequirements,
	}
	if credential := mppay.Credential(c); credential != nil {
		transactionDetails["protocol"] = ProtocolMPP
		transactionDetails["mpp_credential"] = credential
		transactionDetails["mpp_receipt"] = mppay.Receipt(c)
	}

	event := gin.H{
		"payload":             payload,
		"transaction_details": transactionDetails,
		"timestamp":           time.Now().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Warn("failed to marshal action event", zap.Error(err))
	} else {
		h.Logger.Info("publishing action event", zap.Any("event", event))
		pub, err := getZenohPublisher()
		if err != nil {
			h.Logger.Warn("failed to initialize zenoh publisher", zap.Error(err))
		} else if err := pub.Publish(RobotActionTopic, eventBytes); err != nil {
			h.Logger.Warn("failed to publish action event", zap.Error(err))
		}
	}

	if actionID != "" {
		h.SettlementMgr.MarkPending(actionID)
	}

	response := gin.H{
		"status":    "accepted",
		"timestamp": time.Now().Format(time.RFC3339),
	}
	if actionID != "" {
		response["actionId"] = actionID
	}

	c.JSON(http.StatusOK, response)
}

func (h *Handlers) correlationID(payload map[string]interface{}, paymentPayload interface{}) string {
	if payload != nil {
		if actionID, ok := payload["actionId"].(string); ok && actionID != "" {
			return actionID
		}
		if inner, ok := payload["payload"].(map[string]interface{}); ok {
			if actionID, ok := inner["actionId"].(string); ok && actionID != "" {
				return actionID
			}
		}

		if txDetails, ok := payload["transaction_details"].(map[string]interface{}); ok {
			if txActionID, ok := txDetails["actionId"].(string); ok && txActionID != "" {
				return txActionID
			}
			if payment, ok := txDetails["payment_payload"].(map[string]interface{}); ok {
				if txHash, ok := payment["txHash"].(string); ok && txHash != "" {
					return txHash
				}
				if internalActionID, ok := payment["actionId"].(string); ok && internalActionID != "" {
					return internalActionID
				}
			}
		}
	}

	if paymentMap, ok := paymentPayload.(map[string]interface{}); ok {
		if txHash, ok := paymentMap["txHash"].(string); ok && txHash != "" {
			return txHash
		}
		if actionID, ok := paymentMap["actionId"].(string); ok && actionID != "" {
			return actionID
		}
	}

	return ""
}

// GetSettlementStatus returns the settlement status for an actionId.
func (h *Handlers) GetSettlementStatus(c *gin.Context) {
	actionID := c.Param("actionId")
	if actionID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "actionId is required"})
		return
	}

	settled := h.SettlementMgr.IsSettled(actionID)
	result, hasResult := h.SettlementMgr.GetResult(actionID)

	response := gin.H{
		"actionId": actionID,
		"settled":  settled,
	}
	if hasResult {
		response["result"] = result
	}

	c.JSON(http.StatusOK, response)
}
