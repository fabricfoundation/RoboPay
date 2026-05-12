package handlers

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
)

func GetWeather(c *gin.Context) {
	city := c.DefaultQuery("city", "San Francisco")

	weatherData := map[string]map[string]interface{}{
		"San Francisco": {"weather": "foggy", "temperature": 60},
		"New York":      {"weather": "cloudy", "temperature": 55},
		"London":        {"weather": "rainy", "temperature": 50},
		"Tokyo":         {"weather": "clear", "temperature": 65},
	}

	data, exists := weatherData[city]
	if !exists {
		data = map[string]interface{}{"weather": "sunny", "temperature": 70}
	}

	c.JSON(http.StatusOK, gin.H{
		"city":        city,
		"weather":     data["weather"],
		"temperature": data["temperature"],
		"timestamp":   time.Now().Format(time.RFC3339),
	})
}