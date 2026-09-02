package tool

import (
	"github.com/charmbracelet/log"
)

var DefaultLogger = log.Default()

func InitLogger() {
	DefaultLogger.SetTimeFormat("2006-01-02 15:04:05")
	DefaultLogger.SetReportCaller(true)
}
