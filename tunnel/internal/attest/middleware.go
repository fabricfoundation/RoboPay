package attest

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// Challenge header names the attestation covers.
const (
	headerPaymentRequired = "PAYMENT-REQUIRED"
	headerWWWAuthenticate = "WWW-Authenticate"
)

// Middleware attaches an attestation to every 402 the tunnel emits, whichever
// payment protocol produced it.
//
// It must be registered *before* the payment middleware so that the wrapped
// writer is in place when the 402 is written. The attestation is attached at
// WriteHeader time, by which point the challenge headers are set but nothing has
// been flushed to the wire.
//
// A failure to attest is logged and never blocks the response: the payer's own
// policy decides whether to accept an unattested challenge, and failing the
// request here would take the robot offline over a signing problem.
func (s *Signer) Middleware(robotID string, logger *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Writer = &attestWriter{
			ResponseWriter: c.Writer,
			signer:         s,
			robotID:        robotID,
			method:         c.Request.Method,
			path:           c.Request.URL.Path,
			logger:         logger,
		}
		c.Next()
	}
}

// attestWriter attaches the attestation header on the way to a 402.
type attestWriter struct {
	gin.ResponseWriter

	signer  *Signer
	robotID string
	method  string
	path    string
	logger  *zap.Logger
	done    bool
}

func (w *attestWriter) WriteHeader(code int) {
	if code == http.StatusPaymentRequired && !w.done {
		w.done = true
		w.attach()
	}
	w.ResponseWriter.WriteHeader(code)
}

// attach signs whatever challenge the payment middleware has just set.
func (w *attestWriter) attach() {
	header := w.Header()

	// Only the first value of each header is signed: the gateway forwards one
	// value per header name, so signing more would produce an attestation the
	// payer could not reproduce.
	paymentRequired := header.Get(headerPaymentRequired)
	wwwAuthenticate := header.Get(headerWWWAuthenticate)

	if paymentRequired == "" && wwwAuthenticate == "" {
		w.logger.Warn("402 response carries no payment challenge header; nothing to attest",
			zap.String("robot_id", w.robotID),
			zap.String("path", w.path),
		)
		return
	}

	value, err := w.signer.Attest(Challenge{
		RobotID:         w.robotID,
		Method:          w.method,
		Path:            w.path,
		PaymentRequired: paymentRequired,
		WWWAuthenticate: wwwAuthenticate,
	})
	if err != nil {
		w.logger.Error("failed to attest payment requirements",
			zap.String("robot_id", w.robotID),
			zap.Error(err),
		)
		return
	}

	header.Set(HeaderName, value)
}
