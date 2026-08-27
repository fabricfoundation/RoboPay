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

	attached       bool
	signedRequired string
	signedWWWAuth  string
}

func (w *attestWriter) WriteHeader(code int) {
	w.ResponseWriter.WriteHeader(code)
	w.attach()
}

// WriteHeaderNow is where gin actually emits the status line and headers.
func (w *attestWriter) WriteHeaderNow() {
	w.attach()
	w.ResponseWriter.WriteHeaderNow()
}

func (w *attestWriter) Write(b []byte) (int, error) {
	w.attach()
	return w.ResponseWriter.Write(b)
}

func (w *attestWriter) WriteString(s string) (int, error) {
	w.attach()
	return w.ResponseWriter.WriteString(s)
}

// attach signs the challenge as it currently stands, replacing any attestation
// that covered an earlier version of it.
func (w *attestWriter) attach() {
	if w.Status() != http.StatusPaymentRequired || w.ResponseWriter.Written() {
		return
	}

	header := w.Header()

	paymentRequired := header.Get(headerPaymentRequired)
	wwwAuthenticate := header.Get(headerWWWAuthenticate)

	if paymentRequired == "" && wwwAuthenticate == "" {
		if !w.attached {
			w.logger.Warn("402 response carries no payment challenge header; nothing to attest",
				zap.String("robot_id", w.robotID),
				zap.String("path", w.path),
			)
		}
		return
	}

	if w.attached && paymentRequired == w.signedRequired && wwwAuthenticate == w.signedWWWAuth {
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
	w.attached = true
	w.signedRequired = paymentRequired
	w.signedWWWAuth = wwwAuthenticate
}
