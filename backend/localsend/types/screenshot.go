package types

// ScreenshotItem represents a single Steam screenshot for API response.
type ScreenshotItem struct {
	Path     string `json:"path"`
	Filename string `json:"filename"`
	Size     int64  `json:"size"`
	Mtime    int64  `json:"mtime"`
	MtimeStr string `json:"mtime_str"`
}

// ScreenshotListResponse is the response body for GET /api/self/v1/get-user-screenshot.
type ScreenshotListResponse struct {
	Screenshots []ScreenshotItem `json:"screenshots"`
	Count       int              `json:"count"`
	Total       int              `json:"total"`
}
