package types

type FileMetadata struct {
	Modified string `json:"modified,omitempty"`
	Accessed string `json:"accessed,omitempty"`
}

type FileInfo struct {
	ID       string        `json:"id"`
	FileName string        `json:"fileName"`
	Size     int64         `json:"size"`
	FileType string        `json:"fileType"`
	SHA256   string        `json:"sha256,omitempty"`
	Preview  string        `json:"preview,omitempty"`
	Metadata *FileMetadata `json:"metadata,omitempty"`
}

// FileInput represents file input information
type FileInput struct {
	ID       string `json:"id"`                // File ID
	FileName string `json:"fileName"`          // File name (optional if fileUrl is provided)
	Size     int64  `json:"size"`              // File size in bytes (optional if fileUrl is provided)
	FileType string `json:"fileType"`          // File type, e.g., "image/jpeg" (optional if fileUrl is provided)
	SHA256   string `json:"sha256,omitempty"`  // SHA256 hash value (optional)
	Preview  string `json:"preview,omitempty"` // Preview data (optional)
	FileUrl  string `json:"fileUrl,omitempty"` // File URL (supports file:/// protocol, auto-reads file info)
}
