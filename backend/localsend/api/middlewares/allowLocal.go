package middlewares

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

func OnlyAllowLocal(c *gin.Context) {
	if c.ClientIP() == "127.0.0.1" || c.ClientIP() == "::1" {
		c.Next()
	} else {
		c.JSON(http.StatusForbidden, gin.H{"error": "Forbidden"})
		c.Abort()
		return
	}
}
