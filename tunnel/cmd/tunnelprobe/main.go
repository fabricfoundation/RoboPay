// Command tunnelprobe drives the tunnel's real PostAction handler so that the
// bytes it publishes can be observed, and a robot bridge can be exercised
// against them.
//
// Why this exists. A robot bridge is written against whatever the tunnel puts
// on robot/tunnel/action, and getting that shape wrong produces an integration
// that passes its own tests and works with nothing. Reading handlers.go and
// reproducing the shape by hand is better than guessing, but it is still a
// reproduction. This runs the production handler itself, through the
// production Zenoh publisher, so what lands on the topic is the real thing.
//
// What is substituted, and what is not. PostAction, its JSON shaping and its
// Zenoh publisher are the real ones, untouched. What this does not run is the
// x402 middleware in front of them, because verifying a payment needs a
// facilitator and a funded key. Instead it sets the two context values that
// middleware sets on success -- x402_payload and x402_requirements, exactly as
// http/gin/middleware.go does -- which is the state the handler sees for a
// payment that has been verified and not yet settled. Run with -unpaid to
// leave them unset and see what an unverified request would look like if it
// ever reached the handler; in the real tunnel the middleware answers 402 and
// the handler never runs at all.
//
//	go run ./cmd/tunnelprobe -robot x2-sim-001 -skill push_to_target
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"net/http/httptest"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/handlers"
	x402types "github.com/x402-foundation/x402/go/types"
)

func canonicalParamsHash(params map[string]any) string {
	// Matches the bridge: canonical JSON, sorted keys, no incidental space.
	// encoding/json sorts map keys, so this is already canonical.
	blob, _ := json.Marshal(params)
	sum := sha256.Sum256(blob)
	return "sha256:" + hex.EncodeToString(sum[:])
}

func main() {
	robot := flag.String("robot", "x2-sim-001", "robotId the action is addressed to")
	skill := flag.String("skill", "push_to_target", "skillId to request")
	puckX := flag.Float64("puck-x", 0.26, "")
	puckY := flag.Float64("puck-y", 0.17, "")
	goalX := flag.Float64("goal-x", 0.27, "")
	goalY := flag.Float64("goal-y", 0.30, "")
	unpaid := flag.Bool("unpaid", false, "omit the x402 context values")
	flag.Parse()

	logger, _ := zap.NewProduction()
	defer func() { _ = logger.Sync() }()

	params := map[string]any{}
	if *skill == "push_to_target" {
		params = map[string]any{
			"puck_x": *puckX, "puck_y": *puckY,
			"goal_x": *goalX, "goal_y": *goalY,
		}
	}

	body := map[string]any{
		"actionId":       fmt.Sprintf("act_probe_%d", time.Now().UnixNano()%1e12),
		"robotId":        *robot,
		"skillId":        *skill,
		"params":         params,
		"idempotencyKey": fmt.Sprintf("idem-probe-%d", time.Now().UnixNano()%1e10),
		"paramsHash":     canonicalParamsHash(params),
		"expiresAt":      time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339),
	}
	raw, _ := json.Marshal(body)

	requirements := x402types.PaymentRequirements{
		Scheme:            "exact",
		Network:           "eip155:84532",
		Asset:             "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
		Amount:            "2000",
		PayTo:             "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
		MaxTimeoutSeconds: 30,
	}
	payload := x402types.PaymentPayload{
		X402Version: 2,
		Payload: map[string]any{
			"signature": "0x" + hex.EncodeToString(bytes.Repeat([]byte{0xab}, 65)),
			"authorization": map[string]any{
				"from":        "0x1111111111111111111111111111111111111111",
				"to":          requirements.PayTo,
				"value":       requirements.Amount,
				"validAfter":  "0",
				"validBefore": "9999999999",
				"nonce":       "0x" + hex.EncodeToString(bytes.Repeat([]byte{0xcd}, 32)),
			},
		},
		Accepted: requirements,
	}

	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.POST("/action", func(c *gin.Context) {
		if !*unpaid {
			c.Set("x402_payload", payload)
			c.Set("x402_requirements", requirements)
		}
		handlers.NewHandlers(logger).PostAction(c)
	})

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/action", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(rec, req)

	fmt.Printf("handler status : %d\n", rec.Code)
	fmt.Printf("handler body   : %s\n", rec.Body.String())
	fmt.Printf("published to   : %s\n", handlers.RobotActionTopic)
	fmt.Printf("action id      : %s\n", body["actionId"])

	// Zenoh publishes asynchronously; give the session a moment before exit.
	time.Sleep(1500 * time.Millisecond)
	if rec.Code != 200 {
		os.Exit(1)
	}
}
