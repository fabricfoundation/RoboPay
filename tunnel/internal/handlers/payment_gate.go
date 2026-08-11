package handlers

import (
	"context"
	"fmt"
	"time"

	"github.com/gin-gonic/gin"
	x402http "github.com/x402-foundation/x402/go/http"
	ginmw "github.com/x402-foundation/x402/go/http/gin"
)

// VerifyOnlyServer is satisfied by *x402http.HTTPServer (the value returned
// by x402http.Newx402HTTPResourceServer). Declared as an interface so the
// middleware can be unit-tested without a real facilitator.
type VerifyOnlyServer interface {
	RequiresPayment(reqCtx x402http.HTTPRequestContext) bool
	ProcessHTTPRequest(ctx context.Context, reqCtx x402http.HTTPRequestContext, paywallConfig *x402http.PaywallConfig) x402http.HTTPProcessResult
}

// X402VerifyOnly verifies an x402 payment for protected routes but never
// settles it. This deliberately replaces the stock ginmw.X402Payment
// middleware, which settles synchronously as soon as the wrapped handler
// returns a non-error status -- wrong for this tunnel, since PostAction
// returns 202 immediately, before the simulator has actually run.
// Settlement only happens later, in ExecutionWatcher, once a terminal
// robot/tunnel/result confirms real success.
func X402VerifyOnly(server VerifyOnlyServer, timeout time.Duration) gin.HandlerFunc {
	return func(c *gin.Context) {
		reqCtx := x402http.HTTPRequestContext{
			Adapter: ginmw.NewGinAdapter(c),
			Path:    c.Request.URL.Path,
			Method:  c.Request.Method,
		}

		if !server.RequiresPayment(reqCtx) {
			c.Next()
			return
		}

		ctx, cancel := context.WithTimeout(c.Request.Context(), timeout)
		defer cancel()

		result := server.ProcessHTTPRequest(ctx, reqCtx, nil)

		switch result.Type {
		case x402http.ResultNoPaymentRequired:
			c.Next()

		case x402http.ResultPaymentError:
			resp := result.Response
			for k, v := range resp.Headers {
				c.Header(k, v)
			}
			if resp.IsHTML {
				c.Data(resp.Status, "text/html; charset=utf-8", []byte(fmt.Sprintf("%v", resp.Body)))
			} else {
				c.JSON(resp.Status, resp.Body)
			}
			c.Abort()

		case x402http.ResultPaymentVerified:
			// Payment is verified (signature + funds checked against the
			// facilitator) but NOT yet settled. The raw payload/requirements
			// are made available to the handler via gin context so it can
			// persist them for later settlement.
			if result.PaymentPayload != nil {
				c.Set("x402_payload", *result.PaymentPayload)
			}
			if result.PaymentRequirements != nil {
				c.Set("x402_requirements", *result.PaymentRequirements)
			}
			c.Next()
		}
	}
}
