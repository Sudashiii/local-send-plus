package controllers

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/moyoez/localsend-go/api/models"
	"github.com/moyoez/localsend-go/boardcast"
	"github.com/moyoez/localsend-go/notify"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

// browserNameFromUA returns a short browser name from User-Agent for display.
// I dont like Quake, QQ browser or any other browser(They are too bad!!!)
func browserNameFromUA(ua string) string {
	ua = strings.ToLower(ua)
	switch {
	case strings.Contains(ua, "edg/"):
		return "Edge"
	case strings.Contains(ua, "chrome") && !strings.Contains(ua, "chromium"):
		return "Chrome"
	case strings.Contains(ua, "firefox"):
		return "Firefox"
	case strings.Contains(ua, "safari") && !strings.Contains(ua, "chrome"):
		return "Safari"
	case strings.Contains(ua, "opera") || strings.Contains(ua, "opr/"):
		return "Opera"
	default:
		return ""
	}
}

// HandlePrepareDownload handles prepare-download request (LocalSend protocol 5.2)
// POST /api/localsend/v2/prepare-download?sessionId=xxx&pin=xxx
func HandlePrepareDownload(c *gin.Context) {
	sessionId := c.Query("sessionId")
	if sessionId == "" {
		sessionId = c.Query("session") // alternative param from URL
	}
	// session to smaller case
	sessionId = strings.ToLower(sessionId)

	if sessionId == "1145141919810" {
		// test playground for debuging num.
		c.JSON(http.StatusOK, &types.PrepareUploadReverseProxyResp{
			Info: types.DeviceInfoReverseMode{
				Alias:       "Koi-NotAPowerDeck",
				Version:     "2.0.0",
				DeviceModel: "HomeBrewMachineNotMachine",
				DeviceType:  "headless",
			},
			SessionId: sessionId,
			Files: map[string]types.FileInfo{
				"ohhh my god": {
					ID:       "hellbomb ar(strike)med, clean the area.",
					FileName: "do you like whtas' your see",
					Size:     100,
					FileType: "text/plain",
					SHA256:   "good guy",
				},
				"thats": {
					ID:       "sound not good to me.",
					FileName: "wow! lt(strike)t store /com",
					Size:     100,
					FileType: "text/plain",
					SHA256:   "linu(strike) drop tech!",
				},
				"too anime": {
					ID:       "Cial(strike)o~",
					FileName: "0721",
					Size:     0721,
					FileType: "text/plain",
					SHA256:   "081010101",
				},
				"for me": {
					ID:       "huh? ⬆️➡️⬇️⬇️⬇️",
					FileName: "⬆️➡️⬇️⬇️⬇️ For super earth!!!",
					Size:     500,
					FileType: "text/plain",
					SHA256:   "Helldi(strike)ers ready to go!",
				},
			},
		})
		return
	}
	pin := c.Query("pin")

	if sessionId == "" {
		c.JSON(http.StatusForbidden, tool.FastReturnError("Missing sessionId"))
		return
	}

	session, ok := models.GetShareSession(sessionId)
	if !ok {
		tool.DefaultLogger.Infof("[PrepareDownload] Session not found: %s", sessionId)
		c.JSON(http.StatusForbidden, tool.FastReturnError("Session not found or expired"))
		return
	}

	// PIN check
	if session.Pin != "" {
		if pin == "" {
			c.JSON(http.StatusUnauthorized, tool.FastReturnError("PIN required"))
			return
		}
		if pin != session.Pin {
			c.JSON(http.StatusUnauthorized, tool.FastReturnError("Invalid PIN"))
			return
		}
	}

	clientKey := c.ClientIP()
	userAgent := c.GetHeader("User-Agent")
	clientType := browserNameFromUA(userAgent)
	if clientType == "" && userAgent != "" {
		clientType = "Browser"
	} else if clientType == "" {
		clientType = "Unknown"
	}

	if !session.AutoAccept {
		if models.IsDownloadConfirmed(sessionId, clientKey) {
			tool.DefaultLogger.Infof("[PrepareDownload] Session %s already confirmed for client %s, returning file list", sessionId, clientKey)
			// fall through to return 200 + files below
		} else if ch, hasPending := models.GetConfirmDownloadChannel(sessionId, clientKey); hasPending && ch != nil {
			// same client already waiting for confirmation; return 202 so web can keep polling
			tool.DefaultLogger.Infof("[PrepareDownload] Session %s client %s already pending confirmation", sessionId, clientKey)
			c.JSON(http.StatusAccepted, gin.H{
				"status":  "waiting_confirmation",
				"message": "Waiting for sender to authorize this device",
			})
			return
		} else {
			// need confirmation: create channel, send notification, return 202 immediately; goroutine waits for result
			confirmCh := make(chan types.ConfirmResult, 1)
			models.SetConfirmDownloadChannel(sessionId, clientKey, confirmCh)

			files := models.GetShareSessionFiles(session)
			maxFiles := min(len(files), notify.MaxNotifyFiles)
			filesList := make([]types.FileInfo, 0, maxFiles)
			for _, info := range files {
				if len(filesList) >= notify.MaxNotifyFiles {
					break
				}
				filesList = append(filesList, info)
			}

			notification := &types.Notification{
				Type:    types.NotifyTypeConfirmDownload,
				Title:   "Confirm Download",
				Message: "Receiver is requesting to download files. Allow?",
				Data: map[string]any{
					"sessionId":  sessionId,
					"clientKey":  clientKey,
					"clientIp":   clientKey,
					"userAgent":  userAgent,
					"clientType": clientType,
					"fileCount":  len(files),
					"files":      filesList,
				},
			}
			tool.DefaultLogger.Infof("[Notify] Sending confirm_download notification: sessionId=%s, clientKey=%s, fileCount=%d", sessionId, clientKey, len(files))
			tool.DefaultLogger.Debugf("Accept: GET /api/self/v1/confirm-download?sessionId=%s&clientKey=%s&confirmed=true", sessionId, clientKey)
			tool.DefaultLogger.Debugf("Reject: GET /api/self/v1/confirm-download?sessionId=%s&clientKey=%s&confirmed=false", sessionId, clientKey)
			if err := notify.SendNotification(notification, ""); err != nil {
				models.DeleteConfirmDownloadChannel(sessionId, clientKey)
				tool.DefaultLogger.Errorf("[Notify] Failed to send confirm_download notification: %v", err)
				c.JSON(http.StatusInternalServerError, tool.FastReturnError("Failed to request confirmation"))
				return
			}

			go func() {
				confirmTimeout := 30 * time.Second
				confirmTimer := time.NewTimer(confirmTimeout)
				defer confirmTimer.Stop()
				defer models.DeleteConfirmDownloadChannel(sessionId, clientKey)
				select {
				case result := <-confirmCh:
					if result.Confirmed {
						models.MarkDownloadConfirmed(sessionId, clientKey)
						tool.DefaultLogger.Infof("[PrepareDownload] Download confirmed by user: sessionId=%s, clientKey=%s", sessionId, clientKey)
					} else {
						tool.DefaultLogger.Infof("[PrepareDownload] Download rejected by user: sessionId=%s, clientKey=%s", sessionId, clientKey)
					}
				case <-confirmTimer.C:
					tool.DefaultLogger.Infof("[PrepareDownload] Download confirmation timed out: sessionId=%s, clientKey=%s", sessionId, clientKey)
				}
			}()

			c.JSON(http.StatusAccepted, gin.H{
				"status":  "waiting_confirmation",
				"message": "Waiting for sender to authorize this device",
			})
			return
		}
	}

	selfDevice := models.GetSelfDevice()
	if selfDevice == nil {
		c.JSON(http.StatusInternalServerError, tool.FastReturnError("Device info not available"))
		return
	}

	files := models.GetShareSessionFiles(session)
	response := &types.PrepareUploadReverseProxyResp{
		Info: types.DeviceInfoReverseMode{
			Alias:       selfDevice.Alias,
			Version:     selfDevice.Version,
			DeviceModel: selfDevice.DeviceModel,
			DeviceType:  selfDevice.DeviceType,
			Fingerprint: selfDevice.Fingerprint,
			Download:    selfDevice.Download,
		},
		SessionId: sessionId,
		Files:     files,
	}

	tool.DefaultLogger.Infof("[PrepareDownload] Returning file list for session %s, file count: %d", sessionId, len(files))
	c.JSON(http.StatusOK, response)
}

