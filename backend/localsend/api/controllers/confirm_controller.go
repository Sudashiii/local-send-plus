package controllers

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/moyoez/localsend-go/api/models"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

// UserConfirmRecv handles confirm receive request
// GET /api/self/v1/confirm-recv
func UserConfirmRecv(c *gin.Context) {
	sessionId := strings.TrimSpace(c.Query("sessionId"))
	confirmedRaw := strings.TrimSpace(c.Query("confirmed"))
	if sessionId == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: sessionId"))
		return
	}
	if confirmedRaw == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: confirmed"))
		return
	}

	confirmed, err := strconv.ParseBool(confirmedRaw)
	if err != nil {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Invalid parameter: confirmed"))
		return
	}

	confirmCh, ok := models.GetConfirmRecvChannel(sessionId)
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("Session not found or expired"))
		return
	}

	select {
	case confirmCh <- types.ConfirmResult{Confirmed: confirmed}:
		models.DeleteConfirmRecvChannel(sessionId)
		c.JSON(http.StatusOK, tool.FastReturnSuccess())
	default:
		c.JSON(http.StatusConflict, tool.FastReturnError("Confirm channel busy"))
	}
}

// UserConfirmRecvPost handles the Decky-only confirmation shape that carries
// a destination selected by the receiver.  The public LocalSend protocol is
// unchanged; this endpoint is restricted by the local middleware.
func UserConfirmRecvPost(c *gin.Context) {
	var request struct {
		SessionID       string `json:"sessionId"`
		Confirmed       *bool  `json:"confirmed"`
		DestinationPath string `json:"destinationPath"`
	}
	if err := c.ShouldBindJSON(&request); err != nil || strings.TrimSpace(request.SessionID) == "" || request.Confirmed == nil {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing or invalid confirmation body"))
		return
	}

	confirmed := *request.Confirmed
	destination := ""
	if confirmed {
		var err error
		destination, err = tool.ValidateReceiveFolder(request.DestinationPath)
		if err != nil {
			c.JSON(http.StatusBadRequest, tool.FastReturnError(err.Error()))
			return
		}
	}
	confirmCh, ok := models.GetConfirmRecvChannel(strings.TrimSpace(request.SessionID))
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("Session not found or expired"))
		return
	}
	select {
	case confirmCh <- types.ConfirmResult{Confirmed: confirmed, DestinationPath: destination}:
		models.DeleteConfirmRecvChannel(strings.TrimSpace(request.SessionID))
		c.JSON(http.StatusOK, tool.FastReturnSuccess())
	default:
		c.JSON(http.StatusConflict, tool.FastReturnError("Confirm channel busy"))
	}
}

// UserSetReceiveRoot updates the default destination for future sessions.
// Existing sessions retain the root captured at prepare time.
func UserSetReceiveRoot(c *gin.Context) {
	var request struct {
		Path string `json:"path"`
	}
	if err := c.ShouldBindJSON(&request); err != nil {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Invalid receive root body"))
		return
	}
	path, err := tool.ValidateReceiveFolder(request.Path)
	if err != nil {
		c.JSON(http.StatusBadRequest, tool.FastReturnError(err.Error()))
		return
	}
	models.SetDefaultUploadFolder(path)
	c.JSON(http.StatusOK, tool.FastReturnSuccessWithData(map[string]any{"path": path}))
}

// UserReceiveManifest returns every successful save path while the upload-end
// notification is being acknowledged by the local plugin.
func UserReceiveManifest(c *gin.Context) {
	sessionID := strings.TrimSpace(c.Query("sessionId"))
	if sessionID == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: sessionId"))
		return
	}
	options, savePaths, ok := models.GetSessionReceiveManifest(sessionID)
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("Session not found or expired"))
		return
	}
	c.JSON(http.StatusOK, tool.FastReturnSuccessWithData(map[string]any{
		"root":            options.ReceiveRoot,
		"destinationPath": options.DestinationRoot,
		"destinationId":   options.DestinationID,
		"destinationName": options.DestinationName,
		"flat":            options.Flat,
		"layout":          map[bool]string{true: "flat", false: "session"}[options.Flat],
		"savePaths":       savePaths,
	}))
}

// UserTextReceivedDismiss handles text-received modal dismiss (user closed or copied).
// GET /api/self/v1/text-received-dismiss
func UserTextReceivedDismiss(c *gin.Context) {
	sessionId := strings.TrimSpace(c.Query("sessionId"))
	if sessionId == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: sessionId"))
		return
	}
	dismissCh, ok := models.GetTextReceivedDismissChannel(sessionId)
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("Session not found or expired"))
		return
	}
	select {
	case dismissCh <- struct{}{}:
		models.DeleteTextReceivedDismissChannel(sessionId)
		c.JSON(http.StatusOK, tool.FastReturnSuccess())
	default:
		c.JSON(http.StatusConflict, tool.FastReturnError("Dismiss channel busy"))
	}
}

// UserConfirmDownload handles confirm download request
// GET /api/self/v1/confirm-download?sessionId=xxx&clientKey=yyy&confirmed=true
func UserConfirmDownload(c *gin.Context) {
	sessionId := strings.TrimSpace(c.Query("sessionId"))
	clientKey := strings.TrimSpace(c.Query("clientKey"))
	confirmedRaw := strings.TrimSpace(c.Query("confirmed"))
	if sessionId == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: sessionId"))
		return
	}
	if clientKey == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: clientKey"))
		return
	}
	if confirmedRaw == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing required parameter: confirmed"))
		return
	}

	confirmed, err := strconv.ParseBool(confirmedRaw)
	if err != nil {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Invalid parameter: confirmed"))
		return
	}

	confirmCh, ok := models.GetConfirmDownloadChannel(sessionId, clientKey)
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("Session not found or expired"))
		return
	}

	select {
	case confirmCh <- types.ConfirmResult{Confirmed: confirmed}:
		models.DeleteConfirmDownloadChannel(sessionId, clientKey)
		// goroutine in download_controller will call MarkDownloadConfirmed on accept
		c.JSON(http.StatusOK, tool.FastReturnSuccess())
	default:
		c.JSON(http.StatusConflict, tool.FastReturnError("Confirm channel busy"))
	}
}
