package mppay

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/tempoxyz/mpp-go/pkg/mpp"
	mppserver "github.com/tempoxyz/mpp-go/pkg/server"
	ginadapter "github.com/tempoxyz/mpp-go/pkg/server/gin"
	tempocharge "github.com/tempoxyz/mpp-go/pkg/tempo/server"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/config"
)

// tempo-go must stay at v0.5.0 or newer even though mpp-go v0.2.0 only asks for
// v0.4.1: the older deserializer rejects the signature envelope current Python
// and TypeScript payers emit, failing every payment with "failed to deserialize
// transaction payload". See the SDK versions table in the README.

const actionDescription = "Run a paid robot action"

// Gate offers the MPP charge flow on the priced route, falling through to x402
// for every request that is not an MPP one.
type Gate struct {
	mpp    *mppserver.Mpp
	params mppserver.ChargeParams
	charge gin.HandlerFunc
	logger *zap.Logger
}

// New builds the Gate from the tunnel config. It returns (nil, nil) when MPP is
// disabled, so callers can treat "off" and "not configured" the same way.
func New(cfg *config.Config, logger *zap.Logger) (*Gate, error) {
	if !cfg.MPPEnabled {
		return nil, nil
	}

	chainID, ok := cfg.MPPChainID()
	if !ok {
		return nil, fmt.Errorf("mpp: mpp_network %q is not an eip155 CAIP-2 id", cfg.MPPNetwork)
	}

	method, err := tempocharge.MethodFromConfig(tempocharge.Config{
		RPCURL:    cfg.MPPRPCURL,
		ChainID:   chainID,
		Currency:  cfg.MPPCurrency,
		Recipient: cfg.MPPPayeeAddress,
		Decimals:  cfg.MPPDecimals,
	})
	if err != nil {
		return nil, fmt.Errorf("mpp: failed to build tempo charge method: %w", err)
	}

	payment := mppserver.New(method, cfg.MPPRealm, cfg.MPPSecretKey)

	params := mppserver.ChargeParams{
		Amount:      strings.TrimPrefix(cfg.Price, "$"),
		Description: actionDescription,
	}

	currency := cfg.MPPCurrency
	if currency == "" {
		currency = "chain default stablecoin"
	}

	logger.Info("MPP payments enabled",
		zap.String("realm", cfg.MPPRealm),
		zap.String("network", cfg.MPPNetwork),
		zap.Int64("chain_id", chainID),
		zap.String("payee", cfg.MPPPayeeAddress),
		zap.String("currency", currency),
		zap.Int("decimals", cfg.MPPDecimals),
		zap.String("amount", params.Amount),
	)

	return &Gate{
		mpp:    payment,
		params: params,
		charge: ginadapter.ChargeMiddleware(payment, params),
		logger: logger,
	}, nil
}

// Middleware dispatches between MPP and x402 for the priced route.
func (g *Gate) Middleware(x402 gin.HandlerFunc) gin.HandlerFunc {
	return func(c *gin.Context) {
		if !isPricedRoute(c) {
			x402(c)
			return
		}

		credential, err := mpp.FindPaymentAuthorizationStrict(c.GetHeader("Authorization"))
		if err != nil {
			mppserver.WritePaymentError(c.Writer, mpp.ErrBadRequest(err.Error()))
			c.Abort()
			return
		}

		if credential != "" {
			g.charge(c)
			return
		}

		if !hasX402Payment(c) {
			g.advertise(c)
		}
		x402(c)
	}
}

// advertise attaches an MPP challenge to the 402 that x402 is about to write.
func (g *Gate) advertise(c *gin.Context) {
	params := g.params
	params.MppxScope = mppserver.ScopeFromHTTPRequest(c.Request, c.FullPath())

	body, err := mppserver.ReadRequestBody(c.Request)
	if err != nil {
		g.logger.Warn("failed to read body for MPP challenge", zap.Error(err))
		return
	}
	if len(body) > 0 {
		params.Body = body
	}

	result, err := g.mpp.Charge(c.Request.Context(), params)
	if err != nil || result == nil || result.Challenge == nil {
		g.logger.Warn("failed to build MPP challenge", zap.Error(err))
		return
	}

	header, err := result.Challenge.ToAuthenticateStrict(g.mpp.Realm())
	if err != nil {
		g.logger.Warn("failed to format MPP challenge", zap.Error(err))
		return
	}
	c.Writer.Header().Add("WWW-Authenticate", header)
}

// Credential returns the verified MPP credential for the current request, or nil
// when the request was not paid over MPP.
func Credential(c *gin.Context) *mpp.Credential { return ginadapter.Credential(c) }

// Receipt returns the MPP receipt issued for the current request, or nil when
// the request was not paid over MPP.
func Receipt(c *gin.Context) *mpp.Receipt { return ginadapter.Receipt(c) }

// isPricedRoute reports whether the route is the one both protocols price.
func isPricedRoute(c *gin.Context) bool {
	return c.Request.Method == http.MethodPost && c.FullPath() == "/action"
}

// hasX402Payment reports whether the payer already chose x402.
func hasX402Payment(c *gin.Context) bool {
	return c.GetHeader("PAYMENT-SIGNATURE") != ""
}