// HandleDownload handles download request (LocalSend protocol 5.3)
// GET /api/localsend/v2/download?sessionId=xxx&fileId=xxx
func HandleDownload(c *gin.Context) {
	sessionId := c.Query("sessionId")
	fileId := c.Query("fileId")

	if sessionId == "" || fileId == "" {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Missing parameters"))
		return
	}

	session, ok := models.GetShareSession(sessionId)
	if !ok {
		tool.DefaultLogger.Infof("[Download] Session not found: %s", sessionId)
		c.JSON(http.StatusForbidden, tool.FastReturnError("Session not found or expired"))
		return
	}

	entry, ok := models.LookupShareFile(session, fileId)
	if !ok {
		c.JSON(http.StatusNotFound, tool.FastReturnError("File not found"))
		return
	}

	// Verify file exists
	info, err := os.Stat(entry.LocalPath)
	if err != nil {
		if os.IsNotExist(err) {
			c.JSON(http.StatusNotFound, tool.FastReturnError("File not found on disk"))
			return
		}
		tool.DefaultLogger.Errorf("[Download] Failed to stat file: %v", err)
		c.JSON(http.StatusInternalServerError, tool.FastReturnError("Failed to read file"))
		return
	}

	if info.IsDir() {
		c.JSON(http.StatusBadRequest, tool.FastReturnError("Invalid file"))
		return
	}

	fileName := entry.FileInfo.FileName
	if fileName == "" {
		fileName = filepath.Base(entry.LocalPath)
	} else {
		fileName = filepath.Base(fileName)
	}

	c.Header("Content-Disposition", "attachment; filename=\""+fileName+"\"")
	if entry.FileInfo.FileType != "" {
		c.Header("Content-Type", entry.FileInfo.FileType)
	} else {
		c.Header("Content-Type", "application/octet-stream")
	}

	tool.DefaultLogger.Infof("[Download] Serving file: sessionId=%s, fileId=%s, path=%s", sessionId, fileId, entry.LocalPath)
	boardcast.PauseScan()
	defer boardcast.ResumeScan()
	c.File(entry.LocalPath)
}
