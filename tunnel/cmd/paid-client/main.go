// paid-client performs a real x402 EIP-3009 request for reviewer evidence.
// It intentionally accepts the payer private key only through X402_PRIVATE_KEY.
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	exactclient "github.com/x402-foundation/x402/go/mechanisms/evm/exact/client"
	evmsigner "github.com/x402-foundation/x402/go/signers/evm"
)

func main() {
	url := flag.String("url", "http://127.0.0.1:3000/action", "protected RoboPay action URL")
	expectedPayer := flag.String("payer", "", "expected payer address (recommended safety check)")
	action := flag.String("action", "move_forward", "simulator action")
	duration := flag.Float64("duration", 1.0, "action duration in seconds")
	robotID := flag.String("robot-id", "agibot-x2-sim-001", "target robot identifier")
	idempotencyKey := flag.String("idempotency-key", "", "fixed key for an intentional replay test")
	flag.Parse()

	privateKey := strings.TrimSpace(os.Getenv("X402_PRIVATE_KEY"))
	if privateKey == "" {
		fatalf("X402_PRIVATE_KEY is not set; enter it privately in your shell, never in source or chat")
	}

	signer, err := evmsigner.NewClientSignerFromPrivateKey(privateKey)
	if err != nil {
		fatalf("invalid X402_PRIVATE_KEY: %v", err)
	}
	if *expectedPayer != "" && !strings.EqualFold(signer.Address(), *expectedPayer) {
		fatalf("private key derives %s, expected %s; refusing to sign", signer.Address(), *expectedPayer)
	}

	client := x402.Newx402Client().Register(
		x402.Network("eip155:*"),
		exactclient.NewExactEvmScheme(signer, nil),
	)
	paidHTTP := x402http.WrapHTTPClientWithPayment(
		&http.Client{Timeout: 45 * time.Second},
		x402http.Newx402HTTPClient(client),
	)

	requestID := fmt.Sprintf("x2-paid-%d", time.Now().UnixMilli())
	replayKey := *idempotencyKey
	if replayKey == "" {
		replayKey = requestID
	}
	body, err := json.Marshal(map[string]any{
		"actionId":       requestID,
		"idempotencyKey": replayKey,
		"robotId":        *robotID,
		"action":         *action,
		"params":         map[string]any{"duration": *duration},
	})
	if err != nil {
		fatalf("encode request: %v", err)
	}

	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, *url, bytes.NewReader(body))
	if err != nil {
		fatalf("create request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := paidHTTP.Do(req)
	if err != nil {
		fatalf("paid request: %v", err)
	}
	defer resp.Body.Close()
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		fatalf("read response: %v", err)
	}

	paymentResponse := resp.Header.Get("PAYMENT-RESPONSE")
	fmt.Printf("payer=%s\nstatus=%d\npayment-response=%s\n",
		signer.Address(), resp.StatusCode, paymentResponse)
	if decoded, decodeErr := base64.StdEncoding.DecodeString(paymentResponse); paymentResponse != "" && decodeErr == nil {
		fmt.Printf("settlement=%s\n", decoded)
	}
	fmt.Printf("body=%s\n", responseBody)
	if resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "error: "+format+"\n", args...)
	os.Exit(1)
}
