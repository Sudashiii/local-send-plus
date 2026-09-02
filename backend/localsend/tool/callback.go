package tool

import (
	"maps"

	"github.com/gin-gonic/gin"
)

func FastReturnError(msg string) gin.H {
	return gin.H{
		"error": msg,
	}
}

func FastReturnSuccess() gin.H {
	return gin.H{
		"status": "ok",
	}
}

func FastReturnSuccessWithData(data any) gin.H {
	return gin.H{
		"data": data,
	}
}

func FastReturnErrorWithData(msg string, data map[string]any) gin.H {
	resp := gin.H{
		"error": msg,
	}
	maps.Copy(resp, data)
	return resp
}
